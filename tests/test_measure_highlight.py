"""M7：播放高亮从"跟随每个音符"改为"跟随当前小节"的回归测试。

**用户反馈**：播放时高亮跟着每一个音符走（一个音符一个框，闪得厉害）。
**改为**：高亮当前播放的**小节** —— 高亮区域宽度 = 小节宽度、高度 ≈ 该行谱表高度。

实现要点（本文件逐条锁住）：

1. Verovio 的 ``<g class="measure" id="…">`` 用的就是 MEI 的小节 ``xml:id``，
   与 ``sync.json.measures[].id`` 一致 → 小节矩形可以直接从 SVG 解析出来
   （``SvgGeometry.measure_box()``），宽度天然等于小节宽度、高度天然≈谱表高度；
2. 小节 → system 的归属来自 ``sync.json``（``notes[].measure``），
   **不需要解析 SVG**，因此"跳到还没挂载的行"也能先定位到行号、再按需挂载；
3. 控制器只在**小节变化**时重画（同一小节内的多个音符不再各刷一次）；
4. 旧套件（``measures`` 为空）自动退回原来的逐音符高亮；
5. 小节矩形不参与点击命中测试（否则点在谱表空白处会返回小节 ID，
   "点击谱面跳转"就失效了）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zpymusic.core.suite import Suite, SuiteLayout, make_layout
from zpymusic.sync.highlight import MEASURE_PAD, HighlightPlan, HighlightRect, build_css
from zpymusic.sync.score_view import NativeScoreView, SystemRef
from zpymusic.sync.svg_geometry import Box, parse_svg
from zpymusic.sync.timeline import Timeline

REPO = Path(__file__).resolve().parents[1]
SUITES = REPO / "staff" / "suites"
CANON = "canon-in-d-easy"
needs_suite = pytest.mark.skipif(
    not (SUITES / CANON / f"{CANON}.sync.json").is_file(),
    reason="需要先生成 canon-in-d-easy 套件",
)

VIEW_W, VIEW_H = 950, 430


# ===========================================================================
# 1. 从 SVG 解析小节矩形
# ===========================================================================
@needs_suite
class TestMeasureGeometry:
    @staticmethod
    def _doc():
        return Suite(SuiteLayout(root=SUITES, base=CANON)).load_sync()

    def test_measure_container_ids_equal_sync_ids(self, qapp) -> None:  # noqa: ANN001
        """SVG 里的小节 ID 必须与 sync.json 的 measures[].id 一致（策略的前提）。"""
        doc = self._doc()
        sync_ids = {m.id for m in doc.measures}
        seen: set[str] = set()
        for s in doc.systems:
            geom = parse_svg(SUITES / CANON / s.file)
            for m in geom.measures:
                assert m.id in sync_ids, f"{s.file} 里的小节 {m.id} 不在 sync.json 中"
                seen.add(m.id)
        assert seen, "没有解析出任何小节"

    def test_measure_boxes_tile_the_row(self, qapp) -> None:  # noqa: ANN001
        """同一行内的小节矩形应横向首尾相接（宽度 = 小节宽度，无重叠/大空隙）。

        注意一个 system SVG 里可能有**多行**谱表（Verovio 按页排版的套件），
        因此只比较垂直方向重叠的相邻小节。
        """
        doc = self._doc()
        s = doc.systems[0]
        geom = parse_svg(SUITES / CANON / s.file)
        mids = []
        for n in doc.notes:
            mid = n.get("measure")
            if mid and n["id"] in set(s.note_ids) and mid not in mids:
                mids.append(mid)
        boxes = [geom.measure_box(mid) for mid in mids]
        assert all(b is not None for b in boxes), "该行的小节应全部解析出矩形"

        same_row = 0
        for prev, cur in zip(boxes, boxes[1:]):  # noqa: B905 - 按谱面顺序
            overlap = min(prev.y + prev.h, cur.y + cur.h) - max(prev.y, cur.y)
            if overlap < 0.5 * min(prev.h, cur.h):
                continue  # 换行：不要求横向相接
            gap = cur.x - (prev.x + prev.w)
            assert abs(gap) <= 2.0, f"同行相邻小节应首尾相接，实际间隙 {gap:.1f}px"
            assert cur.w > 20.0, f"小节宽度异常：{cur.w}"
            same_row += 1
        assert same_row >= 5, f"同一行内的小节对太少（{same_row}），检查解析结果"

    def test_measure_height_is_staff_height(self, qapp) -> None:  # noqa: ANN001
        """高度应"大致与五线谱高度相当"：既非一条线，也不到整页。"""
        doc = self._doc()
        s = doc.systems[0]
        geom = parse_svg(SUITES / CANON / s.file)
        mid = next(
            n["measure"] for n in doc.notes if n.get("measure") and n["id"] in set(s.note_ids)
        )
        box = geom.measure_box(mid)
        assert box is not None
        # canon 是三行两谱表的钢琴谱：一行谱表高度约为页高的 1/4（实测 ~0.25）
        assert 0.10 * geom.height <= box.h <= 0.45 * geom.height, (
            f"小节高度 {box.h:.0f} 与谱表高度不符（页高 {geom.height:.0f}）"
        )

    def test_unknown_measure_returns_none(self, qapp) -> None:  # noqa: ANN001
        doc = self._doc()
        geom = parse_svg(SUITES / CANON / doc.systems[0].file)
        assert geom.measure_box("不存在的小节") is None

    def test_measure_is_not_highlightable_class(self, qapp) -> None:  # noqa: ANN001
        """小节容器不该混进"可高亮元素"（那是音符级回退路径用的）。"""
        doc = self._doc()
        geom = parse_svg(SUITES / CANON / doc.systems[0].file)
        assert geom.measures and not any(e.is_measure for e in geom.highlightable)

    def test_hit_test_ignores_measures(self, qapp) -> None:  # noqa: ANN001
        """点在谱表空白处不能返回小节 ID（否则点击跳转失效）。"""
        from zpymusic.sync.highlight import SystemGeometryIndex

        doc = self._doc()
        s = doc.systems[0]
        geom = parse_svg(SUITES / CANON / s.file)
        idx = SystemGeometryIndex()
        idx.put(s.index, geom)
        measure_ids = {m.id for m in geom.measures}

        checked = 0
        for m in geom.measures:
            # 逐个小节取 9 个采样点（含四角与中心）
            for fx in (0.05, 0.5, 0.95):
                for fy in (0.1, 0.5, 0.9):
                    hit = idx.element_at(
                        s.index, m.box.x + m.box.w * fx, m.box.y + m.box.h * fy
                    )
                    assert hit not in measure_ids, f"命中测试返回了小节 ID {hit}"
                    checked += 1
        assert checked >= 9


# ===========================================================================
# 2. 时间轴：元素 → 小节
# ===========================================================================
class TestTimelineMeasureMap:
    NOTES = [
        {"id": "n1", "onset_ms": 0, "dur_ms": 300, "measure": "m1"},
        {"id": "n2", "onset_ms": 300, "dur_ms": 300, "measure": "m1"},
        {"id": "n3", "onset_ms": 600, "dur_ms": 300, "measure": "m2"},
    ]

    def test_measure_of_elements(self) -> None:
        tl = Timeline(self.NOTES)
        assert tl.measure_of == {"n1": "m1", "n2": "m1", "n3": "m2"}

    def test_measures_of_dedupes_in_order(self) -> None:
        tl = Timeline(self.NOTES)
        assert tl.measures_of(["n1", "n2", "n3"]) == ["m1", "m2"]
        assert tl.measures_of(["n3", "n1"]) == ["m2", "m1"]
        assert tl.measures_of(["不存在"]) == []

    def test_rests_still_map_to_measure(self) -> None:
        """只有休止符的小节也要能定位（rest 不参与时间轴，但要参与小节映射）。"""
        tl = Timeline([{"id": "r1", "onset_ms": 0, "dur_ms": 300, "measure": "m1", "is_rest": True}])
        assert tl.measures_of(["r1"]) == ["m1"]

    def test_compact_key_variant(self) -> None:
        tl = Timeline([{"id": "n1", "s": 0, "d": 300, "m": "m9"}])
        assert tl.measures_of(["n1"]) == ["m9"]


# ===========================================================================
# 3. 原生视图：画小节覆盖框 / 未挂载行也能补全
# ===========================================================================
def _systems(doc, *, with_measures: bool = True) -> list[SystemRef]:
    tl = Timeline(doc.notes)
    return [
        SystemRef(
            index=s.index,
            file=s.file,
            width=s.width,
            height=s.height,
            note_ids=list(s.note_ids),
            measure_ids=tl.measures_of(s.note_ids) if with_measures else [],
        )
        for s in doc.systems
    ]


@needs_suite
class TestNativeMeasureOverlay:
    @staticmethod
    def _view(qapp, *, mount_all: bool = True) -> tuple[NativeScoreView, object]:  # noqa: ANN001
        doc = Suite(SuiteLayout(root=SUITES, base=CANON)).load_sync()
        view = NativeScoreView()
        view.resize(VIEW_W, VIEW_H)
        view.show()
        view.load(SUITES / CANON, _systems(doc))
        if mount_all:
            for s in view.systems:
                view._mount(s)
        qapp.processEvents()
        return view, doc

    def test_measure_systems_from_measure_ids(self, qapp) -> None:  # noqa: ANN001
        view, doc = self._view(qapp, mount_all=False)
        first = doc.systems[0]
        mid = view.systems[0].measure_ids[0]
        assert view.measure_systems(mid) == [first.index]
        assert view.measure_systems("不存在") == []
        assert view.measure_systems("") == []

    def test_rend_variant_measures_both_resolve(self, qapp) -> None:  # noqa: ANN001
        """反复记号会把同一小节展开成 ``xxx`` / ``xxx-rend2`` 两个 ID。

        ``sync.json`` 里两个都在（onset 不同），而 SVG 只画其中一个：
        两种写法都必须能定位到同一行、解析出同一个矩形（M7 实测踩到的坑：
        只按字面 ID 查时，反复记号的那几个小节会退回逐音符高亮）。
        """
        view, doc = self._view(qapp)
        pairs = []
        for m in doc.measures:
            if m.id.endswith("-rend2"):
                pairs.append((m.id[: -len("-rend2")], m.id))
        assert pairs, "canon 应该含反复记号展开出的小节"

        for base, expanded in pairs:
            sys_base = view.measure_systems(base)
            sys_exp = view.measure_systems(expanded)
            assert sys_base and sys_base == sys_exp, f"{base} / {expanded} 定位不一致"
            box_base = view.geometry_index.measure_box(sys_base[0], base)
            box_exp = view.geometry_index.measure_box(sys_base[0], expanded)
            assert box_base is not None and box_base.to_dict() == box_exp.to_dict(), (
                f"{base} 与其展开 ID 应解析出同一矩形"
            )

    def test_silent_measure_indexed_after_mount(self, qapp) -> None:  # noqa: ANN001
        """整小节只有休止符（没有音符 → ``sync.json`` 里没有它的归属）时：

        未挂载查不到；挂载该行后，行内**实际含有**的小节会被补进映射。
        """
        from zpymusic.sync.svg_geometry import ElementGeometry, SvgGeometry

        view, _doc = self._view(qapp, mount_all=False)
        geom = SvgGeometry(
            width=100,
            height=50,
            elements={
                # 模拟一行 SVG：一个静音小节 + 一个带 -rendN 的展开小节
                "silent-m": ElementGeometry(
                    id="silent-m", box=Box(0, 0, 10, 10), classes=("measure",)
                ),
                "expanded-rend2": ElementGeometry(
                    id="expanded-rend2", box=Box(1, 1, 5, 5), classes=("measure",)
                ),
            },
        )
        assert view.measure_systems("silent-m") == []
        view._index_measures(7, geom)
        assert view.measure_systems("silent-m") == [7]
        # 展开 ID 也要登记成"base 可查"
        assert view.measure_systems("expanded") == [7]
        assert view.measure_systems("expanded-rend2") == [7]

    def test_overlay_rect_matches_measure_box(self, qapp) -> None:  # noqa: ANN001
        """覆盖框 = 小节矩形 + pad；宽度/高度就是小节宽度/谱表高度。"""
        view, doc = self._view(qapp)
        system = view.systems[0].index
        mid = view.systems[0].measure_ids[0]
        expected = view.geometry_index.measure_box(system, mid)
        assert expected is not None

        plan = view.geometry_index.measure_plan([system], mid)
        assert plan.measure_id == mid and plan.first_system == system
        view.set_highlight(plan)
        qapp.processEvents()

        drawn = view._overlay.rects
        assert len(drawn) == 1, f"小节高亮应只画一个框，实际 {len(drawn)} 个"
        rect, eid = drawn[0]
        assert eid == mid
        origin = view._rects[system].topLeft()
        assert rect.width() == pytest.approx(expected.w + 2 * MEASURE_PAD, abs=0.5)
        assert rect.height() == pytest.approx(expected.h + 2 * MEASURE_PAD, abs=0.5)
        assert rect.x() == pytest.approx(origin.x() + expected.x - MEASURE_PAD, abs=0.5)
        # 高度"大致与五线谱高度相当"：明显比整行间距小、又比一条线高
        row_pitch = (
            view._rects[view.systems[1].index].top() - view._rects[system].top()
            if len(view.systems) > 1
            else rect.height() * 2
        )
        assert rect.height() < row_pitch, "小节框不该盖住相邻行"

    def test_unmounted_row_is_mounted_and_drawn(self, qapp) -> None:  # noqa: ANN001
        """跳到还没挂载的行（拖动进度条到远处）：先挂载该行再补全矩形。"""
        view, doc = self._view(qapp, mount_all=False)
        last = view.systems[-1]
        assert last.index not in view._items, "这个测试要求该行初始未挂载"
        mid = last.measure_ids[0]

        # 几何索引此时还没有该行的数据 → 计划里是空框（模拟控制器算出的计划）
        plan = view.geometry_index.measure_plan([last.index], mid)
        assert plan.rects[0].box.empty
        view.set_highlight(plan)
        qapp.processEvents()

        assert last.index in view._items, "应按需挂载目标行"
        drawn = view._overlay.rects
        assert drawn, "挂载后应补全并画出小节框"
        assert drawn[0][0].width() > 20.0
        assert drawn[0][1] == mid

    def test_clear_highlight_removes_measure_rect(self, qapp) -> None:  # noqa: ANN001
        view, _doc = self._view(qapp)
        system = view.systems[0].index
        mid = view.systems[0].measure_ids[0]
        view.set_highlight(view.geometry_index.measure_plan([system], mid))
        assert view._overlay.rects
        view.clear_highlight()
        assert view._overlay.rects == []

    def test_note_plan_still_works(self, qapp) -> None:  # noqa: ANN001
        """逐音符回退路径不能被改坏（旧套件/无小节数据时用它）。"""
        view, _doc = self._view(qapp)
        s = view.systems[0]
        plan = view.geometry_index.plan(view.system_of, s.note_ids[:3], pad=1.5)
        assert plan.rects and not plan.is_measure
        view.set_highlight(plan)
        assert view._overlay.rects

    def test_unknown_measure_falls_back_to_notes(self, qapp) -> None:  # noqa: ANN001
        """小节 ID 在 SVG 里找不到（sync.json 与 SVG 不同源）→ 退回逐音符，不能全黑。"""
        view, _doc = self._view(qapp)
        s = view.systems[0]
        # 造一个"小节存在但几何取不到"的计划，并带上当前发声的音符
        plan = view.geometry_index.measure_plan([s.index], "不存在的小节", ids=s.note_ids[:2])
        assert plan.is_measure and plan.rects[0].box.empty
        view.set_highlight(plan)
        qapp.processEvents()

        assert view._plan.measure_id == "", "应退回逐音符计划"
        assert view._overlay.rects, "回退后仍应有高亮"
        drawn_ids = {x for _r, eid in view._overlay.rects for x in eid.split("+")}
        assert drawn_ids <= set(s.note_ids[:2]), f"应高亮当前发声的音符，实际 {drawn_ids}"


# ===========================================================================
# 4. 控制器：跟随小节（而不是跟随每个音符）
# ===========================================================================
class _FakeView:
    """记录收到的 HighlightPlan（含小节→system 映射，模拟真实视图）。"""

    def __init__(self) -> None:
        self.systems: list = []
        self.plans: list[HighlightPlan] = []
        self._follow = True

    def load(self, root: Path, systems: list) -> None:
        self.systems = list(systems)

    def measure_systems(self, measure_id: str) -> list[int]:
        for s in self.systems:
            if measure_id in getattr(s, "measure_ids", ()):
                return [s.index]
        return []

    def build_system_map(self, out: dict[str, int]) -> None:
        out.clear()
        for s in self.systems:
            for eid in s.note_ids:
                out.setdefault(eid, s.index)

    def set_highlight(self, plan: HighlightPlan) -> None:
        self.plans.append(plan)

    def clear_highlight(self) -> None:
        self.plans.append(HighlightPlan())

    def set_highlight_style(self, color: str, opacity: float) -> None:
        pass

    def set_follow(self, follow: bool) -> None:
        self._follow = bool(follow)

    @property
    def follow(self) -> bool:
        return self._follow

    @property
    def geometry_index(self):  # noqa: ANN201
        """没有几何索引 → 走"只带 ID/空框"的分支（等价于 WebEngine 后端）。"""
        raise AttributeError("该假视图没有几何索引")


def _make_measure_suite(tmp: Path, *, with_measures: bool = True):
    """两个小节、每小节两个音符；小节 m1 在 system 1、m2 在 system 2。"""
    suite_dir = tmp / "demo"
    (suite_dir / "svg").mkdir(parents=True)
    notes = [
        {"id": "n1", "s": 0, "d": 400, "m": "m1"},
        {"id": "n2", "s": 400, "d": 400, "m": "m1"},
        {"id": "n3", "s": 1000, "d": 400, "m": "m2"},
        {"id": "n4", "s": 1400, "d": 400, "m": "m2"},
    ]
    if not with_measures:
        for n in notes:
            n.pop("m")
    systems = [
        {"index": 1, "file": "svg/sys-0001.svg", "width": 100, "height": 50,
         "first_id": "n1", "last_id": "n2", "note_ids": ["n1", "n2"]},
        {"index": 2, "file": "svg/sys-0002.svg", "width": 100, "height": 50,
         "first_id": "n3", "last_id": "n4", "note_ids": ["n3", "n4"]},
    ]
    measures = (
        [{"id": "m1", "onset_ms": 0}, {"id": "m2", "onset_ms": 1000}]
        if with_measures
        else []
    )
    (suite_dir / "demo.sync.json").write_text(
        json.dumps(
            {
                "schema": "zpymusic-sync/1.1",
                "source": {"base": "demo"},
                "audio": {"duration_ms": 2000, "soundfont": "x.sf3"},
                "tempo": {"initial_bpm": 120},
                "systems": systems,
                "measures": measures,
                "notes": notes,
            }
        ),
        encoding="utf-8",
    )
    (suite_dir / "demo.mp3").write_bytes(b"\x00" * 32)
    return Suite(make_layout(tmp, "demo"))


def _controller(suite, view):  # noqa: ANN001, ANN202
    from zpymusic.sync.controller import PlaybackController
    from zpymusic.sync.player import PlaybackBackend, PlayerState

    class _P(PlaybackBackend):
        def __init__(self, parent=None) -> None:  # noqa: ANN001
            super().__init__(parent)
            self._pos = 0
            self._state = PlayerState.STOPPED

        def load(self, path: Path) -> None:
            pass

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
            return 120_000

        def state(self):  # noqa: ANN201
            return self._state

        def set_rate(self, rate: float) -> float:
            return float(rate)

        def set_volume(self, volume: float) -> None:
            pass

    player = _P()
    ctrl = PlaybackController(player, view)  # type: ignore[arg-type]
    assert ctrl.load_suite(suite) is True
    # load_suite 内部会 stop()（清空高亮）→ 丢掉这次记录，后面的断言只看播放期间的计划
    plans = getattr(view, "plans", None)
    if plans is not None:
        plans.clear()
    return ctrl, player


class TestControllerFollowsMeasure:
    def test_plan_per_measure_not_per_note(self, tmp_path: Path) -> None:
        """同一小节内的多个音符只画一次；跨小节才重画（本次改动的核心）。"""
        view = _FakeView()
        ctrl, player = _controller(_make_measure_suite(tmp_path), view)
        ctrl.play()

        for pos in (50, 450, 750):  # 都在 m1（每个音符起点各刷一帧）
            player._pos = pos
            ctrl._tick()

        assert len(view.plans) == 1, f"m1 内多个音符应只画一次，实际 {len(view.plans)} 次"
        plan = view.plans[-1]
        assert plan.measure_id == "m1"
        assert plan.first_system == 1
        assert all(r.element_id == "m1" for r in plan.rects), "矩形应指向小节而不是音符"
        # 音符级追踪仍在跑（状态栏"当前音符"/按音符校准要用）
        assert ctrl.tracker is not None and ctrl.tracker.active == ("n2",)

        player._pos = 1050  # 进入 m2
        ctrl._tick()
        assert len(view.plans) == 2
        assert view.plans[-1].measure_id == "m2"
        assert view.plans[-1].first_system == 2

    def test_seek_forces_redraw_of_same_measure(self, tmp_path: Path) -> None:
        view = _FakeView()
        ctrl, _player = _controller(_make_measure_suite(tmp_path), view)
        ctrl.seek_score(10)
        ctrl.seek_score(20)
        assert [p.measure_id for p in view.plans] == ["m1", "m1"]

    def test_measure_highlight_covers_rest_only_measure(self, tmp_path: Path) -> None:
        """小节内暂时没有音符（休止）也要保持高亮，不能清掉。"""
        view = _FakeView()
        ctrl, player = _controller(_make_measure_suite(tmp_path), view)
        ctrl.play()
        player._pos = 2000  # 最后一个音符之后，仍在 m2
        ctrl._tick()
        assert view.plans and view.plans[-1].measure_id == "m2"

    def test_falls_back_to_note_highlight_without_measures(self, tmp_path: Path) -> None:
        """旧套件（没有 measures）自动退回逐音符高亮。"""
        view = _FakeView()
        ctrl, player = _controller(
            _make_measure_suite(tmp_path, with_measures=False), view
        )
        ctrl.play()
        for pos in (50, 450, 1050):
            player._pos = pos
            ctrl._tick()
        assert view.plans, "应至少画出一次高亮"
        assert all(not p.measure_id for p in view.plans), "无小节数据时不该用小节高亮"
        assert all(r.element_id in {"n1", "n2", "n3", "n4"} for p in view.plans for r in p.rects)

    def test_resets_strategy_on_stop(self, tmp_path: Path) -> None:
        view = _FakeView()
        ctrl, player = _controller(_make_measure_suite(tmp_path), view)
        ctrl.play()
        player._pos = 50
        ctrl._tick()
        ctrl.stop()
        assert ctrl._highlighted_measure == ""
        player._pos = 50
        ctrl.play()
        ctrl._tick()
        assert view.plans[-1].measure_id == "m1", "重新播放应重新画一次当前小节"


@needs_suite
class TestRealSuiteEndToEnd:
    """真实套件：控制器 + 原生视图 + 解析出的几何，整条链路跑一遍。"""

    def test_first_measure_is_highlighted_on_real_suite(self, qapp) -> None:  # noqa: ANN001
        suite = Suite(SuiteLayout(root=SUITES, base=CANON))
        view = NativeScoreView()
        view.resize(VIEW_W, VIEW_H)
        view.show()
        view.set_follow(False)
        view.set_fit_width(False)  # 固定缩放，坐标断言更直观
        ctrl, player = _controller(suite, view)

        ctrl.play()
        player._pos = 0
        ctrl._tick()
        qapp.processEvents()

        mid = ctrl.timeline.measure_at(0)  # type: ignore[union-attr]
        assert mid, "套件应带小节数据"
        system = view.measure_systems(mid)
        assert len(system) == 1
        assert system[0] in view._items, "目标行应按需挂载"

        expected = view.geometry_index.measure_box(system[0], mid)
        assert expected is not None
        drawn = view._overlay.rects
        assert len(drawn) == 1, "首帧应恰好画一个小节框"
        rect, eid = drawn[0]
        assert eid == mid
        assert rect.width() == pytest.approx(expected.w + 2 * MEASURE_PAD, abs=0.5)
        assert rect.height() == pytest.approx(expected.h + 2 * MEASURE_PAD, abs=0.5)
        # 控制器保存的计划必须是**真正画出来**的那一份（含补全后的矩形）
        assert ctrl._plan.measure_id == mid
        assert ctrl._plan.rects and not ctrl._plan.rects[0].box.empty

    def test_seek_to_far_measure_resolves_box(self, qapp) -> None:  # noqa: ANN001
        """拖到很远处（该行还没挂载）：控制器拿到的计划里也应带上真实矩形。"""
        suite = Suite(SuiteLayout(root=SUITES, base=CANON))
        view = NativeScoreView()
        view.resize(VIEW_W, VIEW_H)
        view.show()
        view.set_follow(False)
        ctrl, _player = _controller(suite, view)

        last = view.systems[-1]
        mid = last.measure_ids[0]
        onset = next(o for o, m in ctrl.timeline.measures if m == mid)  # type: ignore[union-attr]
        assert last.index not in view._items, "这个测试要求该行初始未挂载"
        ctrl.seek_score(onset)
        qapp.processEvents()

        assert last.index in view._items, "seek 应触发按需挂载"
        assert ctrl._plan.measure_id == mid
        assert ctrl._plan.rects and not ctrl._plan.rects[0].box.empty
        assert view._overlay.rects, "视图里应画出了这个矩形"

    def test_next_measure_moves_the_box(self, qapp) -> None:  # noqa: ANN001
        """播放推进到下一小节时，框要整体右移（而不是逐音符闪）。"""
        suite = Suite(SuiteLayout(root=SUITES, base=CANON))
        view = NativeScoreView()
        view.resize(VIEW_W, VIEW_H)
        view.show()
        view.set_follow(False)
        ctrl, player = _controller(suite, view)
        ctrl.play()

        first = view.systems[0].measure_ids[:2]
        player._pos = 0
        ctrl._tick()
        qapp.processEvents()
        box1 = view._overlay.rects[0][0]

        onset2 = next(o for o, mid in ctrl.timeline.measures if mid == first[1])  # type: ignore[union-attr]
        player._pos = onset2 + 10
        ctrl._tick()
        qapp.processEvents()
        box2 = view._overlay.rects[0][0]

        assert view._overlay.rects[0][1] == first[1]
        assert box2.x() > box1.x(), "下一小节的高亮框应在右边"
        assert box2.width() != pytest.approx(0.0)


# ===========================================================================
# 5. 样式与 WebEngine 后端的等价实现
# ===========================================================================
class TestMeasureStyleAndWebBackend:
    def test_build_css_styles_measure_rect(self) -> None:
        css = build_css("#123456", 0.5)
        assert "rect.zpy-hl-measure" in css
        assert "--zpy-hl-color: #123456" in css

    def test_page_has_measure_api(self) -> None:
        from zpymusic.sync.local_server import PAGE_HTML

        for needle in ("setMeasure:", "clearMeasure:", "getBBox", "rect.zpy-hl-measure"):
            assert needle in PAGE_HTML, f"页面缺少 {needle}"
        # 撤销高亮时必须同时清掉小节覆盖框（否则切模式会残留）
        assert "clearMeasure()" in PAGE_HTML.split("clearHighlight: function")[1][:200]
        # 找不到小节时要能退回逐音符高亮
        body = PAGE_HTML.split("setMeasure: function")[1].split("clearMeasure: function")[0]
        assert "setActive(ids)" in body, "页面缺少「小节几何缺失 → 逐音符」的回退"

    def test_web_view_sends_measure_id(self) -> None:
        """WebEngine 后端只需把小节 ID 交给页面，几何由 DOM 的 getBBox 负责。"""
        from zpymusic.sync.web_view import WebScoreView

        class _Fake:
            _follow = True
            backend_ready = True
            # 复用真实实现，只替换 JS 通道（不能真的起 QWebEngineView）
            _set_measure_highlight = WebScoreView._set_measure_highlight

            def __init__(self) -> None:
                self.scripts: list[str] = []

            def _run_js(self, script: str) -> None:
                self.scripts.append(script)

        fake = _Fake()
        plan = HighlightPlan(
            rects=(HighlightRect(1, "m1", Box(0, 0, 0, 0)),),
            ids=("n1",),
            measure_id="m1",
        )
        WebScoreView.set_highlight(fake, plan)  # type: ignore[arg-type]
        js = " ".join(fake.scripts)
        assert "setMeasure" in js and '"m1"' in js
        assert '"n1"' in js, "回退用的音符 ID 也要一起下发"
        assert "true" in js, "跟随开关应下发"

        # 反过来：没有小节信息时回到逐音符高亮，并且要清掉残留的小节框
        fake.scripts.clear()
        note_plan = HighlightPlan(rects=(HighlightRect(1, "n1", Box(0, 0, 1, 1)),), ids=("n1",))
        WebScoreView.set_highlight(fake, note_plan)  # type: ignore[arg-type]
        js2 = " ".join(fake.scripts)
        assert "clearMeasure()" in js2, "切回逐音符高亮时要清掉小节覆盖框"
        assert '"n1"' in js2 and "setMeasure" not in js2

    def test_web_view_measure_systems(self) -> None:
        from zpymusic.sync.web_view import WebScoreView

        class _Fake:
            _measure_of = {"m2": [7], "m3": [2, 3]}  # m3 跨行（两段）

        assert WebScoreView.measure_systems(_Fake(), "m2") == [7]  # type: ignore[arg-type]
        assert WebScoreView.measure_systems(_Fake(), "m3") == [2, 3]  # type: ignore[arg-type]
        assert WebScoreView.measure_systems(_Fake(), "m9") == []  # type: ignore[arg-type]

    def test_page_js_passes_syntax_check(self, tmp_path: Path) -> None:
        """页面 JS 能过 ``node --check``（WebEngine 后端本机跑不起来，至少别写坏语法）。"""
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            pytest.skip("本机没有 node，跳过页面 JS 语法检查")
        from zpymusic.sync.local_server import PAGE_HTML

        page = tmp_path / "page.html"
        page.write_text(PAGE_HTML, encoding="utf-8")
        script = REPO / "tools" / "check_page_js.js"
        assert script.is_file(), "缺少 tools/check_page_js.js"
        proc = subprocess.run(
            [node, str(script), str(page)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
        assert proc.returncode == 0, f"页面 JS 检查失败：\n{proc.stdout}\n{proc.stderr}"
