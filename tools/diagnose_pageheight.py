"""排障：让 SVG 高度贴合内容，同时**不**把乐谱整体缩小。

问题背景：当前配置 `scale=100, pageWidth=2100, pageHeight=1500,
adjustPageHeight=False` 下，Verovio 输出的每个 system 是 **2100×1500**，
但内容只占约 2100×500 —— 页面下方约 1000 px 全是空白。

之前 M0 发现 `adjustPageHeight=True`（配合 pageHeight=400）会把乐谱**整体缩小**，
于是关掉了它。本脚本搞清楚 `adjustPageHeight` 到底在什么条件下才缩小内容，
找出"高度贴合 + 尺寸不变"的组合。

判据：
  * ``svg 高度 ≈ 内容高度``（空白少）
  * ``符头宽度`` 保持在 scale=100 下的正常值（约 22–23 px）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import verovio  # noqa: E402

SAMPLE = (
    REPO / "staff" / "musicxml"
    / "rachmaninoff-rhapsody-on-a-theme-of-paganini-variation-18-solo-piano.mxl"
)
CANON = REPO / "staff" / "musicxml" / "canon-in-d-easy.mxl"
DIM_RE = re.compile(r'<svg[^>]*?width="([\d.]+)px"[^>]*?height="([\d.]+)px"', re.S)

VARIANTS: list[tuple[str, dict]] = [
    ("A 当前配置 pH=1500 不贴合", dict(scale=100, pageWidth=2100, pageHeight=1500, adjustPageHeight=False)),
    ("B pH=1500 + 贴合", dict(scale=100, pageWidth=2100, pageHeight=1500, adjustPageHeight=True)),
    ("C pH=20000 + 贴合", dict(scale=100, pageWidth=2100, pageHeight=20000, adjustPageHeight=True)),
    ("D 不设 pH + 贴合", dict(scale=100, pageWidth=2100, adjustPageHeight=True)),
    ("E pH=6000 不贴合", dict(scale=100, pageWidth=2100, pageHeight=6000, adjustPageHeight=False)),
    ("F pH=3000 不贴合", dict(scale=100, pageWidth=2100, pageHeight=3000, adjustPageHeight=False)),
]


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)  # noqa: F841
    from zpymusic.sync.svg_geometry import parse_svg

    tmpdir = REPO / ".zpy-tmp"
    tmpdir.mkdir(exist_ok=True)
    tmp = tmpdir / "probe.svg"

    for label, extra in VARIANTS:
        opts = {"breaks": "smart", "footer": "none", "header": "none"}
        opts.update(extra)
        print(f"\n=== {label} ===")
        print(f"    选项: {extra}")
        for name, src in (("rachmaninoff", SAMPLE), ("canon", CANON)):
            tk = verovio.toolkit()
            tk.setOptions(opts)
            if not tk.loadFile(str(src)):
                print(f"    {name}: 加载失败")
                continue
            pages = tk.getPageCount()
            svg = tk.renderToSVG(1)
            m = DIM_RE.search(svg)
            w, h = (float(m.group(1)), float(m.group(2))) if m else (0, 0)
            tmp.write_text(svg, encoding="utf-8")
            g = parse_svg(tmp)
            notes = [e for e in g.elements.values() if e.is_note]
            nw = max((e.box.w for e in notes), default=0)
            # 内容实际纵向占用
            bottoms = [e.box.y + e.box.h for e in g.elements.values()]
            top = min((e.box.y for e in g.elements.values()), default=0)
            content_h = (max(bottoms) - top) if bottoms else 0
            blank = h - content_h
            verdict = "贴合" if blank < h * 0.15 else f"空白 {blank:.0f}px"
            print(
                f"    {name:14s} pages={pages:3d} svg={w:.0f}x{h:<5.0f} "
                f"内容高={content_h:5.0f} 符头宽={nw:5.1f}  {verdict}"
            )
    tmp.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
