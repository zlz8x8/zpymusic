"""排障：**浏览器引擎后端**的可用性、切换套件后的逐行加载情况。

这个脚本回答三个问题：

1. 本地只读资源服务本身是否正常（不涉及浏览器）—— 每个套件取一个 system 直接 HTTP 拉；
2. ``is_webengine_available()`` 的探测结论是什么、用的是哪个 profile
   （``default`` = 开着 GPU，``software`` = 软件渲染兜底）、失败原因属于
   **命名管道被拒**还是**建不出 GL 上下文**；
3. 真实 ``WebScoreView`` 后端连续切换 A→B→A 三个套件后，页面里
   ``.sys`` 块的 loaded / error 计数与 ``window.zpyErrors()`` 的真实原因。

"该行加载失败" 出自 ``local_server.PAGE_HTML`` 里的 ``.sys.error::after``；
服务端实测 448/448 全 200，所以只要出现失败，问题就在浏览器侧（或视图没被显示、
IntersectionObserver 因此不触发 —— 本脚本会 ``show()`` 一个真实窗口，避免这个假象）。

用法::

    $py tools\\diagnose_web_switch.py            # 探测失败就直接停
    $py tools\\diagnose_web_switch.py --force    # 无视探测结论，强制构造 WebEngine 视图

历史问题（已修）：本脚本调用了 ``view._run_js(js, callback)``，
而 ``WebScoreView._run_js()`` 只接受一个参数 → 必然 ``TypeError`` 崩溃；
旧版还从不 ``show()`` 视图，视口尺寸为 0，IntersectionObserver 永远不触发，
于是 loaded 恒为 0、看起来像"所有行都加载不出来"。现在直接用
``page().runJavaScript()`` 并显示窗口。
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

try:  # 让中文输出在 cp936 / utf-8 控制台里都不乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except (AttributeError, ValueError):  # pragma: no cover - 老解释器/被重定向
    pass

from PySide6.QtWidgets import QApplication  # noqa: E402

from zpymusic.core.suite import discover_suites  # noqa: E402
from zpymusic.core.sync_model import SyncDoc  # noqa: E402
from zpymusic.sync.score_view import SystemRef  # noqa: E402

STATUS_JS = (
    "JSON.stringify({blocks: document.querySelectorAll('.sys').length,"
    " loaded: Array.from(document.querySelectorAll('.sys'))"
    ".filter(d=>d.dataset.loaded==='1').length,"
    " err: document.querySelectorAll('.sys.error').length,"
    " zpyErrors: (window.zpyErrors ? window.zpyErrors() : null)})"
)


def refs_for(suite) -> list[SystemRef]:  # noqa: ANN001
    doc = SyncDoc.load(suite.layout.sync)
    return [
        SystemRef(
            index=s.index,
            file=s.file,
            width=s.width,
            height=s.height,
            note_ids=list(s.note_ids),
            measure_ids=list(getattr(s, "measure_ids", None) or ()),
        )
        for s in doc.systems
    ]


def main(argv: list[str]) -> int:
    force = "--force" in argv
    app = QApplication([argv[0]] if argv else sys.argv)
    suites = discover_suites(REPO / "staff" / "suites")
    if len(suites) < 2:
        print("!! 需要至少 2 个套件")
        return 2
    a, b = suites[0], suites[1]

    # ---------------------------------------------------------------- 0) 探测结论
    print("=== 0) WebEngine 可用性探测（子进程）===")
    from zpymusic.sync import web_view

    ok = web_view.is_webengine_available()
    print(f"    is_webengine_available() = {ok}")
    print(f"    profile                  = {web_view.active_profile()}")
    print(f"    失败原因分类             = {web_view.last_probe_reason() or '(成功/未探测)'}")
    print(f"    QTWEBENGINE_CHROMIUM_FLAGS = {os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '(未设置)')}")
    print(f"    QT_OPENGL / QT_QUICK_BACKEND = "
          f"{os.environ.get('QT_OPENGL', '(未设置)')} / "
          f"{os.environ.get('QT_QUICK_BACKEND', '(未设置)')}")
    if not ok and not force:
        print("    → 探测不可用，停止（加 --force 可无视探测、强制构造视图）")
        print("    " + web_view.webengine_environment_hint(web_view.last_probe_reason()))
        return 3
    print()

    # ---------------------------------------------------------------- 1) 只测服务
    print("=== 1) 只测本地资源服务（不涉及浏览器）===")
    from zpymusic.sync.local_server import LocalAssetServer

    for suite in (a, b):
        srv = LocalAssetServer(suite.dir)
        base = srv.start()
        doc = SyncDoc.load(suite.layout.sync)
        first = next((s for s in doc.systems if s.file), None)
        name = Path(first.file).name if first else ""
        url = f"{base}/svg/{name}"
        status, size, ctype = -1, 0, ""
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                status, ctype = r.status, r.headers.get("Content-Type", "")
                size = len(r.read())
        except Exception as e:  # noqa: BLE001
            print(f"    {suite.base[:34]:36s} 请求失败：{type(e).__name__}: {e}")
        print(f"    {suite.base[:34]:36s} port={srv.port} {name} -> {status} {ctype} {size}B")
        try:
            with urllib.request.urlopen(base + "/", timeout=8) as r:
                print(f"        根页面 -> {r.status} {len(r.read())}B")
        except Exception as e:  # noqa: BLE001
            print(f"        根页面失败：{e}")
        srv.stop()
    print()

    # ---------------------------------------------------------------- 2) 真实视图
    print("=== 2) 真实 WebEngine 后端：切换套件 A → B → A ===")
    from zpymusic.sync.web_view import WebScoreView

    view = WebScoreView()
    if not view.backend_ready:
        print(f"!! WebEngine 不可用：{view.unavailable_reason}")
        return 3
    print("    WebEngine 就绪")
    # 必须真正显示并给一个非零视口：IntersectionObserver 的 rootMargin 基于视口，
    # 视口为 0 时一行都不会挂载（旧版本就是这样，看起来像"全部加载失败"）。
    view.widget.resize(1200, 900)
    view.widget.show()

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    def js(script: str, timeout_s: float = 6.0) -> object:
        box: dict[str, object] = {"v": None, "done": False}

        def got(value: object) -> None:
            box["v"] = value
            box["done"] = True

        view._view.page().runJavaScript(script, got)
        end = time.time() + timeout_s
        while not box["done"] and time.time() < end:
            app.processEvents()
            time.sleep(0.02)
        return box["v"]

    def settle(seconds: float = 20.0) -> None:
        """等到"已加载行数不再增长"或超时。"""
        last = -1
        end = time.time() + seconds
        while time.time() < end:
            pump(0.4)
            cur = js("(window.zpy ? window.zpy.mountCount() : -1)")
            if cur == last:
                return
            last = cur

    def check(label: str, suite) -> None:  # noqa: ANN001
        print(f"    --- {label}: {suite.base} ---")
        view.load(suite.dir, refs_for(suite))
        settle()
        print(f"        {js(STATUS_JS)}")

    check("A", a)
    check("B（切换）", b)
    check("A（切回）", a)

    print(f"    B 套件目录 = {b.dir}")
    print(f"    服务根目录 = {view._server.root}")
    view.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
