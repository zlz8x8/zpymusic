"""播放时间轴（需求 §5.3 的同步算法）。

纯逻辑、无 Qt / 无 IO，便于单测与在 M4 播放界面复用。

核心 API：

* :class:`Timeline` —— 由 ``sync.json`` 的 ``notes`` 构建，提供
  * :meth:`Timeline.active_at` ：二分查找某时刻正在发声的元素 ID；
  * :meth:`Timeline.diff` ：与上一帧的高亮集合做增量 diff（只增删 class，不整树重绘）；
  * :meth:`Timeline.measure_at` ：当前小节；
  * :meth:`Timeline.measures_of` ：元素 → 小节（供"高亮当前小节"定位所在行）。
* :class:`HighlightTracker` —— 把"当前时间"变成"要增删的 DOM id 集合"，
  并处理 ``offset_ms`` 校准与播放速率。
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..core.sync_model import SyncDoc

__all__ = ["Timeline", "HighlightTracker", "FrameDiff"]

_REND_SUFFIX_RE = re.compile(r"-rend\d*$")


@dataclass(frozen=True)
class FrameDiff:
    """一帧的高亮变化。"""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    current: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


class Timeline:
    """按 ``onset_ms`` 索引的音符时间轴。"""

    def __init__(
        self,
        notes: Iterable[dict],
        *,
        measures: Sequence[dict] = (),
        duration_ms: int = 0,
        include_rests: bool = False,
    ) -> None:
        self.entries: list[tuple[int, int, str]] = []
        self.duration_ms = int(duration_ms)
        self.include_rests = include_rests
        # 保留每个 id 最早一次出现的原始记录，供"点击跳转"等反向查询
        self.by_id: dict[str, tuple[int, int]] = {}
        #: ``{元素ID: 小节ID}`` —— 供"高亮当前小节"把小节映射到所在的 system
        #: （小节容器 ID 在 SVG 里与 ``sync.json.measures[].id`` 一致）。
        self.measure_of: dict[str, str] = {}

        for d in notes:
            nid = _visual_id(str(d["id"]))
            mid = str(d.get("measure") or d.get("m") or "")
            if mid:
                self.measure_of[nid] = mid
                self.measure_of.setdefault(str(d["id"]), mid)
            if d.get("is_rest") and not include_rests:
                continue
            onset = int(d.get("onset_ms", d.get("s", 0)))
            dur = int(d.get("dur_ms", d.get("d", 0)))
            if dur <= 0:
                dur = 1  # 装饰音等零时长事件：至少占 1 ms，避免永不命中
            self.entries.append((onset, dur, nid))
            prev = self.by_id.get(nid)
            if prev is None or onset < prev[0]:
                self.by_id[nid] = (onset, dur)

        self.entries.sort(key=lambda t: (t[0], t[2]))
        self._onsets: list[int] = [e[0] for e in self.entries]
        self._max_dur = max((e[1] for e in self.entries), default=0)
        if not self.duration_ms:
            self.duration_ms = max((o + d for o, d, _ in self.entries), default=0)

        self.measures: list[tuple[int, str]] = sorted(
            (int(m.get("onset_ms", 0)), str(m.get("id", ""))) for m in measures
        )
        self._measure_onsets = [m[0] for m in self.measures]

    # ------------------------------------------------------------------ 查询
    @classmethod
    def from_sync(cls, sync: SyncDoc, *, include_rests: bool = False) -> "Timeline":
        return cls(
            sync.notes,
            measures=[m.to_dict() for m in sync.measures],
            duration_ms=sync.duration_ms,
            include_rests=include_rests,
        )

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def note_count(self) -> int:
        """不同元素的数量（去掉展开产生的重复）。"""
        return len(self.by_id)

    def active_at(self, ms: float | int) -> tuple[str, ...]:
        """返回 ``ms`` 时刻正在发声的（用于 SVG 定位的）元素 ID。

        实现：以 ``ms - max_dur`` 为下界二分定位，再向后扫描。
        时间复杂度 O(log n + k)，k 为窗口内的条目数。
        """
        t = int(ms)
        if t < 0 or not self.entries:
            return ()
        lo = bisect.bisect_left(self._onsets, t - self._max_dur)
        out: list[str] = []
        for i in range(lo, len(self.entries)):
            onset, dur, nid = self.entries[i]
            if onset > t:
                break
            if onset <= t < onset + dur and nid not in out:
                out.append(nid)
        return tuple(out)

    def measure_at(self, ms: float | int) -> str:
        """返回 ``ms`` 时刻所在的小节 ID（无数据时返回空串）。"""
        if not self.measures:
            return ""
        idx = bisect.bisect_right(self._measure_onsets, int(ms)) - 1
        return self.measures[max(0, idx)][1]

    def onset_of(self, element_id: str) -> int | None:
        """某元素的起始时间（用于"点击谱面 → 跳到该音符"）。"""
        hit = self.by_id.get(_visual_id(element_id)) or self.by_id.get(element_id)
        return hit[0] if hit else None

    def measures_of(self, element_ids: Iterable[str]) -> list[str]:
        """给一串元素 ID（如某个 system 的 ``note_ids``），返回其小节 ID。

        按首次出现顺序去重 —— 这就是"该行里依次出现的小节"，
        播放界面据此把当前小节映射到它所在的 system。
        """
        out: list[str] = []
        seen: set[str] = set()
        for eid in element_ids:
            mid = self.measure_of.get(eid) or self.measure_of.get(_visual_id(eid), "")
            if mid and mid not in seen:
                seen.add(mid)
                out.append(mid)
        return out

    def index_at(self, ms: float | int) -> int:
        """返回当前时间对应的条目索引（-1 表示在第一个音符之前）。"""
        return bisect.bisect_right(self._onsets, int(ms)) - 1


@dataclass
class HighlightTracker:
    """把播放位置换算成"要增删的高亮 ID"。

    需求 §5.3 的公式::

        查询时间 = player.position() * rate + offset_ms

    Attributes:
        timeline: 时间轴。
        offset_ms: 全局校准偏移（音频设备缓冲补偿，F2.8）。
        rate: 播放速率（0.5–2.0，F2.5）。
    """

    timeline: Timeline
    offset_ms: float = 0.0
    rate: float = 1.0
    _active: tuple[str, ...] = field(default=(), init=False)
    _cursor_id: str = field(default="", init=False)
    _last_ms: float = field(default=-1.0, init=False)

    # ------------------------------------------------------------------ 主循环
    def update(self, player_ms: float) -> FrameDiff:
        """处理一帧，返回需要应用的增量变化。"""
        return self.update_at(self.query_time(player_ms))

    def update_at(self, query_ms: float, *, force: bool = False) -> FrameDiff:
        """直接以"乐谱时间"更新（拖动 seek 时可绕过速率换算）。"""
        if not force and abs(query_ms - self._last_ms) < 0.5:
            return FrameDiff(current=self._active)
        self._last_ms = query_ms
        new = self.timeline.active_at(query_ms)
        if new == self._active:
            return FrameDiff(current=self._active)
        added = tuple(i for i in new if i not in self._active)
        removed = tuple(i for i in self._active if i not in new)
        self._active = new
        if added:
            self._cursor_id = added[0]
        return FrameDiff(added=added, removed=removed, current=new)

    def reset(self) -> None:
        """停止 / 换曲时清空状态。"""
        self._active = ()
        self._cursor_id = ""
        self._last_ms = -1.0

    # ------------------------------------------------------------------ 换算
    def query_time(self, player_ms: float) -> float:
        """把播放器时间换算为乐谱时间（含速率与校准偏移）。"""
        return player_ms * self.rate + self.offset_ms

    def player_time(self, score_ms: float) -> float:
        """乐谱时间 → 播放器时间（点击谱面跳转时用）。"""
        if self.rate <= 0:
            return score_ms
        return (score_ms - self.offset_ms) / self.rate

    @property
    def active(self) -> tuple[str, ...]:
        """当前高亮的元素 ID。"""
        return self._active

    @property
    def cursor_id(self) -> str:
        """当前用于滚动跟随的元素 ID。"""
        return self._cursor_id

    @property
    def duration_ms(self) -> int:
        return self.timeline.duration_ms

    def effective_bpm(self, initial_bpm: float | None) -> float | None:
        """把播放速率换算成等效 BPM（界面上只读显示，F2.9）。"""
        if not initial_bpm or initial_bpm <= 0 or self.rate <= 0:
            return None
        return initial_bpm * self.rate


def _visual_id(element_id: str) -> str:
    """把 Verovio 展开产生的 ``x-rend2`` 回退为可在 SVG 中找到的 ``x``。"""
    return _REND_SUFFIX_RE.sub("", element_id)
