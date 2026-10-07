"""M0 探针 5：选定 SVG 版面选项组合（需求 Q2：单页连续为默认）。

对每个候选选项集，在 3 个不同规模的样本上测量：
  pages / 单文件大小 / SVG 尺寸 / setup+render 耗时
目标：找到"整曲一个连续纵向页面"的选项组合。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import verovio

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ["canon-in-d-easy", "waltz-in-a-minorchopin", "the-four-seasons-complete"]

COMMON = {
    "scale": 40,
    "pageWidth": 2100,
    "footer": "none",
    "header": "none",
    "svgAdditionalAttribute": ["note@pname", "note@oct", "note@dur"],
}

CANDIDATES: dict[str, dict] = {
    "A breaks=smart": {"breaks": "smart"},
    "B breaks=none + 高页": {"breaks": "none", "pageHeight": 20000, "adjustPageHeight": True},
    "C breaks=auto + 高页": {"breaks": "auto", "pageHeight": 20000, "adjustPageHeight": True},
    "D breaks=none + 巨页": {"breaks": "none", "pageHeight": 200000, "adjustPageHeight": True},
    "E 默认(不设 breaks)": {},
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
            ok = tk.loadFile(str(src))
            if not ok:
                print(f"  {name[:34]:36s} LOAD FAILED")
                continue
            tk.renderToMIDI()
            pages = tk.getPageCount()
            t_setup = time.perf_counter() - t0

            t0 = time.perf_counter()
            first = tk.renderToSVG(1)
            t_first = time.perf_counter() - t0
            m = DIM_RE.search(first)
            dims = m.groups() if m else ("?", "?")

            total_bytes = 0
            t0 = time.perf_counter()
            n_to_render = pages if pages <= 12 else 12
            for p in range(1, n_to_render + 1):
                total_bytes += len(tk.renderToSVG(p))
            t_render = time.perf_counter() - t0
            est = int(total_bytes / max(1, n_to_render) * pages)

            print(
                f"  {name[:34]:36s} pages={pages:4d} dims={dims[0]}x{dims[1]} "
                f"setup={t_setup:5.2f}s first={t_first:5.2f}s "
                f"avg/est_total={total_bytes//max(1,n_to_render)//1024}K/{est//1024//1024}M"
            )


if __name__ == "__main__":
    main()
