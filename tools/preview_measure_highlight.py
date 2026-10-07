"""目视验证：小节高亮框的位置与尺寸（离屏渲染成 PNG）。

M7 把播放高亮从"跟随每个音符"改成"跟随当前小节"后，最直接的验货方式就是
把覆盖框画出来看一眼：**宽度是否等于小节宽度、高度是否≈该行谱表高度**。

本脚本用真实套件做三件事：

1. 依次高亮某一行的前几个小节，打印覆盖框的坐标/尺寸，并把第 2 个小节存成图；
2. 模拟"拖动进度条到很远的、还没挂载的行"，验证按需挂载 + 矩形补全；
3. 统计 `parse_svg()` 的平均耗时（小节矩形是边走边并出来的，不该拖慢解析）。

用法::

    $env:QT_QPA_PLATFORM = "offscreen"
    python tools/preview_measure_highlight.py [套件名] [输出目录]

默认套件 ``canon-in-d-easy``，默认输出到 ``images/``。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from zpymusic.core.suite import Suite, SuiteLayout  # noqa: E402
from zpymusic.sync.score_view import NativeScoreView, SystemRef  # noqa: E402
from zpymusic.sync.svg_geometry import parse_svg  # noqa: E402
from zpymusic.sync.timeline import Timeline  # noqa: E402

SUITES = REPO / "staff" / "suites"
VIEW_W, VIEW_H = 1100, 560


def _build(app, root: Path, base: str) -> NativeScoreView:  # noqa: ANN001
    doc = Suite(SuiteLayout(root=root, base=base)).load_sync()
    tl = Timeline(doc.notes)
    systems = [
        SystemRef(
            index=s.index,
            file=s.file,
            width=s.width,
            height=s.height,
            note_ids=list(s.note_ids),
            measure_ids=tl.measures_of(s.note_ids),
        )
        for s in doc.systems
    ]
    view = NativeScoreView()
    view.resize(VIEW_W, VIEW_H)
    view.show()
    view.load(root / base, systems)
    app.processEvents()
    view.set_follow(False)
    view.set_fit_width(False)
    view.set_zoom(0.45)
    app.processEvents()
    return view


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "canon-in-d-easy"
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "images"
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    view = _build(app, SUITES, base)
    first = view.systems[0]
    print(f"套件 = {base}  行数 = {len(view.systems)}  首行小节 = {len(first.measure_ids)}")

    print("\n=== 1) 首行前 3 个小节的高亮框 ===")
    for i, mid in enumerate(first.measure_ids[:3]):
        plan = view.geometry_index.measure_plan([first.index], mid)
        view.set_highlight(plan)
        app.processEvents()
        drawn = view._overlay.rects[0][0] if view._overlay.rects else None
        box = view.geometry_index.measure_box(first.index, mid)
        if drawn is None or box is None:
            print(f"  小节 #{i} {mid}: 没画出矩形（几何缺失）")
            continue
        print(
            f"  小节 #{i} {mid}: 小节 {box.w:.0f}×{box.h:.0f}px → "
            f"高亮框 {drawn.width():.0f}×{drawn.height():.0f}px @ ({drawn.x():.0f},{drawn.y():.0f})"
        )
        if i == 1:
            path = out_dir / "measure-highlight.png"
            view.grab().save(str(path))
            print(f"  已存图：{path}")

    print("\n=== 2) 跳到还没挂载的远行 ===")
    view2 = _build(app, SUITES, base)
    last = view2.systems[-1]
    print(f"  挂载前：末行 {last.index} 已挂载 = {last.index in view2._items}")
    mid = last.measure_ids[0]
    plan = view2.geometry_index.measure_plan([last.index], mid)
    print(f"  计划里的矩形（未解析）为空框 = {plan.rects[0].box.empty}")
    view2.set_highlight(plan)
    app.processEvents()
    print(
        f"  挂载后：末行 {last.index} 已挂载 = {last.index in view2._items}"
        f"  覆盖框数 = {len(view2._overlay.rects)}"
    )
    view2.scroll_to_system(last.index)
    app.processEvents()
    path = out_dir / "measure-highlight-far.png"
    view2.grab().save(str(path))
    print(f"  已存图：{path}")

    print("\n=== 3) 解析耗时（含小节矩形）===")
    t0 = time.perf_counter()
    for s in view.systems:
        parse_svg(SUITES / base / s.file)
    n = max(1, len(view.systems))
    print(f"  parse_svg 平均 {(time.perf_counter() - t0) / n * 1000:.1f} ms/行（{n} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
