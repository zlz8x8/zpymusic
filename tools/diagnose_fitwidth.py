"""排障：宽度自适应（fit width）与手动缩放是否真的生效。

用户报告：某些曲谱界面横向扩展很大、两侧留白多，**且无法手动缩小**。
本脚本验证修复：

  1. 载入后是否自动"适应宽度"（缩放值 = 视口宽 / 内容宽）
  2. 横向滚动条是否消失（不再需要左右拖动）
  3. 手动缩放（含缩小到 30%）是否生效
  4. 关闭"适应宽度"后是否保持手动缩放值
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from zpymusic.core.suite import discover_suites  # noqa: E402
from zpymusic.core.sync_model import SyncDoc  # noqa: E402
from zpymusic.sync.score_view import MAX_ZOOM, MIN_ZOOM, NativeScoreView, SystemRef  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    suites = discover_suites(REPO / "staff" / "suites")
    target = sys.argv[1] if len(sys.argv) > 1 else "rachmaninoff"
    suite = next((s for s in suites if target in s.base), None)
    if suite is None:
        print(f"!! 找不到套件 {target}")
        return 2

    doc = SyncDoc.load(suite.layout.sync)
    refs = [
        SystemRef(index=s.index, file=s.file, width=s.width, height=s.height,
                  note_ids=list(s.note_ids))
        for s in doc.systems
    ]
    print(f"套件 = {suite.base}")
    print(f"sync 记录: {len(refs)} 行, 宽 {refs[0].width:.0f}, 高 {refs[0].height:.0f}")
    print(f"缩放范围: {MIN_ZOOM*100:.0f}% – {MAX_ZOOM*100:.0f}%\n")

    for width in (900, 1300, 1900):
        view = NativeScoreView()
        # 必须 show() 才能拿到真实视口宽度（否则 resize 不生效、视口固定在默认值）
        view.resize(width, 700)
        view.show()
        app.processEvents()
        view.set_fit_width(True)
        view.load(suite.dir, refs)
        view._ensure_visible(force=True)
        app.processEvents()

        scene_w = view._scene.sceneRect().width()
        vp = view.view.viewport().width()
        hmax = view.view.horizontalScrollBar().maximum()
        print(f"窗口宽 {width:5d} → 视口 {vp:5d}, 场景 {scene_w:.0f}, "
              f"缩放 {view.zoom():.2f}, 横向滚动范围 {hmax}")
        if hmax > 2:
            print(f"    !! 仍有横向滚动（适应宽度未生效）：期望缩放 "
                  f"{(vp-16)/scene_w:.2f}")
        vp_before = vp
        # 手动缩小
        view.set_fit_width(False)
        view.set_zoom(0.5)
        app.processEvents()
        print(f"    手动 set_zoom(0.5) → {view.zoom():.2f}, "
              f"横向滚动范围 {view.view.horizontalScrollBar().maximum()}")
        view.set_zoom(0.3)
        print(f"    手动 set_zoom(0.3) → {view.zoom():.2f}")
        view.set_zoom(9.9)
        print(f"    上限钳制 set_zoom(9.9) → {view.zoom():.2f}")
        view.close()

    print("\n=== 结论 ===")
    print("  期望：各宽度下横向滚动范围≈0（适应宽度生效）；手动可缩到 30%。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
