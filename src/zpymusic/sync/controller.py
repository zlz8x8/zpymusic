"""播放控制器：把播放器、时间轴、高亮、曲谱视图串起来（需求 §5.3）。

职责边界：

* **不碰 Qt Widget**（只发信号），因此 `tab_play` 的界面代码保持简单，控制器可单测。
* 维护 30 fps 的 tick 循环，把 `player.position()` 换算成乐谱时间，
  再经 :class:`~zpymusic.sync.timeline.HighlightTracker` 得到当前发声的元素
  （状态栏 / 校准用），最后交给 :class:`~zpymusic.sync.score_view.ScoreView`。

**高亮策略（M7 起）**：视觉高亮**跟随当前小节**，而不是跟随每个音符 ——
每帧只判断"小节是否变了"，变了才重画一个覆盖整个小节的矩形
（宽度 = 小节宽度、高度 ≈ 谱表高度，几何取自 SVG 的 ``<g class="measure">``）。
这既符合"看谱跟读"的阅读习惯，也把 30 fps 下的重绘次数从"每音符一次"
降到"每小节一次"。套件没有小节数据或小节定位失败时自动退回逐音符高亮。

**变速的正确处理**（F2.5）：播放速率变化时不能简单地用 ``position × rate``，
否则谱面时间会跳变。这里用"锚点"换算::

    乐谱时间 = anchor_score_ms + (position_ms - anchor_pos_ms) × rate

锚点在每次变速 / seek / 起播时重设，保证连续。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..common.log import get_logger
from ..core.suite import Suite
from .highlight import HighlightPlan, HighlightRect
from .player import MAX_RATE, MIN_RATE, PlaybackBackend, PlayerState
from .score_view import ScoreView, SystemRef
from .svg_geometry import Box
from .timeline import HighlightTracker, Timeline

log = get_logger(__name__)

__all__ = ["PlaybackController", "TICK_MS", "RATE_STEPS"]

#: tick 间隔（约 30 fps，满足需求 §7.1 的高亮刷新率）
TICK_MS = 33
#: 速率档位（F2.5：50%–200%）
RATE_STEPS = (0.5, 0.6, 0.75, 0.85, 1.0, 1.15, 1.25, 1.5, 1.75, 2.0)


@dataclass
class PlaybackStatus:
    """一帧的状态快照（供状态栏显示，F2.9）。"""

    score_ms: int = 0
    duration_ms: int = 0
    measure_id: str = ""
    active_ids: tuple[str, ...] = ()
    rate: float = 1.0
    effective_bpm: float | None = None
    state: str = PlayerState.STOPPED.value


class PlaybackController(QObject):
    """播放编排器。"""

    status = Signal(object)  # PlaybackStatus
    highlight_changed = Signal(object)  # HighlightPlan
    error = Signal(str)
    finished = Signal()

    def __init__(
        self,
        player: PlaybackBackend,
        view: ScoreView,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.player = player
        self.view = view
        self.suite: Suite | None = None
        self.timeline: Timeline | None = None
        self.tracker: HighlightTracker | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

        # 变速锚点
        self._anchor_pos_ms = 0.0
        self._anchor_score_ms = 0.0
        self._rate = 1.0
        self._duration_ms = 0
        self._initial_bpm: float | None = None
        self._last_status = PlaybackStatus()
        self._plan = HighlightPlan()
        self._highlighted_measure = ""
        """当前已高亮的小节 ID（空串 = 尚未高亮 / 退回逐音符模式）。"""

        self.player.error.connect(self.error)
        self.player.state_changed.connect(self._on_state_changed)

    # ------------------------------------------------------------------ 载入
    def load_suite(self, suite: Suite) -> bool:
        """载入套件：读 sync.json、构建时间轴、把 system 交给视图。

        Returns:
            True 表示可以播放（音频与同步数据都就绪）。
        """
        self.stop()
        self.suite = suite
        self.timeline = None
        self.tracker = None
        self._plan = HighlightPlan()
        self._highlighted_measure = ""

        audio = suite.audio_files
        if not audio:
            self.error.emit(f"套件 {suite.base} 中没有可播放的音频（.mp3/.mid/.wav）")
            return False

        try:
            sync = suite.load_sync()
        except Exception as e:  # noqa: BLE001 - 缺 sync.json 时仍允许播放但要提示
            self.error.emit(f"{e}；将只播放音频、不做同步高亮")
            self.player.load(audio[0])
            self._duration_ms = 0
            return True

        self.timeline = Timeline.from_sync(sync)
        self.tracker = HighlightTracker(self.timeline, offset_ms=float(sync.audio.get("offset_ms", 0.0)))
        self._duration_ms = sync.duration_ms
        bpm = sync.tempo.get("initial_bpm")
        self._initial_bpm = float(bpm) if bpm else None

        systems = [
            SystemRef(
                index=s.index,
                file=s.file,
                width=s.width,
                height=s.height,
                note_ids=list(s.note_ids),
                measure_ids=self.timeline.measures_of(s.note_ids),
            )
            for s in sync.systems
        ]
        self.view.load(suite.dir, systems)
        self.view.set_highlight_style(
            self._style[0] if hasattr(self, "_style") else "#FF8C00",
            self._style[1] if hasattr(self, "_style") else 0.35,
        )

        self.player.load(audio[0])
        self._reset_anchors(0.0)
        self._emit_status(force=True)
        log.info(
            "已载入套件 %s：%d 个事件 / %d 个 system / %.1f s / 音频 %s",
            suite.base,
            len(self.timeline.entries),
            len(systems),
            self._duration_ms / 1000,
            audio[0].name,
        )
        return True

    # ------------------------------------------------------------------ 传输控制
    @property
    def state(self) -> PlayerState:
        return self.player.state()

    @property
    def rate(self) -> float:
        return self._rate

    def play(self) -> None:
        if self.timeline is None and self.suite is None:
            self.error.emit("尚未载入套件")
            return
        if self.at_end():
            self.seek_score(0)
        self._reset_anchors(self._score_time())
        self.player.play()
        self._timer.start()

    def pause(self) -> None:
        self.player.pause()
        self._timer.stop()
        self._emit_status(force=True)

    def toggle(self) -> None:
        if self.state == PlayerState.PLAYING:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self._timer.stop()
        self.player.stop()
        self._reset_anchors(0.0)
        if self.tracker is not None:
            self.tracker.reset()
        self._clear_highlight()
        self._emit_status(force=True)

    def set_rate(self, rate: float) -> float:
        """设置播放速率（0.5–2.0）。返回实际生效值。"""
        clamped = max(MIN_RATE, min(MAX_RATE, float(rate)))
        # 变速前先把当前乐谱时间锚定下来，保证不跳变
        current_score = self._score_time()
        actual = self.player.set_rate(clamped)
        self._rate = actual
        if self.tracker is not None:
            self.tracker.rate = actual
        self._reset_anchors(current_score)
        self._emit_status(force=True)
        return actual

    def step_rate(self, direction: int) -> float:
        """按档位调整速率（``<`` / ``>`` 快捷键）。"""
        steps = list(RATE_STEPS)
        # 找到当前速率最近的档位
        idx = min(range(len(steps)), key=lambda i: abs(steps[i] - self._rate))
        idx = max(0, min(len(steps) - 1, idx + direction))
        if abs(steps[idx] - self._rate) < 1e-6 and direction:
            idx = max(0, min(len(steps) - 1, idx + (1 if direction > 0 else -1)))
        return self.set_rate(steps[idx])

    def reset_rate(self) -> float:
        return self.set_rate(1.0)

    # ------------------------------------------------------------------ 定位
    def seek_score(self, score_ms: float, *, force: bool = True) -> None:
        """按**乐谱时间**定位（进度条拖动、点击谱面、跳小节都用它）。"""
        score_ms = max(0.0, min(float(score_ms), float(self._duration_ms or score_ms)))
        player_ms = (score_ms - self._offset_ms()) / (self._rate or 1.0)
        self.player.seek(int(max(0.0, player_ms)))
        self._reset_anchors(score_ms)
        if self.tracker is not None:
            self.tracker.reset()
        self._apply_highlight(score_ms, force=True)
        self._emit_status(force=True)

    def seek_fraction(self, fraction: float) -> None:
        """按比例定位（进度条 0–1）。"""
        self.seek_score(float(fraction) * float(self._duration_ms or 0))

    def nudge(self, delta_ms: float) -> None:
        self.seek_score(self._score_time() + float(delta_ms))

    def set_offset(self, offset_ms: float) -> None:
        """F2.8：全局校准偏移（补偿音频设备缓冲延迟）。"""
        if self.tracker is not None:
            self.tracker.offset_ms = float(offset_ms)
            self._reset_anchors(self._score_time())
            self._apply_highlight(self._score_time(), force=True)
            self._emit_status(force=True)

    @property
    def offset_ms(self) -> float:
        return self.tracker.offset_ms if self.tracker is not None else 0.0

    def calibrate_to_active(self) -> float:
        """把当前高亮的音符当作基准对齐（F2.8 的"以当前音符为基准"按钮）。

        做法：取播放器当前位置对应的乐谱时间，与当前高亮音符的真实起点之差，
        累加到 ``offset_ms`` 上。
        """
        if self.timeline is None or self.tracker is None:
            return 0.0
        active = self.tracker.active
        if not active:
            self.error.emit("当前没有高亮的音符，无法校准")
            return self.offset_ms
        target = self.timeline.onset_of(active[0])
        if target is None:
            return self.offset_ms
        # 让"当前播放位置"正好落在该音符起点上
        pos_ms = float(self.player.position())
        desired_offset = float(target) - pos_ms * self._rate
        self.set_offset(desired_offset)
        log.info("已按当前音符校准 offset → %.0f ms", desired_offset)
        return desired_offset

    def preview_at(self, score_ms: float) -> None:
        """拖动进度条时的**预览**：只刷新高亮，不改变播放位置（F2.4）。"""
        if self.timeline is None or self.tracker is None:
            return
        self.tracker.reset()
        self._apply_highlight(float(score_ms), force=True)
        self._emit_status(force=True)

    def jump_to_element(self, element_id: str) -> bool:
        """点击谱面/元素 → 跳到该音符（F2.4）。"""
        if self.timeline is None:
            return False
        onset = self.timeline.onset_of(element_id)
        if onset is None:
            return False
        self.seek_score(onset)
        self.view.scroll_to_element(element_id)
        return True

    def jump_to_measure(self, measure_id: str) -> bool:
        if self.timeline is None or not measure_id:
            return False
        for onset, mid in self.timeline.measures:
            if mid == measure_id:
                self.seek_score(onset)
                return True
        return False

    # ------------------------------------------------------------------ 状态
    @property
    def duration_ms(self) -> int:
        return self._duration_ms or self.player.duration()

    def at_end(self) -> bool:
        dur = self.duration_ms
        return bool(dur) and self.player.position() >= dur - 50

    def score_time(self) -> int:
        return int(self._score_time())

    def snapshot(self) -> PlaybackStatus:
        """最近一帧的状态快照（注意：:attr:`status` 是信号名，故此处不叫 status）。"""
        return self._last_status

    # ------------------------------------------------------------------ tick
    def _tick(self) -> None:
        if self.state != PlayerState.PLAYING:
            return
        score_ms = self._score_time()
        self._apply_highlight(score_ms)
        if self.at_end():
            self._timer.stop()
            self.player.pause()
            self.finished.emit()
        self._emit_status()

    def _apply_highlight(self, score_ms: float, *, force: bool = False) -> None:
        """按当前乐谱时间刷新高亮。

        **M7 起的主策略：跟随当前小节。** 每帧只做一次"小节是否变了"的判断，
        变了才重画一个覆盖整个小节的矩形（宽度 = 小节宽度、高度 ≈ 谱表高度）。
        音符级的时间轴（``HighlightTracker``）仍然每帧更新 —— 状态栏的
        "当前音符"与"按当前音符校准"（F2.8）需要它，只是不再驱动视觉高亮。

        套件没有小节数据（旧 ``sync.json``）或小节定位失败时，
        自动退回原来的"逐音符高亮"。
        """
        if self.timeline is None or self.tracker is None or self.view is None:
            return
        # 音符级追踪照常推进（状态栏 / 校准用）
        diff = self.tracker.update_at(score_ms, force=force)

        measure_id = self.timeline.measure_at(score_ms)
        systems = self._measure_systems(measure_id) if measure_id else []
        if systems:
            # 小节变了才重画 —— 30 fps 下绝大多数帧在这里直接返回
            if not force and measure_id == self._highlighted_measure and self._plan.rects:
                return
            plan = self._plan_for_measure(systems, measure_id, list(diff.current))
            self._highlighted_measure = measure_id
        else:
            if measure_id and measure_id != self._highlighted_measure:
                log.debug("小节 %s 找不到所属 system，退回逐音符高亮", measure_id)
            if not diff.changed and not force:
                return
            ids = list(diff.current)
            if not ids:
                self._clear_highlight()
                return
            system_of: dict[str, int] = {}
            self.view.build_system_map(system_of)
            plan = self._plan_for(system_of, ids)
            self._highlighted_measure = ""

        self._plan = plan
        applied = self.view.set_highlight(plan)
        if applied is not None:
            # 视图可能补全了"跟随小节"的矩形（目标行按需挂载后才算得出），
            # 以它返回的计划为准 —— 控制器保存的就是真正画出来的东西。
            self._plan = applied
        self.highlight_changed.emit(self._plan)

    def _measure_systems(self, measure_id: str) -> list[int]:
        """小节 → system 序号（视图按 ``sync.json`` 回答，无需解析 SVG）。"""
        fn = getattr(self.view, "measure_systems", None)
        return list(fn(measure_id)) if callable(fn) else []

    def _plan_for_measure(
        self, systems: list[int], measure_id: str, ids: list[str]
    ) -> HighlightPlan:
        """生成"高亮整个小节"的计划。

        原生后端直接用几何索引里的小节矩形；WebEngine 后端只带 ID，
        矩形由页面内的 DOM（``getBBox``）负责 —— 与 :meth:`_plan_for` 同样的分工。
        """
        index = getattr(self.view, "geometry_index", None)
        if index is not None:
            return index.measure_plan(systems, measure_id, ids=ids)
        rects = tuple(
            HighlightRect(system, measure_id, Box(0.0, 0.0, 0.0, 0.0)) for system in systems
        )
        return HighlightPlan(rects=rects, ids=tuple(ids), measure_id=measure_id)

    def _plan_for(self, system_of: dict[str, int], ids: list[str]) -> HighlightPlan:
        """生成高亮计划：原生后端用几何矩形，WebEngine 后端用 ID 列表。"""
        index = getattr(self.view, "geometry_index", None)
        if index is not None:
            return index.plan(system_of, ids, pad=1.5)
        # WebEngine：矩形没有意义（高亮靠 DOM class），用零矩形占位并保留 ID 顺序
        rects = tuple(
            HighlightRect(system_of.get(eid, 0), eid, Box(0.0, 0.0, 0.0, 0.0)) for eid in ids
        )
        return HighlightPlan(rects=rects, ids=tuple(ids))

    def _clear_highlight(self) -> None:
        if self.view is not None:
            self.view.clear_highlight()
        self._plan = HighlightPlan()
        self._highlighted_measure = ""

    # ------------------------------------------------------------------ 内部
    def _offset_ms(self) -> float:
        return self.tracker.offset_ms if self.tracker is not None else 0.0

    def _score_time(self) -> float:
        """播放器位置 → 乐谱时间（用锚点做变速换算）。"""
        pos = float(self.player.position())
        return self._anchor_score_ms + (pos - self._anchor_pos_ms) * (self._rate or 1.0)

    def _reset_anchors(self, score_ms: float) -> None:
        self._anchor_pos_ms = float(self.player.position())
        self._anchor_score_ms = float(score_ms)
        if self.tracker is not None:
            self.tracker.rate = self._rate

    def _on_state_changed(self, state: str) -> None:
        if state != PlayerState.PLAYING.value and self._timer.isActive():
            self._timer.stop()
        self._emit_status(force=True)

    def _emit_status(self, *, force: bool = False) -> None:
        score_ms = self._score_time()
        st = PlaybackStatus(
            score_ms=int(max(0.0, score_ms)),
            duration_ms=int(self.duration_ms),
            measure_id=self.timeline.measure_at(score_ms) if self.timeline else "",
            active_ids=self.tracker.active if self.tracker else (),
            rate=self._rate,
            effective_bpm=(
                self._initial_bpm * self._rate if self._initial_bpm else None
            ),
            state=self.state.value,
        )
        if not force and st == self._last_status:
            return
        self._last_status = st
        self.status.emit(st)

    # ------------------------------------------------------------------ 样式
    _style: tuple[str, float] = ("#FF8C00", 0.35)

    def set_highlight_style(self, color: str, opacity: float) -> None:
        """设置高亮颜色与透明度（F2.6）。"""
        self._style = (color, float(opacity))
        if self.view is not None:
            self.view.set_highlight_style(color, float(opacity))
        if self._plan.rects:
            self._apply_highlight(self._score_time(), force=True)

    def set_follow(self, follow: bool) -> None:
        if self.view is not None:
            self.view.set_follow(bool(follow))
