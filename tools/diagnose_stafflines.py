"""排障：为什么原生后端不显示五线谱的谱线？

假设：Verovio 的 SVG 里，谱线是**只有 stroke、没有 fill** 的 ``<path>``
（``<path d="M1510 620 L7382 620" stroke-width="13"/>``），
其颜色来自 CSS 规则 ``#ID path {stroke:currentColor}``。
QtSvg 只支持 SVG Tiny 1.2 的子集，**对内嵌 CSS 的支持很有限** ——
如果这条规则不生效，谱线就没有描边，于是完全不可见；
而符头是 ``<use>`` 引用的实心字形，即使没有描边也能靠 fill 显示出来，
所以"音符看得见、谱线看不见"。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

SVG = REPO / "staff" / "suites" / "canon-in-d-easy" / "svg" / "sys-0001.svg"


def main() -> int:
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)  # noqa: F841
    from zpymusic.sync.svg_geometry import flatten_svg

    text = SVG.read_text(encoding="utf-8")

    print("=== 1) CSS 里与可见性有关的规则 ===")
    m = re.search(r"<style[^>]*>(.*?)</style>", text, re.S)
    css = m.group(1) if m else ""
    for rule in css.split("}"):
        if "stroke" in rule or "fill" in rule:
            print("   " + " ".join(rule.split())[:160] + " }")

    print("\n=== 2) 谱线元素长什么样 ===")
    sm = re.search(r'<g id="[^"]+" class="staff">(.{0,200})', text, re.S)
    print("   " + " ".join(sm.group(0).split())[:200] if sm else "   not found")

    print("\n=== 3) 关键事实 ===")
    print("   path 元素自带 stroke 属性？      ", 'stroke="' in text)
    print("   CSS 提供了 stroke:currentColor？ ", "stroke:currentColor" in text)

    def render(label: str, svg_text: str) -> tuple[int, list[int]]:
        r = QSvgRenderer(svg_text.encode("utf-8"))
        w, h = 840, 188
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(0xFFFFFFFF)
        p = QPainter(img)
        r.render(p, QRectF(0, 0, w, h))
        p.end()
        dark = 0
        rows: list[int] = []
        for y in range(h):
            c = sum(1 for x in range(w) if img.pixelColor(x, y).lightness() < 128)
            rows.append(c)
            dark += c
        return dark, rows

    print("\n=== 4) 渲染对比（谱线检测：单行深色像素 > 150 视为一条横贯线）===")
    for label, txt in (
        ("原始", text),
        ("仅展平不内联", flatten_svg(text, inline_stroke=False)),
        ("展平+内联（修复后）", flatten_svg(text)),
    ):
        dark, rows = render(label, txt)
        wide = [y for y, c in enumerate(rows) if c > 150]
        print(f"   {label:18s} 深色像素={dark:5d}  横贯谱线={len(wide):2d} 条  行号={wide[:8]}")

    print("\n=== 结论 ===")
    print("   '仅展平不内联' 的深色像素 > 0 但横贯谱线 = 0 —— 这正是用户看到的现象：")
    print("   音符（实心字形）看得见，五线谱的线全都不见。")
    print("   原因是 Verovio 的谱线只有 stroke-width、颜色靠内嵌 CSS")
    print("   `#id path {stroke:currentColor}`，而 QtSvg 不套用该规则。")
    print("   修复：flatten_svg() 现在会把 stroke 内联到元素上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
