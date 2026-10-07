"""M6 性能基准：**默认（GPU 加速）配置 vs 软件渲染兜底配置**的 WebEngine 后端对比。

背景（见 ``sync/web_view.py`` 模块文档）：旧版本无条件注入
``--disable-gpu`` / ``QT_OPENGL=software`` / ``QT_QUICK_BACKEND=software``，
于是既产生了 ``gpu_channel_manager.cc`` 那两条报错，又真的把 GPU 合成关掉了。

本工具给出一条可复现的口径：把每个 profile 放进**独立子进程**里（环境变量必须在
导入 PySide6 之前定好），加载同一个套件的前 N 行，测量

* ``page_ms``    —— ``view.load()`` 到页面 ``loadFinished``；
* ``first_ms``   —— 首屏行挂载完成（``zpy.mountCount() >= 1``）；
* ``scroll_ms``  —— 逐步滚动直到 **N 行全部挂载**（对应 README 记的"滚动挂载 60 行"）；
* ``rows``/``svg``/``err`` —— 实际挂载行数 / 页面里的 ``<svg>`` 数 / 失败行数；
* ``gpu_error``  —— 子进程 stderr 里是否出现 ``gpu_channel_manager`` 报错。

**验收口径**（本工具判 PASS/FAIL 的三条）：

1. 默认 profile 的 ``gpu_error`` 必须是 **无**（这是 M6 的核心回归点）；
2. 两个 profile 都必须 ``mounted == rows`` 且 ``err == 0``（软件兜底仍然可用）；
3. 默认 profile 的 ``scroll_ms`` 不得明显差于 software profile
   （允许 ±30% 波动）。

⚠ 实测（the-four-seasons-complete，60 行，1200×900）两个 profile 的 ``scroll_ms``
**基本一致**（约 22 s，比值 1.00×）—— 这条路线的耗时由"取回 + 解析 + 栅格化 60 张
SVG"主导，两者都是 CPU 侧工作。所以本基准**不**用来宣称"软件渲染更慢"；
它的价值是上面第 1、2 条那个**二元回归点**，外加"没有把 GPU 配置搞得明显更差"的兜底检查。

用法::

    $py tools\\probes\\probe_webengine_perf.py --compare
    $py tools\\probes\\probe_webengine_perf.py --compare --suite the-four-seasons-complete --rows 60
    $py tools\\probes\\probe_webengine_perf.py --profile software --rows 10   # 单跑，打印 JSON

注意：本工具需要能在本机创建命名管道（普通桌面终端 / IDE 里跑）。
在受限沙箱里 Chromium 会直接 abort —— 这与图形性能无关。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"

#: 子进程用这个前缀把结果打成一行 JSON 回传给父进程
RESULT_PREFIX = "PERF_JSON "

_DEFAULT_FLAGS = "--disable-dev-shm-usage"
_SOFTWARE_FLAGS = (
    "--disable-gpu --disable-gpu-compositing --disable-gpu-rasterization "
    "--disable-accelerated-2d-canvas --disable-accelerated-video-decode "
    "--disable-dev-shm-usage"
)


def _apply_profile_env(profile: str) -> None:
    """必须在导入 PySide6 **之前**调用。"""
    for key in ("QT_OPENGL", "QT_QUICK_BACKEND", "QTWEBENGINE_CHROMIUM_FLAGS"):
        os.environ.pop(key, None)
    if profile == "software":
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = _SOFTWARE_FLAGS
        os.environ["QT_OPENGL"] = "software"
        os.environ["QT_QUICK_BACKEND"] = "software"
    else:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = _DEFAULT_FLAGS


def _run_child_once(argv: list[str]) -> int:
    """子进程：按 argparse 给的 profile 跑一次测量，打印一行 JSON。"""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--suite", default="the-four-seasons-complete")
    parser.add_argument("--rows", type=int, default=60)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--timeout", type=float, default=90.0)
    args, _ = parser.parse_known_args(argv)

    _apply_profile_env(args.profile)
    sys.path.insert(0, str(SRC))

    from PySide6.QtWidgets import QApplication

    app = QApplication([sys.argv[0]])
    from zpymusic.core.suite import discover_suites
    from zpymusic.core.sync_model import SyncDoc
    from zpymusic.sync.score_view import SystemRef
    from zpymusic.sync.web_view import WebScoreView

    suites = {s.base: s for s in discover_suites(REPO / "staff" / "suites")}
    suite = suites.get(args.suite) or (next(iter(suites.values())) if suites else None)
    if suite is None:
        print(RESULT_PREFIX + json.dumps({"error": "没有可用套件"}, ensure_ascii=False))
        return 2

    doc = SyncDoc.load(suite.layout.sync)
    refs = [
        SystemRef(
            index=s.index,
            file=s.file,
            width=s.width,
            height=s.height,
            note_ids=list(s.note_ids),
            measure_ids=list(getattr(s, "measure_ids", None) or ()),
        )
        for s in doc.systems
        if s.file
    ][: max(1, args.rows)]

    view = WebScoreView()
    if not view.backend_ready:
        print(RESULT_PREFIX + json.dumps(
            {"error": f"WebEngine 不可用：{view.unavailable_reason}"}, ensure_ascii=False))
        return 3
    view.widget.resize(args.width, args.height)
    view.widget.show()

    def pump(seconds: float) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.005)

    def js(script: str, timeout_s: float = 8.0) -> object:
        box: dict[str, object] = {"v": None, "done": False}

        def got(value: object) -> None:
            box["v"] = value
            box["done"] = True

        view._view.page().runJavaScript(script, got)
        end = time.perf_counter() + timeout_s
        while not box["done"] and time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.005)
        return box["v"]

    started = time.perf_counter()

    def wait_page(timeout_s: float) -> float:
        end = time.perf_counter() + timeout_s
        while not view._page_loaded and time.perf_counter() < end:
            pump(0.02)
        return (time.perf_counter() - started) * 1000.0

    view.load(suite.dir, refs)
    page_ms = wait_page(30.0)

    # 让页面把 setSystems 的 DOM 建出来
    pump(0.6)

    deadline = time.perf_counter() + args.timeout
    first_ms = -1.0
    t_first_start = time.perf_counter()
    while time.perf_counter() < deadline:
        pump(0.05)
        if (js("(window.zpy ? window.zpy.mountCount() : 0)") or 0) >= 1:
            first_ms = (time.perf_counter() - t_first_start) * 1000.0
            break

    # 逐步滚动到底，直到 N 行全部挂载
    t_scroll = time.perf_counter()
    positions = js(
        "JSON.stringify({h: document.body.scrollHeight, vh: window.innerHeight,"
        " n: document.querySelectorAll('.sys').length})"
    )
    try:
        info = json.loads(str(positions))
    except (TypeError, ValueError):
        info = {"h": 0, "vh": args.height, "n": len(refs)}
    step = max(80, int(int(info.get("vh") or args.height) * 0.9))
    total_h = int(info.get("h") or 0)
    pos = 0
    while pos <= total_h and time.perf_counter() < deadline:
        js(f"window.scrollTo(0, {pos});")
        # 每步只给一点点时间：steps 的节拍小了，滚动挂载耗时才主要由
        # "取回 + 解析 + 栅格化 SVG" 决定，而不是被本循环的 sleep 主导。
        pump(0.08)
        if (js("(window.zpy ? window.zpy.mountCount() : 0)") or 0) >= len(refs):
            break
        pos += step
    # 收尾：等最后一批 fetch 落盘
    while time.perf_counter() < deadline:
        pump(0.15)
        if (js("(window.zpy ? window.zpy.mountCount() : 0)") or 0) >= len(refs):
            break
    scroll_ms = (time.perf_counter() - t_scroll) * 1000.0

    stats = js(
        "JSON.stringify({rows: document.querySelectorAll('.sys').length,"
        " mounted: (window.zpy ? window.zpy.mountCount() : -1),"
        " svg: document.querySelectorAll('.sys > svg').length,"
        " err: document.querySelectorAll('.sys.error').length})"
    )
    try:
        st = json.loads(str(stats))
    except (TypeError, ValueError):
        st = {}

    out = {
        "profile": args.profile,
        "suite": suite.base,
        "rows_requested": len(refs),
        "page_ms": round(page_ms, 1),
        "first_ms": round(first_ms, 1),
        "scroll_ms": round(scroll_ms, 1),
        "rows": st.get("rows"),
        "mounted": st.get("mounted"),
        "svg": st.get("svg"),
        "err": st.get("err"),
        "chromium_flags": os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", ""),
        "qt_opengl": os.environ.get("QT_OPENGL", ""),
        "qt_quick_backend": os.environ.get("QT_QUICK_BACKEND", ""),
    }
    view.shutdown()
    print(RESULT_PREFIX + json.dumps(out, ensure_ascii=False))
    return 0


def _spawn(profile: str, args: argparse.Namespace) -> dict:
    cmd = [
        sys.executable or "python",
        str(Path(__file__).resolve()),
        "--profile",
        profile,
        "--suite",
        args.suite,
        "--rows",
        str(args.rows),
        "--width",
        str(args.width),
        "--height",
        str(args.height),
        "--timeout",
        str(args.timeout),
    ]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=env)
    gpu_error = "gpu_channel_manager" in ((proc.stderr or "") + (proc.stdout or ""))
    payload: dict = {}
    for line in (proc.stdout or "").splitlines():
        if line.startswith(RESULT_PREFIX):
            try:
                payload = json.loads(line[len(RESULT_PREFIX):])
            except ValueError:
                payload = {}
    if not payload:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()
        payload = {"error": tail[-1][:200] if tail else f"returncode={proc.returncode}"}
    payload["gpu_error"] = gpu_error
    payload["returncode"] = proc.returncode
    return payload


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--profile" in argv:
        return _run_child_once(argv)

    parser = argparse.ArgumentParser(description="WebEngine 后端 GPU/软件渲染性能对比")
    parser.add_argument("--compare", action="store_true", help="依次跑 default 与 software 两个 profile")
    parser.add_argument("--suite", default="the-four-seasons-complete")
    parser.add_argument("--rows", type=int, default=60)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args(argv)

    profiles = ["default", "software"] if args.compare else ["default"]
    results = [_spawn(p, args) for p in profiles]

    print(f"套件 = {args.suite}    目标行数 = {args.rows}    窗口 = {args.width}x{args.height}")
    header = f"{'profile':<10} {'page_ms':>9} {'first_ms':>9} {'scroll_ms':>10} {'mounted':>8} {'svg':>5} {'err':>4} {'GPU报错':>7}"
    print(header)
    print("-" * len(header))
    for r in results:
        if "error" in r:
            print(f"{r.get('profile', '?'):<10} —— 失败：{r['error']}")
            continue
        print(
            f"{r['profile']:<10} {r['page_ms']:>9.1f} {r['first_ms']:>9.1f} "
            f"{r['scroll_ms']:>10.1f} {str(r.get('mounted')):>8} "
            f"{str(r.get('svg')):>5} {str(r.get('err')):>4} "
            f"{'有' if r.get('gpu_error') else '无':>6}"
        )

    if args.compare and len(results) == 2 and all("error" not in r for r in results):
        d, s = results
        if d["scroll_ms"] > 0 and s["scroll_ms"] > 0:
            print(f"\n软件渲染 / 默认(GPU)：页面 {s['page_ms'] / d['page_ms']:.2f}×，"
                  f"滚动挂载 {s['scroll_ms'] / d['scroll_ms']:.2f}×")

    # ---------------------------------------------------------------- 验收判定
    checks: list[tuple[str, bool, str]] = []
    for r in results:
        if "error" in r:
            continue
        tag = r["profile"]
        # GPU 报错只在默认 profile 上算失败：software profile 本来就会报那两条
        # （它是"旧的自我致错配置"的对照组，报错正是它该有的样子）。
        if tag == "default":
            checks.append((f"{tag}: 不产生 GPU 报错", not r.get("gpu_error"),
                           "有 gpu_channel_manager 报错" if r.get("gpu_error") else "干净"))
        else:
            checks.append((f"{tag}: 对照组确实会报 GPU 错（证明因果）", bool(r.get("gpu_error")),
                           "有 gpu_channel_manager 报错" if r.get("gpu_error") else "干净（与预期不符）"))
        ok_rows = r.get("mounted") == r.get("rows_requested") and not r.get("err")
        checks.append((f"{tag}: 全部行挂载且 0 失败", bool(ok_rows),
                       f"mounted={r.get('mounted')}/{r.get('rows_requested')} err={r.get('err')}"))
        checks.append((f"{tag}: <svg> 数与行数一致", r.get("svg") == r.get("rows_requested"),
                       f"svg={r.get('svg')}"))
    if args.compare and len(results) == 2 and all("error" not in r for r in results):
        d, s = results
        if d["scroll_ms"] > 0 and s["scroll_ms"] > 0:
            ratio = d["scroll_ms"] / s["scroll_ms"]
            checks.append(("默认 profile 不比软件兜底明显慢（≥0.7×）", ratio >= 0.7,
                           f"默认/软件 = {ratio:.2f}×"))

    print()
    failed = 0
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  （{detail}）")
        failed += 0 if ok else 1
    print(f"\n结论：{'全部通过' if not failed else str(failed) + ' 项未通过'}"
          f"（默认 profile 的 GPU 报错是 M6 的核心回归点）")
    return 1 if (failed or any("error" in r for r in results)) else 0


if __name__ == "__main__":
    sys.exit(main())
