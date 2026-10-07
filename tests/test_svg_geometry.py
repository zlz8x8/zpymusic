"""SVG 几何解析与高亮计划的单测（需求 §5.3 F2.6）。

这些测试锁住 M4 最关键的两个"踩过坑"的行为：

1. **单位换算**：必须按内层 ``viewBox`` 折算（canon = 840/21000 = 0.04）。
   第一版按"100 单位 = 1 px"猜，算出的符头只有 2.3 px（真实 9.08 px）。
2. **``<use>`` 解引用**：Verovio 把符头放在 ``<defs>`` 里用 ``<use>`` 引用，
   不解引用就只能得到一个点（宽高为 0）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zpymusic.sync.highlight import HighlightPlan, HighlightRect, SystemGeometryIndex, build_css
from zpymusic.sync.svg_geometry import (
    Box,
    SvgGeometry,
    flatten_svg,
    parse_svg,
    verovio_units_per_px,
)
from zpymusic.sync.svg_geometry import _parse_path_d  # noqa: PLC0415  测试内部解析器

REPO = Path(__file__).resolve().parents[1]
SYSTEM_SVG = REPO / "staff" / "suites" / "canon-in-d-easy" / "svg" / "sys-0001.svg"
BIG_SVG = REPO / "staff" / "suites" / "the-four-seasons-complete" / "svg" / "sys-0001.svg"
needs_svg = pytest.mark.skipif(not SYSTEM_SVG.is_file(), reason="需要先生成套件（zpymusic generate）")

# --------------------------------------------------------------------------- Box
class TestBox:
    def test_empty(self) -> None:
        assert Box(0, 0, 0, 0).empty
        assert Box(0, 0, 0, 5).empty  # 零面积视为空（符干靠描边补宽度）
        assert not Box(0, 0, 1, 1).empty

    def test_union(self) -> None:
        a = Box(0, 0, 10, 10)
        b = Box(5, 5, 10, 10)
        u = a.union(b)
        assert (u.x, u.y, u.w, u.h) == (0, 0, 15, 15)

    def test_expand(self) -> None:
        e = Box(10, 10, 4, 4).expand(2)
        assert (e.x, e.y, e.w, e.h) == (8, 8, 8, 8)

    def test_roundtrip(self) -> None:
        b = Box(1.5, 2.25, 3.125, 4.0)
        rt = Box.from_seq(b.to_dict())
        # to_dict() 只保留 2 位小数（与 sync.json 的约定一致）
        # 注意 3.125 用 round() 走的是"四舍六入五成双"，得到 3.12
        assert (rt.x, rt.y, rt.w, rt.h) == (1.5, 2.25, 3.12, 4.0)


# --------------------------------------------------------------------------- path 解析
class TestPathParse:
    """手写 SVG path 解析器的关键行为（Qt 未公开该入口）。"""

    def test_absolute_move_line(self) -> None:
        r = _parse_path_d("M0 0 L100 0 L100 50").boundingRect()
        assert (r.x(), r.y(), r.width(), r.height()) == (0, 0, 100, 50)

    def test_relative_commands(self) -> None:
        r = _parse_path_d("m0 0l10 0l0 10l-10 0z").boundingRect()
        assert (r.width(), r.height()) == (10, 10)

    def test_implicit_repeat_of_cubic(self) -> None:
        """``c`` 后面跟多组 6 个数字必须按隐式重复处理（Verovio 字形大量使用）。"""
        p = _parse_path_d("M0 0c1 1 2 2 3 3 4 4 5 5 6 6")
        assert p.elementCount() == 7  # MoveTo + 2 组 CurveTo/CurveToData
        assert p.boundingRect().width() == 9

    def test_implicit_lineto_after_moveto(self) -> None:
        p = _parse_path_d("M0 0 10 0 10 10")
        assert p.elementCount() == 3

    def test_number_without_separator(self) -> None:
        """``118-72`` 这类省略分隔符的写法要切成两个数。"""
        r = _parse_path_d("M0 0 L118-72").boundingRect()
        assert r.width() == 118

    def test_hv_commands(self) -> None:
        r = _parse_path_d("M0 0 H100 V50 H0 Z").boundingRect()
        assert (r.width(), r.height()) == (100, 50)

    def test_empty(self) -> None:
        assert _parse_path_d("").isEmpty()
        assert _parse_path_d("Z").isEmpty()

    def test_garbage_does_not_hang(self) -> None:
        """异常输入不能死循环（第一版解析器在这里卡死过）。"""
        for d in ("M", "L 1", "c 1 2", "!!!", "M0 0 X 1 2"):
            _parse_path_d(d)  # 只要不挂住即可


# --------------------------------------------------------------------------- flatten
class TestFlatten:
    def test_flatten_makes_qt_svg_able_to_render(self) -> None:
        """展平后的 SVG 必须能被 QtSvg 解析且带 viewBox。

        （原始嵌套结构下 QtSvg 会 ``isValid()==True`` 但渲染全白。）
        """
        from PySide6.QtSvg import QSvgRenderer

        p = Path(__file__).resolve().parents[1]
        f = p / "staff" / "suites" / "canon-in-d-easy" / "svg" / "sys-0001.svg"
        if not f.is_file():
            pytest.skip("需要先生成套件")
        text = f.read_text(encoding="utf-8")
        flat = flatten_svg(text)
        assert flat != text
        assert "viewBox=" in flat
        # 原始结构里 definition-scale 是 <svg>，展平后应是 <g>
        assert "definition-scale" in flat
        r = QSvgRenderer(flat.encode("utf-8"))
        assert r.isValid()
        # 宽度等于渲染时的 pageWidth（默认配置为 2100）
        assert r.defaultSize().width() > 500

    def test_flatten_is_idempotent(self) -> None:
        text = "<svg width='10px' height='10px'><g/></svg>"
        assert flatten_svg(text) == text
        assert flatten_svg(flatten_svg(text)) == text

    def test_flatten_keeps_xml_valid(self) -> None:
        import xml.etree.ElementTree as ET

        p = Path(__file__).resolve().parents[1]
        f = p / "staff" / "suites" / "canon-in-d-easy" / "svg" / "sys-0001.svg"
        if not f.is_file():
            pytest.skip("需要先生成套件")
        flat = flatten_svg(f.read_text(encoding="utf-8"))
        ET.fromstring(flat)  # 不应抛异常


# --------------------------------------------------------------------------- 真实 SVG
@needs_svg
class TestParseRealSvg:
    def test_units_per_px_from_viewbox(self) -> None:
        text = SYSTEM_SVG.read_text(encoding="utf-8")
        # 单位换算 = 外层 px 宽度 / 内层 viewBox 宽度（两者都从文件里读，不硬编码）
        import re

        w = float(re.search(r'<svg[^>]*?width="([\d.]+)px"', text, re.S).group(1))
        vb = float(
            re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+)', text).group(1)
        )
        assert verovio_units_per_px(text) == pytest.approx(w / vb, rel=1e-6)

    def test_geometry_basic(self) -> None:
        g = parse_svg(SYSTEM_SVG)
        assert isinstance(g, SvgGeometry)
        # 宽度 = 渲染时的 pageWidth（默认 2100），从文件读，不硬编码
        assert g.width > 500
        assert g.element_count > 50

    def test_notehead_size_is_realistic(self) -> None:
        """符头必须是真实尺寸。

        历史：M4 第一版把单位换算算错，符头只有 2.3 px（真实值随 ``scale`` 变化：
        ``scale=100`` 时约 22.7 px，``scale=40`` 时约 9.1 px）。这里用**相对判据**：
        符头宽度应约占页宽的 1% 左右，而不是写死像素值。
        """
        g = parse_svg(SYSTEM_SVG)
        notes = [e for e in g.elements.values() if e.is_note]
        assert notes, "没有解析到 note"
        widths = [e.box.w for e in notes]
        heights = [e.box.h for e in notes]
        ratio = max(widths) / g.width
        assert 0.004 <= ratio <= 0.04, (
            f"符头宽/页宽 比例异常：{ratio:.4f}（符头 {min(widths):.1f}..{max(widths):.1f}，页宽 {g.width}）"
        )
        assert 15.0 <= max(widths) <= 35.0, f"符头宽度异常：{min(widths)}..{max(widths)}"
        assert 12.0 <= max(heights) <= 35.0, f"符头高度异常：{min(heights)}..{max(heights)}"

    def test_all_elements_within_page(self) -> None:
        g = parse_svg(SYSTEM_SVG)
        for eid, eg in g.elements.items():
            assert eg.box.x >= -5, f"{eid} 越界 x={eg.box.x}"
            assert eg.box.y >= -5, f"{eid} 越界 y={eg.box.y}"
            assert eg.box.x + eg.box.w <= g.width + 5, f"{eid} 越界右 {eg.box}"
            assert eg.box.y + eg.box.h <= g.height + 5, f"{eid} 越界下 {eg.box}"

    def test_note_count_matches_svg(self) -> None:
        import re

        text = SYSTEM_SVG.read_text(encoding="utf-8")
        want = set(re.findall(r'<g id="([^"]+)" class="note"', text))
        g = parse_svg(SYSTEM_SVG)
        got = {e.id for e in g.elements.values() if e.is_note}
        assert want <= got, f"漏了 {len(want - got)} 个 note：{sorted(want - got)[:5]}"

    def test_data_attributes_captured(self) -> None:
        g = parse_svg(SYSTEM_SVG)
        notes = [e for e in g.elements.values() if e.is_note]
        assert any(e.data.get("data-pname") for e in notes)

    def test_staff_height_sane(self) -> None:
        """五线谱高度应约 72 px（5 线 × 18 px 间距），用来交叉验证单位换算。

        数值随 `scale` 变化：scale=100（页面 2100 px）下谱表约 72 px。
        """
        g = parse_svg(SYSTEM_SVG)
        staffs = [e for e in g.elements.values() if "staff" in e.classes]
        assert staffs, "没有解析到 staff"
        heights = [e.box.h for e in staffs]
        assert 50 <= max(heights) <= 120, f"谱表高度异常：{heights[:5]}"

    def test_box_of_rend_suffix(self) -> None:
        g = parse_svg(SYSTEM_SVG)
        note = next(e for e in g.elements.values() if e.is_note)
        assert g.box_of(note.id) is not None
        assert g.box_of(f"{note.id}-rend2") is not None
        assert g.box_of("不存在的id") is None

    def test_serialization_roundtrip(self) -> None:
        g = parse_svg(SYSTEM_SVG)
        back = SvgGeometry.from_dict(g.to_dict())
        assert back.width == g.width
        assert back.element_count == g.element_count
        note = next(e for e in g.elements.values() if e.is_note)
        rb = back.elements[note.id].box
        # 序列化保留 2 位小数
        assert (round(rb.x, 2), round(rb.y, 2), round(rb.w, 2), round(rb.h, 2)) == (
            round(note.box.x, 2),
            round(note.box.y, 2),
            round(note.box.w, 2),
            round(note.box.h, 2),
        )
        assert back.elements[note.id].classes == note.classes


@needs_svg
@pytest.mark.skipif(not BIG_SVG.is_file(), reason="需要 four-seasons 套件")
class TestParseBigSvg:
    def test_big_system_parses(self) -> None:
        g = parse_svg(BIG_SVG)
        notes = [e for e in g.elements.values() if e.is_note]
        assert len(notes) >= 100
        ms = max(e.box.w for e in notes)
        assert 15.0 <= ms <= 35.0, f"符头宽度异常：{ms}"

    def test_big_system_within_reasonable_time(self) -> None:
        import time

        t0 = time.perf_counter()
        parse_svg(BIG_SVG)
        assert time.perf_counter() - t0 < 1.0, "单个 system 解析应远快于 1 秒"


# --------------------------------------------------------------------------- 高亮
def _fake_geometry() -> SvgGeometry:
    from zpymusic.sync.svg_geometry import ElementGeometry

    els = {
        "n1": ElementGeometry("n1", Box(10, 10, 8, 6), ("note",)),
        "n2": ElementGeometry("n2", Box(40, 20, 8, 6), ("note",)),
        "c1": ElementGeometry("c1", Box(70, 5, 8, 30), ("chord",)),
    }
    return SvgGeometry(width=200, height=60, elements=els)


class TestSystemGeometryIndex:
    def _index(self) -> SystemGeometryIndex:
        idx = SystemGeometryIndex()
        idx.set_file(1, "svg/sys-0001.svg")
        idx.put(1, _fake_geometry())
        return idx

    def test_box_of(self) -> None:
        idx = self._index()
        assert idx.box_of(1, "n1") == Box(10, 10, 8, 6)
        assert idx.box_of(1, "nope") is None

    def test_rend_suffix_resolution(self) -> None:
        idx = self._index()
        assert idx.box_of(1, "n1-rend2") == Box(10, 10, 8, 6)

    def test_merged_box(self) -> None:
        idx = self._index()
        m = idx.merged_box(1, ["n1", "n2"])
        assert m is not None
        assert (m.x, m.y) == (10, 10)
        assert (m.w, m.h) == (38, 16)

    def test_plan_merges_same_system(self) -> None:
        idx = self._index()
        plan = idx.plan({"n1": 1, "n2": 1}, ["n1", "n2"], pad=0)
        assert len(plan.rects) == 1, "同一 system 的两个元素应合并为一个矩形"
        assert plan.rects[0].system == 1

    def test_plan_keeps_systems_separate(self) -> None:
        idx = self._index()
        idx.set_file(2, "svg/sys-0002.svg")
        idx.put(2, _fake_geometry())
        plan = idx.plan({"n1": 1, "n2": 2}, ["n1", "n2"], pad=0)
        assert {r.system for r in plan.rects} == {1, 2}

    def test_plan_padding(self) -> None:
        idx = self._index()
        plan = idx.plan({"n1": 1}, ["n1"], pad=2)
        assert plan.rects[0].box == Box(8, 8, 12, 10)

    def test_plan_skips_unknown_and_empty(self) -> None:
        idx = self._index()
        plan = idx.plan({"n1": 1}, ["n1", "未知", "n1"])
        assert len(plan.rects) == 1
        assert set(plan.ids) == {"n1", "未知"}  # 去重，但保留未知项便于诊断

    def test_plan_empty(self) -> None:
        idx = self._index()
        assert idx.plan({"n1": 1}, []).empty

    def test_element_at_hit(self) -> None:
        idx = self._index()
        assert idx.element_at(1, 14, 13) == "n1"
        assert idx.element_at(1, 44, 23) == "n2"

    def test_element_at_nearest_fallback(self) -> None:
        """点在空白处（但离某音符很近）也能定位，便于"点谱面任意位置跳转"。"""
        idx = self._index()
        assert idx.element_at(1, 20, 13) == "n1"

    def test_element_at_far_returns_empty(self) -> None:
        idx = self._index()
        assert idx.element_at(1, 199, 59) == ""

    def test_unloaded_system_returns_none(self) -> None:
        idx = self._index()
        assert idx.box_of(99, "n1") is None
        assert not idx.has(99)

    def test_cache_invalidated_on_put(self) -> None:
        idx = self._index()
        assert idx.box_of(1, "n1") == Box(10, 10, 8, 6)
        from zpymusic.sync.svg_geometry import ElementGeometry

        g = _fake_geometry()
        g.elements["n1"] = ElementGeometry("n1", Box(0, 0, 4, 4), ("note",))
        idx.put(1, g)
        assert idx.box_of(1, "n1") == Box(0, 0, 4, 4)


class TestHighlightPlanAndCss:
    def test_plan_by_system(self) -> None:
        plan = HighlightPlan(
            rects=(
                HighlightRect(1, "a", Box(0, 0, 1, 1)),
                HighlightRect(2, "b", Box(0, 0, 1, 1)),
                HighlightRect(1, "c", Box(0, 0, 1, 1)),
            )
        )
        grouped = plan.by_system()
        assert sorted(grouped) == [1, 2]
        assert len(grouped[1]) == 2
        assert plan.first_system == 1

    def test_css_contains_vars_and_classes(self) -> None:
        css = build_css("#123456", 0.4)
        assert "--zpy-hl-color: #123456" in css
        assert "--zpy-hl-alpha: 0.4" in css
        assert "zpy-hl" in css
        assert "g.note" in css and "g.chord" in css

    def test_css_clamps_opacity(self) -> None:
        assert "--zpy-hl-alpha: 1.0" in build_css("#000", 5.0)
        assert "--zpy-hl-alpha: 0.0" in build_css("#000", -1.0)

    def test_rect_padded(self) -> None:
        r = HighlightRect(1, "a", Box(10, 10, 4, 4)).padded(1)
        assert r.box == Box(9, 9, 6, 6)
        assert r.system == 1 and r.element_id == "a"
