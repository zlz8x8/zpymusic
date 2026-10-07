"""排障：曲谱视图"横向非常大、且不能缩小"。

用户报告：选 ``rachmaninoff-rhapsody-...-solo-piano`` 时界面横向扩展很大，
两侧有大量留白，无法手动缩小。

本脚本把一个套件真的载入 ``NativeScoreView``，测量：

  * SVG 自身尺寸与场景尺寸（scene rect）
  * 视图控件尺寸与缩放（transform）
  * 横向滚动条范围（是否真的"很宽"）
  * ``set_zoom`` 是否真的能缩小

并对多个套件做同样测量，找出"只有某一首异常"的原因。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from zpymusic.common.config import AppConfig  # noqa: E402,F401
from zpymusic.core.suite import discover_suites  # noqa: E402
from zpymusic.core.sync_model import SyncDoc  # noqa: E402
from zpymusic.sync.score_view import NativeScoreView, SystemRef  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    suites = discover_suites(REPO / "staff" / "suites")
    names = [
        "canon-in-d-easy",
        "rachmaninoff-rhapsody-on-a-theme-of-paganini-variation-18-solo-piano",
        "the-four-seasons-complete",
    ]
    print(f"{'套件':40s} {'svg WxH':>12s} {'scene W':>9s} {'view W':>7s} {'缩放':>6s} {'hbar max':>9s}")
    print("-" * 92)
    for name in names:
        suite = next((s for s in suites if s.base == name), None)
        if suite is None:
            continue
        doc = SyncDoc.load(suite.layout.sync)
        refs = [
            SystemRef(index=s.index, file=s.file, width=s.width, height=s.height,
                      note_ids=list(s.note_ids))
            for s in doc.systems
        ]
        view = NativeScoreView()
        view.resize(950, 600)
        view.load(suite.dir, refs)
        view._ensure_visible(force=True)
        app.processEvents()

        svg_w = refs[0].width if refs else 0
        svg_h = refs[0].height if refs else 0
        scene_w = view._scene.sceneRect().width()
        view_w = view.view.viewport().width()
        zoom = view.zoom()
        hmax = view.view.horizontalScrollBar().maximum()
        print(
            f"{suite.base[:38]:40s} {f'{svg_w:.0f}x{svg_h:.0f}':>12s} "
            f"{scene_w:9.0f} {view_w:7d} {zoom:6.2f} {hmax:9d}"
        )

        # set_zoom 是否有效
        view.set_zoom(0.6)
        app.processEvents()
        print(f"{'':40s} set_zoom(0.6) -> {view.zoom():.2f}, "
              f"hbar max -> {view.view.horizontalScrollBar().maximum()}")
        view.set_zoom(1.0)
        app.processEvents()

    print("\n=== 结论提示 ===")
    print("  若 scene W 远大于 svg W，说明场景矩形被错误放大（视图会出现大片留白）。")
    print("  若 scene W ≈ svg W 且 hbar max 合理，则'横向很大'来自 SVG 本身尺寸。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
