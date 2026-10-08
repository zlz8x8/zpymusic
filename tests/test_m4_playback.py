"""M4 两个"看着像没实现、其实是状态判断错了"的 bug 的回归测试。

**Bug 1（谱线不可见）**：Verovio 的谱线是只带 ``stroke-width`` 的 ``<path>``，
颜色来自内嵌 CSS ``#id path {stroke:currentColor}``，而**当年的** QtSvg 不套用该规则。
不内联 stroke 时，符头（实心字形）可见、**五线谱的线全部消失**。
→ 测试 :func:`inline_stroke_attributes` 与 ``flatten_svg``。

.. note::
   **Qt 6.12.0 起该上游限制已修复** —— QtSvg 开始套用内嵌 CSS 并解析 ``currentColor``，
   于是"不内联就看不见"这个前提不再成立（``inline_stroke`` 变成冗余但无害的兼容保险）。
   :meth:`TestStaffLinesRender.test_without_inline_stroke_lines_are_missing`
   因此按 Qt 版本分两路断言，详见该测试的 docstring。

**Bug 2（播放不高亮、不跟随）**：``PlayerState`` 的值曾写成小写
``"playingState"``，而 ``str(QMediaPlayer.PlaybackState.PlayingState)`` 得到的是
``"PlaybackState.PlayingState"``（大写 P）。于是
``controller._tick()`` 里的 ``if self.state != PlayerState.PLAYING: return``
**永远成立**，每帧直接返回 —— 表现为"播放时完全不高亮"。
→ 测试 :class:`TestPlaybackTick`，用假播放器驱动真实 :class:`PlaybackController`。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from zpymusic.sync.player import PlaybackBackend, PlayerState
from zpymusic.sync.svg_geometry import flatten_svg, inline_stroke_attributes

REPO = Path(__file__).resolve().parents[1]
SVG = REPO / "staff" / "suites" / "canon-in-d-easy" / "svg" / "sys-0001.svg"
needs_svg = pytest.mark.skipif(not SVG.is_file(), reason="需要先生成套件")


def qt_version_tuple() -> tuple[int, ...]:
    """当前 Qt 版本号，形如 ``(6, 12, 0)``；解析不出的段直接忽略。"""
    from PySide6.QtCore import qVersion

    return tuple(int(p) for p in qVersion().split(".") if p.isdigit())


def qt_svg_applies_embedded_css() -> bool:
    """QtSvg 是否已套用内嵌 CSS 规则并解析 ``currentColor``。

    **分界版本是 Qt 6.12.0**：此前的 QtSvg 只支持 SVG Tiny 1.2 的子集，
    不套用 ``#id path {stroke:currentColor}``，因此 Verovio 的谱线（只带
    ``stroke-width``、颜色靠 CSS）会整条消失；6.12 起该限制被上游修复。
    """
    return qt_version_tuple() >= (6, 12)


# ===========================================================================
# Bug 1：谱线
# ===========================================================================
class TestInlineStroke:
    def test_adds_stroke_to_shapes(self) -> None:
        src = '<svg><path d="M0 0 L10 0" stroke-width="13"/><rect x="0" y="0"/></svg>'
        out = inline_stroke_attributes(src)
        assert '<path stroke="currentColor" d=' in out
        assert '<rect stroke="currentColor" x=' in out

    def test_does_not_duplicate_existing_stroke(self) -> None:
        """已有 stroke 属性时不该再加（避免覆盖 Verovio 显式指定的颜色）。"""
        src = '<svg><path stroke="red" d="M0 0"/></svg>'
        assert inline_stroke_attributes(src).count("stroke=") == 1

    def test_idempotent(self) -> None:
        src = '<svg><path d="M0 0"/></svg>'
        once = inline_stroke_attributes(src)
        assert inline_stroke_attributes(once) == once

    @needs_svg
    def test_real_svg_gets_stroke_on_staff_paths(self) -> None:
        text = SVG.read_text(encoding="utf-8")
        out = inline_stroke_attributes(text)
        # 谱线路径（stroke-width="13" 的直线）现在应带 stroke
        staff_paths = re.findall(r'<path[^>]*stroke-width="13"[^>]*>', out)
        assert staff_paths, "没有找到谱线路径"
        missing = [p for p in staff_paths if "stroke=" not in p]
        assert not missing, f"{len(missing)} 条谱线仍没有 stroke"


@needs_svg
class TestStaffLinesRender:
    """端到端：渲染出来必须**真的有横贯谱线**（而不只是"有深色像素"）。

    需要 ``qapp``：没有 QApplication 时 Qt 会在 C++ 层 access violation
    （进程直接崩，pytest 连汇总都打不出来）。
    """

    @staticmethod
    def _render(svg_text: str) -> tuple[int, list[int]]:
        """渲染到 QImage（尺寸取 SVG 自身尺寸），返回 (深色像素总数, 每行深色像素数)。"""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(svg_text.encode("utf-8"))
        assert renderer.isValid()
        size = renderer.defaultSize()
        w, h = size.width(), size.height()
        assert w > 0 and h > 0
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(0xFFFFFFFF)
        p = QPainter(img)
        renderer.render(p, QRectF(0, 0, w, h))
        p.end()
        rows: list[int] = []
        for y in range(h):
            rows.append(sum(1 for x in range(w) if img.pixelColor(x, y).lightness() < 128))
        return sum(rows), rows

    def test_staff_lines_present_after_flatten(self, qapp) -> None:  # noqa: ANN001
        text = SVG.read_text(encoding="utf-8")
        flat = flatten_svg(text)
        assert 'stroke="currentColor"' in flat, "flatten_svg 应内联 stroke"
        _dark, rows = self._render(flat)
        # 谱线特征：单行深色像素数约为页宽的 1/4 以上且横贯页面
        w = max(1, self._render_width(flat))
        threshold = max(200, w // 4)
        wide = [y for y, c in enumerate(rows) if c > threshold]
        assert len(wide) >= 5, f"应有至少 5 条谱线，实际 {len(wide)} 条（阈值 {threshold}，行号 {wide[:10]}）"

    @staticmethod
    def _render_width(svg_text: str) -> int:
        from PySide6.QtSvg import QSvgRenderer

        return QSvgRenderer(svg_text.encode("utf-8")).defaultSize().width()

    def test_without_inline_stroke_lines_are_missing(self, qapp) -> None:  # noqa: ANN001
        """反向验证：确认「不内联 stroke 谱线就不可见」这个前提**是否仍然成立**。

        这条测试的用途是给 ``flatten_svg(inline_stroke=True)`` 的必要性提供**反证**。
        但那个缺陷是 **QtSvg 的上游限制**，而 Qt 6.12.0 已把它修好（QtSvg 开始套用
        内嵌 CSS 并解析 ``currentColor``）。所以这里按版本分两路断言：

        * **Qt < 6.12**：不内联 → 谱线确实不可见（内联修复仍然必要，原始断言）；
        * **Qt >= 6.12**：不内联 → 谱线照样可见（内联成为**冗余但无害**的兼容保险；
          保留它是为了让程序在旧 Qt 上照常工作）。

        两路都在断言**可观测事实**，任一侧翻转都会失败：既守住旧版行为，
        也能在上游行为再次变化时立刻发现（而不是让测试悄悄失去意义）。

        实测依据（Qt 6.12.0，``the-four-seasons-complete/svg/sys-0001.svg``）：
        内联 211651 深色像素 / 不内联 211204，横贯谱线**均为 60 行**；
        最小复现中把 CSS 选择器改成不匹配的 ``#nope`` 则一个像素都不画 ——
        证明它真的在做选择器匹配，而不是"默认补了黑描边"。
        """
        from PySide6.QtCore import qVersion

        text = SVG.read_text(encoding="utf-8")
        no_inline = flatten_svg(text, inline_stroke=False)
        assert 'stroke="currentColor"' not in no_inline
        _dark, rows = self._render(no_inline)
        wide = [y for y, c in enumerate(rows) if c > 400]
        if qt_svg_applies_embedded_css():
            assert len(wide) >= 5, (
                f"Qt {qVersion()} 的 QtSvg 本应套用内嵌 CSS（不内联也能画出谱线），"
                f"实际只有 {len(wide)} 条横贯行 —— 上游行为可能又变了，"
                f"请复核 inline_stroke 的必要性"
            )
        else:
            assert len(wide) < 5, (
                f"预期 Qt {qVersion()}（< 6.12）不内联 stroke 时谱线不可见"
                f"（否则本测试失去意义），实际有 {len(wide)} 条横贯行"
            )

    def test_noteheads_visible_in_both_cases(self, qapp) -> None:  # noqa: ANN001
        """符头无论是否内联 stroke 都应可见（说明"看得见音符"不能作为渲染正常的证据）。"""
        text = SVG.read_text(encoding="utf-8")
        for label, svg in (
            ("不内联", flatten_svg(text, inline_stroke=False)),
            ("内联", flatten_svg(text)),
        ):
            dark, _rows = self._render(svg)
            assert dark > 1000, f"{label} 时符头也应可见，实际深色像素 {dark}"


# ===========================================================================
# Bug 2：播放状态与 tick
# ===========================================================================
class TestPlayerStateValues:
    def test_values_match_qt_enum_member_names(self) -> None:
        """值必须是 Qt 枚举的**成员名**（大写 P），旧版本写成小写导致状态恒不匹配。"""
        assert PlayerState.PLAYING.value == "PlayingState"
        assert PlayerState.PAUSED.value == "PausedState"
        assert PlayerState.STOPPED.value == "StoppedState"

    def test_str_of_qt_enum_matches_our_value(self) -> None:
        """锁住真实来源：PySide6 的 str() 末段必须等于我们的枚举值。"""
        from PySide6.QtMultimedia import QMediaPlayer

        for qt_state, ours in (
            (QMediaPlayer.PlaybackState.PlayingState, PlayerState.PLAYING),
            (QMediaPlayer.PlaybackState.PausedState, PlayerState.PAUSED),
            (QMediaPlayer.PlaybackState.StoppedState, PlayerState.STOPPED),
        ):
            assert str(qt_state).rsplit(".", 1)[-1] == ours.value

    def test_mapping_matches_qt_enum(self) -> None:
        """真 QMediaPlayer 的枚举必须能正确映射（不依赖字符串解析）。"""
        from PySide6.QtMultimedia import QMediaPlayer

        from zpymusic.sync.player import QtMediaPlayer

        Q = QMediaPlayer.PlaybackState
        assert QtMediaPlayer.map_qt_state(Q.PlayingState) == PlayerState.PLAYING
        assert QtMediaPlayer.map_qt_state(Q.PausedState) == PlayerState.PAUSED
        assert QtMediaPlayer.map_qt_state(Q.StoppedState) == PlayerState.STOPPED

    def test_real_media_player_defaults_to_stopped(self, qapp) -> None:  # noqa: ANN001
        from zpymusic.sync.player import QtMediaPlayer

        p = QtMediaPlayer()
        assert p.state() == PlayerState.STOPPED
        assert p.duration() == 0
        assert p.position() == 0


class _FakePlayer(PlaybackBackend):
    """假播放器：可以手动推进位置，并记录状态。

    继承 :class:`PlaybackBackend` 以获得 ``error`` / ``state_changed`` 等**信号**
    —— 控制器在构造时会连接它们，纯鸭子类型对象会因缺少信号而报错。
    """

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._pos = 0
        self._dur = 120_000
        self._state = PlayerState.STOPPED
        self.rate = 1.0
        self.loaded: Path | None = None

    # PlaybackBackend 的接口
    def load(self, path: Path) -> None:
        self.loaded = Path(path)

    def play(self) -> None:
        self._state = PlayerState.PLAYING
        self.state_changed.emit(self._state.value)

    def pause(self) -> None:
        self._state = PlayerState.PAUSED
        self.state_changed.emit(self._state.value)

    def stop(self) -> None:
        self._state = PlayerState.STOPPED
        self._pos = 0
        self.state_changed.emit(self._state.value)

    def seek(self, ms: int) -> None:
        self._pos = max(0, int(ms))

    def position(self) -> int:
        return self._pos

    def duration(self) -> int:
        return self._dur

    def state(self) -> PlayerState:
        return self._state

    def set_rate(self, rate: float) -> float:
        self.rate = rate
        return rate

    def set_volume(self, volume: float) -> None:
        pass


class _FakeView:
    """假视图：记录高亮计划与滚动目标。"""

    def __init__(self) -> None:
        self.systems: list = []
        self.plans: list = []
        self.scrolled: list[int] = []
        self._follow = True
        self._map: dict[str, int] = {}

    def load(self, root: Path, systems: list) -> None:
        self.systems = list(systems)
        self._map = {}
        for s in self.systems:
            for eid in s.note_ids:
                self._map.setdefault(eid, s.index)

    def build_system_map(self, out: dict[str, int]) -> None:
        out.clear()
        out.update(self._map)

    def set_highlight(self, plan) -> None:
        self.plans.append(plan)
        if self._follow and plan.rects:
            self.scrolled.append(plan.first_system)

    def clear_highlight(self) -> None:
        self.plans.append(None)

    def set_highlight_style(self, color: str, opacity: float) -> None:
        pass

    def set_follow(self, follow: bool) -> None:
        self._follow = follow

    @property
    def follow(self) -> bool:
        return self._follow


def _make_suite(tmp: Path):
    """在临时目录里造一个最小套件（sync.json + 假音频）。"""
    import json

    from zpymusic.core.suite import Suite, make_layout

    suite_dir = tmp / "demo"
    (suite_dir / "svg").mkdir(parents=True)
    notes = [
        {"id": "n1", "s": 0, "d": 1000},
        {"id": "n2", "s": 1000, "d": 1000},
        {"id": "n3", "s": 2000, "d": 1000},
    ]
    systems = [
        {"index": 1, "file": "svg/sys-0001.svg", "width": 100, "height": 50,
         "first_id": "n1", "last_id": "n2", "note_ids": ["n1", "n2"]},
        {"index": 2, "file": "svg/sys-0002.svg", "width": 100, "height": 50,
         "first_id": "n3", "last_id": "n3", "note_ids": ["n3"]},
    ]
    (suite_dir / "demo.sync.json").write_text(
        json.dumps(
            {
                "schema": "zpymusic-sync/1.1",
                "source": {"base": "demo"},
                "audio": {"duration_ms": 3000, "soundfont": "x.sf3"},
                "tempo": {"initial_bpm": 120},
                "systems": systems,
                "measures": [],
                "notes": notes,
            }
        ),
        encoding="utf-8",
    )
    (suite_dir / "demo.mp3").write_bytes(b"\x00" * 32)
    return Suite(make_layout(tmp, "demo"))


class TestPlaybackTick:
    """真实播放链路：``play() → _tick() → 高亮``。"""

    def test_tick_applies_highlight_during_playback(self, tmp_path: Path) -> None:
        """这是"播放不高亮"的直接回归测试：tick 必须真的点亮音符。"""
        from zpymusic.sync.controller import PlaybackController

        suite = _make_suite(tmp_path)
        player = _FakePlayer()
        view = _FakeView()
        ctrl = PlaybackController(player, view)  # type: ignore[arg-type]
        assert ctrl.load_suite(suite) is True

        ctrl.play()
        assert player.state() == PlayerState.PLAYING, "play() 后应处于播放状态"

        player._pos = 1500  # 落在 n2 的区间内
        ctrl._tick()

        assert ctrl.tracker is not None
        assert ctrl.tracker.active == ("n2",), (
            f"tick 后应高亮 n2，实际 {ctrl.tracker.active}；"
            "若为空，检查 PlayerState 与 QMediaPlayer 的状态比较"
        )
        assert view.plans and view.plans[-1] is not None, "视图应收到高亮计划"

    def test_tick_does_nothing_when_stopped(self, tmp_path: Path) -> None:
        from zpymusic.sync.controller import PlaybackController

        suite = _make_suite(tmp_path)
        player = _FakePlayer()
        view = _FakeView()
        ctrl = PlaybackController(player, view)  # type: ignore[arg-type]
        ctrl.load_suite(suite)
        # 未播放
        player._pos = 1500
        ctrl._tick()
        assert ctrl.tracker is not None
        assert ctrl.tracker.active == (), "未播放时不应高亮"

    def test_highlight_advances_with_position(self, tmp_path: Path) -> None:
        from zpymusic.sync.controller import PlaybackController

        suite = _make_suite(tmp_path)
        player = _FakePlayer()
        view = _FakeView()
        ctrl = PlaybackController(player, view)  # type: ignore[arg-type]
        ctrl.load_suite(suite)
        ctrl.play()

        seen: list[tuple[str, ...]] = []
        for pos in (100, 1100, 2100):
            player._pos = pos
            ctrl._tick()
            seen.append(ctrl.tracker.active if ctrl.tracker else ())
        assert seen == [("n1",), ("n2",), ("n3",)], f"高亮应随之推进，实际 {seen}"

    def test_follow_scrolls_on_system_change(self, tmp_path: Path) -> None:
        from zpymusic.sync.controller import PlaybackController

        suite = _make_suite(tmp_path)
        player = _FakePlayer()
        view = _FakeView()
        ctrl = PlaybackController(player, view)  # type: ignore[arg-type]
        ctrl.load_suite(suite)
        ctrl.play()
        for pos in (100, 2100):
            player._pos = pos
            ctrl._tick()
        assert view.scrolled == [1, 2], f"应跟随滚动到 1、2 行，实际 {view.scrolled}"

    def test_no_follow_when_disabled(self, tmp_path: Path) -> None:
        from zpymusic.sync.controller import PlaybackController

        suite = _make_suite(tmp_path)
        player = _FakePlayer()
        view = _FakeView()
        ctrl = PlaybackController(player, view)  # type: ignore[arg-type]
        ctrl.load_suite(suite)
        ctrl.set_follow(False)
        ctrl.play()
        for pos in (100, 2100):
            player._pos = pos
            ctrl._tick()
        assert view.scrolled == [], "关闭跟随后不应滚动"

    def test_rate_change_keeps_score_time_continuous(self, tmp_path: Path) -> None:
        """变速时乐谱时间不应跳变（用锚点换算）。"""
        from zpymusic.sync.controller import PlaybackController

        suite = _make_suite(tmp_path)
        player = _FakePlayer()
        view = _FakeView()
        ctrl = PlaybackController(player, view)  # type: ignore[arg-type]
        ctrl.load_suite(suite)
        ctrl.play()
        player._pos = 1000
        before = ctrl.score_time()
        ctrl.set_rate(2.0)
        after = ctrl.score_time()
        assert abs(after - before) < 100, f"{before} → {after} 跳变了"
