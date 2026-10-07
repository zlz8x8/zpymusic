"""M0 探针 4：验证"只用 timemap 构建 onsets"的方案与 per-system 渲染。

背景（实测）：
  * getTimesForElement() 逐个调用不 scale：four-seasons 29324 个 id 共耗时 132 s。
  * pageHeight=20000 并未得到单页，four-seasons 仍为 98 页。
所以改方案：onsets 来自 timemap（on/off 配对），并按 system 分文件渲染。
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import verovio

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
SAMPLES = [
    "canon-in-d-easy",
    "waltz-in-a-minorchopin",
    "the-four-seasons-complete",
]


def build_from_timemap(tm: list[dict]) -> tuple[dict[str, tuple[int, int]], int]:
    """单遍配对 on/off，返回 {id: (onset_ms, dur_ms)} 与总时长。"""
    open_at: dict[str, int] = {}
    result: dict[str, tuple[int, int]] = {}
    total = 0
    dup = 0
    for entry in tm:
        t = int(entry["tstamp"])
        total = max(total, t)
        for eid in entry.get("off", []) or []:
            start = open_at.pop(eid, None)
            if start is None:
                continue
            if eid in result:
                dup += 1
            result[eid] = (start, t - start)
        for eid in entry.get("on", []) or []:
            open_at[eid] = t
    for eid, start in open_at.items():
        result[eid] = (start, total - start)
    return result, total


def main() -> None:
    for name in SAMPLES:
        src = ROOT / "staff" / "musicxml" / f"{name}.mxl"
        print(f"\n{'='*70}\n{name}")
        tk = verovio.toolkit()
        opts = {
            "scale": 40,
            "pageWidth": 2100,
            "breaks": "smart",
            "svgAdditionalAttribute": ["note@pname", "note@oct", "note@dur"],
            "footer": "none",
            "header": "none",
        }
        t0 = time.perf_counter()
        tk.setOptions(opts)
        tk.loadFile(str(src))
        tk.renderToMIDI()
        pages = tk.getPageCount()
        t_loaded = time.perf_counter() - t0
        print(f"pages(systems)={pages} setup={t_loaded:.2f}s")

        # 渲染前一两个 system，测单文件大小
        t0 = time.perf_counter()
        sizes = []
        for p in range(1, min(pages, 3) + 1):
            svg = tk.renderToSVG(p)
            sizes.append(len(svg))
        t_svg = time.perf_counter() - t0
        print(f"svg sizes(first 3)={sizes}  render={t_svg:.2f}s")

        t0 = time.perf_counter()
        tm = tk.renderToTimemap({"includeMeasures": True, "includeRests": False})
        notes, total = build_from_timemap(tm)
        t_tm = time.perf_counter() - t0
        print(f"timemap={len(tm)} notes_paired={len(notes)} duration={total/1000:.1f}s build={t_tm:.2f}s")

        # 抽样校验：tstamp 推出的 onset 与 getTimesForElement 是否一致
        ids = list(notes)[:400]
        t0 = time.perf_counter()
        mism = 0
        checked = 0
        for eid in ids:
            info = notes[eid]
            try:
                tv = tk.getTimesForElement(eid)
            except Exception:
                continue
            on = tv.get("tstampOn", [None])[0]
            off = tv.get("tstampOff", [None])[0]
            if on is None:
                continue
            checked += 1
            exp_on = on
            exp_dur = (off - on) if off is not None else 0
            if abs(exp_on - info[0]) > 1 or abs(exp_dur - info[1]) > 1:
                mism += 1
                if mism <= 3:
                    print(f"   MISMATCH {eid}: timemap={info} times=({exp_on},{exp_dur})")
        print(f"sample check: {checked} ids, mismatches={mism} ({time.perf_counter()-t0:.2f}s)")

        # 与 getElementsAtTime 交叉验证若干时刻
        agrees = 0
        disagree = 0
        for f in (0.1, 0.3, 0.5, 0.7):
            ms = int(total * f)
            got = tk.getElementsAtTime(ms)
            live = set(got.get("notes", [])) | set(got.get("chords", []))
            mine = {i for i, (o, d) in notes.items() if o <= ms < o + d}
            if live and mine:
                if live & mine:
                    agrees += 1
                else:
                    disagree += 1
        print(f"cross-check with getElementsAtTime: agree={agrees} disagree={disagree}")

        # 写出一个 system 的 SVG 供肉眼检查
        if name == "canon-in-d-easy":
            (OUT / "system1.svg").write_text(tk.renderToSVG(1), encoding="utf-8")
            (OUT / "sync_preview.json").write_text(
                json.dumps(
                    {
                        "duration_ms": total,
                        "sample": [{"id": k, "onset": v[0], "dur": v[1]} for k, v in list(notes.items())[:10]],
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )


if __name__ == "__main__":
    main()
