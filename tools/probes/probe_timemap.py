"""M0 探针 2：搞清 verovio 6.3.0 的 timemap 字段结构，决定 sync.json 的生成方式。"""
from __future__ import annotations

import json
from pathlib import Path

import verovio

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "staff" / "musicxml" / "canon-in-d-easy.mxl"


def main() -> None:
    tk = verovio.toolkit()
    tk.loadFile(str(SAMPLE))
    tk.setOptions({"scale": 40, "pageWidth": 2100, "pageHeight": 20000, "adjustPageHeight": True})
    tk.renderToMIDI()

    tm = tk.renderToTimemap({"includeMeasures": True, "includeRests": False})
    keys: dict[str, int] = {}
    for e in tm:
        for k in e:
            keys[k] = keys.get(k, 0) + 1
    print("total entries:", len(tm))
    print("field histogram:", keys)
    print("first 3:", json.dumps(tm[:3], ensure_ascii=False, indent=1))
    print("last 2:", json.dumps(tm[-2:], ensure_ascii=False, indent=1))

    # 单音高与多音高的 on 分布
    sizes: dict[int, int] = {}
    for e in tm:
        n = len(e.get("on", []))
        sizes[n] = sizes.get(n, 0) + 1
    print("on[] length histogram:", sizes)

    # tstamp 是否单调、单位与总时长
    ts = [e["tstamp"] for e in tm]
    print("tstamp monotonic:", all(b >= a for a, b in zip(ts, ts[1:])), "tstamp range:", ts[0], ts[-1])
    qs = [e["qstamp"] for e in tm]
    print("qstamp range:", qs[0], qs[-1])

    # 与 getTimesForElement 对照
    first_id = tm[0]["on"][0]
    print("first on id:", first_id, "times:", json.dumps(tk.getTimesForElement(first_id), ensure_ascii=False))

    # 逐音符取时间，看需要多少次调用与总耗时
    import time

    t0 = time.perf_counter()
    all_ids = sorted({i for e in tm for i in e.get("on", [])})
    got = {i: tk.getTimesForElement(i) for i in all_ids}
    dt = time.perf_counter() - t0
    print(f"getTimesForElement x{len(all_ids)} took {dt:.3f}s")
    tk0 = got[all_ids[0]]
    print("sample times keys:", list(tk0.keys()))
    print("sample times:", json.dumps(tk0, ensure_ascii=False))

    # 与 getElementsAtTime 交叉验证
    for ms in (0, 500, 1000, 2000, len_check := ts[-1] // 2):
        print(f"getElementsAtTime({ms}):", json.dumps(tk.getElementsAtTime(ms), ensure_ascii=False)[:180])


if __name__ == "__main__":
    main()
