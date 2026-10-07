"""M0 探针 6：确定"每个 system 一个紧凑 SVG"的选项组合。

需求 §5.1.2：默认单页连续纵向；但大曲目需要懒加载 → 倾向"每行一个 SVG"。
关键：breaks='smart'/'line' 配合小 pageHeight + adjustPageHeight 是否能得到
"每行一个、高度贴合内容"的 SVG。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import verovio

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ["canon-in-d-easy", "the-four-seasons-complete"]

COMMON = {
    "scale": 40,
    "pageWidth": 2100,
    "footer": "none",
    "header": "none",
    "adjustPageHeight": True,
    "svgAdditionalAttribute": ["note@pname", "note@oct", "note@dur"],
}

CANDIDATES: dict[str, dict] = {
    "F smart+h400": {"breaks": "smart", "pageHeight": 400},
    "G line+h400": {"breaks": "line", "pageHeight": 400},
    "H none+h600 + auto": {"breaks": "none", "pageHeight": 600},
}

DIM_RE = re.compile(r'<svg[^>]*?width="([^"]+)"[^>]*?height="([^"]+)"')


def main() -> None:
    for label, extra in CANDIDATES.items():
        opts = dict(COMMON)
        opts.update(extra)
        print(f"\n{'='*78}\n{label}  {extra}")
        for name in SAMPLES:
            src = ROOT / "staff" / "musicxml" / f"{name}.mxl"
            tk = verovio.toolkit()
            tk.setOptions(opts)
            t0 = time.perf_counter()
            tk.loadFile(str(src))
            tk.renderToMIDI()
            pages = tk.getPageCount()
            t_setup = time.perf_counter() - t0

            heights: list[int] = []
            widths: set[str] = set()
            total = 0
            t0 = time.perf_counter()
            n = min(pages, 20)
            for p in range(1, n + 1):
                svg = tk.renderToSVG(p)
                total += len(svg)
                m = DIM_RE.search(svg)
                if m:
                    widths.add(m.group(1))
                    heights.append(int(re.sub(r"\D", "", m.group(2)) or 0))
            t_render = time.perf_counter() - t0
            avg_h = sum(heights) // max(1, len(heights))
            per_note_ok = tk.getPageWithElement(
                next(e["on"][0] for e in tk.renderToTimemap() if e.get("on"))
            )
            print(
                f"  {name[:30]:32s} pages={pages:4d} w={sorted(widths)[:2]} avgH={avg_h:6d} "
                f"mxH={max(heights) if heights else 0:6d} avg={total//max(1,n)//1024:5d}K "
                f"est={total//max(1,n)*pages//1024//1024:3d}M setup={t_setup:5.2f}s render={t_render:5.2f}s "
                f"pageOfFirstNote={per_note_ok}"
            )


if __name__ == "__main__":
    main()
