"""M4 探针：修正 M1 的版面问题。

M1 用了 ``breaks=smart`` + ``pageHeight=400`` + ``adjustPageHeight=True``，
M4 解析几何时发现这些设置让 Verovio **把每个 system 整体缩小约 6.7 倍**：
canon 的 5 线谱在一个 system 里只有 188 px 高（正常应约 1260 px），
符头只有 2.3×1.9 px —— 也就是"每行很小、字很小"。

本探针找出"每个 system 一个 SVG + 尺寸正常"的选项组合。
判据：符头宽度应在 8–16 px（Verovio scale=40 下的正常值）。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import verovio

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ["canon-in-d-easy", "the-four-seasons-complete"]
NOTE_ID_RE = re.compile(r'<g id="([^"]+)" class="note"')

BASE = {
    "scale": 40,
    "pageWidth": 2100,
    "footer": "none",
    "header": "none",
    "adjustPageHeight": False,
    "svgAdditionalAttribute": ["note@pname", "note@oct", "note@dur"],
}

CANDIDATES: dict[str, dict] = {
    "1 smart + adjustPageHeight": {"breaks": "smart", "adjustPageHeight": True},
    "2 smart + pH700": {"breaks": "smart", "pageHeight": 700},
    "3 smart + pH900": {"breaks": "smart", "pageHeight": 900},
    "4 smart + pH1200": {"breaks": "smart", "pageHeight": 1200},
    "5 smart + pH1500": {"breaks": "smart", "pageHeight": 1500},
    "6 none + pH20000(单页)": {"breaks": "none", "pageHeight": 20000},
    "7 smart + pH20000": {"breaks": "smart", "pageHeight": 20000},
}

_DIM_RE = re.compile(r'<svg[^>]*?width="([\d.]+)px"[^>]*?height="([\d.]+)px"')


def main() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    from zpymusic.sync.svg_geometry import parse_svg

    for label, extra in CANDIDATES.items():
        opts = dict(BASE)
        opts.update(extra)
        print(f"\n{'='*86}\n{label}  {extra}")
        for name in SAMPLES:
            src = ROOT / "staff" / "musicxml" / f"{name}.mxl"
            tk = verovio.toolkit()
            tk.setOptions(opts)
            tk.loadFile(str(src))
            tk.renderToMIDI()
            pages = tk.getPageCount()

            t0 = time.perf_counter()
            svg = tk.renderToSVG(1)
            dt = time.perf_counter() - t0
            m = _DIM_RE.search(svg)
            w, h = (float(m.group(1)), float(m.group(2))) if m else (0, 0)

            tmp = ROOT / ".probe-out.svg"
            tmp.write_text(svg, encoding="utf-8")
            g = parse_svg(tmp)
            notes = [e for e in g.elements.values() if e.is_note]
            nw = [e.box.w for e in notes] or [0]
            nh = [e.box.h for e in notes] or [0]
            tmp.unlink(missing_ok=True)

            verdict = "OK " if 6 <= max(nw) <= 20 else "!! "
            print(
                f"  {verdict}{name[:30]:32s} pages={pages:4d} sys={w:.0f}x{h:.0f} "
                f"note={len(notes):3d}/{len(NOTE_ID_RE.findall(svg)):3d} "
                f"headW={min(nw):.1f}..{max(nw):.1f} headH={min(nh):.1f}..{max(nh):.1f} "
                f"render={dt*1000:.0f}ms"
            )


if __name__ == "__main__":
    main()
