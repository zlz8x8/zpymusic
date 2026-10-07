"""M4 探针 2：确定正确的 ``scale``。

M1 选了 ``scale=40``（当时只看"页数"），M4 解析几何才发现它让**整份谱被缩小约 5 倍**：
符头只有 2.3×1.9 px、相邻音符只隔 5 px（一个 5 线谱在 188 px 里）。

本探针扫描 scale，以"符头宽度落在 8–16 px"为判据（Verovio 正常排版下的经验值）。
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import verovio

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

BASE = {
    "pageWidth": 2100,
    "breaks": "smart",
    "pageHeight": 1500,
    "footer": "none",
    "header": "none",
    "svgAdditionalAttribute": ["note@pname", "note@oct", "note@dur"],
}

_DIM_RE = re.compile(r'<svg[^>]*?width="([\d.]+)px"[^>]*?height="([\d.]+)px"')


def main() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    from zpymusic.sync.svg_geometry import parse_svg

    print(f"{'scale':>6s} {'样本':26s} {'pages':>6s} {'system 尺寸':>14s} {'符头宽':>16s} {'符头高':>16s} {'音间距':>8s}")
    for scale in (40, 60, 80, 100, 120, 150):
        for name in ("canon-in-d-easy", "the-four-seasons-complete"):
            src = ROOT / "staff" / "musicxml" / f"{name}.mxl"
            opts = dict(BASE, scale=scale)
            tk = verovio.toolkit()
            tk.setOptions(opts)
            tk.loadFile(str(src))
            pages = tk.getPageCount()
            t0 = time.perf_counter()
            svg = tk.renderToSVG(1)
            dt = time.perf_counter() - t0
            m = _DIM_RE.search(svg)
            w, h = (float(m.group(1)), float(m.group(2))) if m else (0, 0)

            tmp = ROOT / ".probe-out.svg"
            tmp.write_text(svg, encoding="utf-8")
            g = parse_svg(tmp)
            tmp.unlink(missing_ok=True)
            notes = sorted(
                (e for e in g.elements.values() if e.is_note), key=lambda e: e.box.x
            )
            nw = [e.box.w for e in notes] or [0]
            nh = [e.box.h for e in notes] or [0]
            gaps = [
                notes[i + 1].box.x - notes[i].box.x
                for i in range(min(len(notes) - 1, 40))
                if notes[i + 1].box.x > notes[i].box.x
            ]
            gap = min(gaps) if gaps else 0.0
            ok = "OK " if 8 <= max(nw) <= 20 else "   "
            print(
                f"{scale:>6d} {name[:24]:26s} {pages:>6d} {w:>7.0f}x{h:<6.0f} "
                f"{min(nw):>6.1f}..{max(nw):<8.1f} {min(nh):>6.1f}..{max(nh):<8.1f} "
                f"{gap:>7.1f} {ok}{dt*1000:.0f}ms"
            )
        print()


if __name__ == "__main__":
    main()
