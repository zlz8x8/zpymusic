"""M0 环境探针：验证 Verovio 在本机 Python 3.13.16 + verovio 6.3.0 下的关键 API 行为。

结论将回填到 docs/requirements.md 附录 B。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import verovio

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
SAMPLE = ROOT / "staff" / "musicxml" / "canon-in-d-easy.mxl"


def main() -> int:
    tk = verovio.toolkit()
    print("verovio version:", tk.getVersion())

    t0 = time.perf_counter()
    ok = tk.loadFile(str(SAMPLE))
    print("loadFile:", ok)
    if not ok:
        print("LOG:", tk.getLog())
        return 1

    tk.setOptions(
        {
            "scale": 40,
            "pageWidth": 2100,
            "pageHeight": 20000,
            "adjustPageHeight": True,
            "svgAdditionalAttribute": ["note@pname", "note@oct", "note@dur"],
            "footer": "none",
            "header": "none",
        }
    )
    print("pages:", tk.getPageCount())

    # 时间查询类 API 必须先 renderToMIDI
    midi_b64 = tk.renderToMIDI()
    print("renderToMIDI type:", type(midi_b64).__name__, "len:", len(midi_b64))

    tm = tk.renderToTimemap({"includeMeasures": True, "includeRests": False})
    print("renderToTimemap type:", type(tm).__name__, "entries:", len(tm))
    (OUT / "timemap.json").write_text(
        json.dumps(tm, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("first entry:", json.dumps(tm[0], ensure_ascii=False)[:200])

    kinds: dict[str, int] = {}
    for e in tm:
        kinds[e.get("type", "?")] = kinds.get(e.get("type", "?"), 0) + 1
    print("timemap entry types:", kinds)

    note_ids = [e["id"] for e in tm if e.get("type") == "note"]
    chord_ids = [e["id"] for e in tm if e.get("type") == "chord"]
    print("note ids:", len(note_ids), "chord ids:", len(chord_ids))
    print("sample note ids:", note_ids[:5])

    for probe_id in (note_ids or chord_ids)[:2]:
        times = tk.getTimesForElement(probe_id)
        print(f"getTimesForElement({probe_id}):", json.dumps(times, ensure_ascii=False))
        try:
            print(f"  getMIDIValuesForElement:", json.dumps(tk.getMIDIValuesForElement(probe_id), ensure_ascii=False))
        except Exception as e:  # noqa: BLE001
            print("  getMIDIValuesForElement FAILED:", e)
        print("  getPageWithElement:", tk.getPageWithElement(probe_id))
        print("  getElementAttr keys:", list(tk.getElementAttr(probe_id).keys())[:12])

    print("getElementsAtTime(1000):", json.dumps(tk.getElementsAtTime(1000), ensure_ascii=False)[:200])
    print("getElementsAtTime(0):", json.dumps(tk.getElementsAtTime(0), ensure_ascii=False)[:200])

    svg = tk.renderToSVG(1)
    (OUT / "page1.svg").write_text(svg, encoding="utf-8")
    print("svg len:", len(svg))
    # 抽样检查 SVG 是否保留 id 与 class
    import re

    gs = re.findall(r'<g[^>]*class="[^"]*note[^"]*"[^>]*>', svg)[:3]
    for g in gs:
        print("svg note g:", g[:220])
    print("svg has id attr count:", svg.count('id="'))
    print("has data-pname:", "data-pname" in svg)

    print("elapsed %.2fs" % (time.perf_counter() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
