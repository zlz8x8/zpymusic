"""排障：本地资源服务能否稳定服务**所有套件**的**所有** system SVG？

如果某首曲子的 SVG 请求会拿到非 200（或服务在切换后失效），
WebEngine 页面就会把对应的行标记成 `.sys.error` → 显示"该行加载失败"。
本脚本把 10 个套件的每个 SVG 都请求一遍，统计状态码与失败项。

同时验证"反复 **停止旧服务 → 起新服务 → 再请求**"这个切换流程是否稳定
（WebEngine 后端在每次切换套件时就是这么做的）。

注意 URL 约定：行 SVG 的相对路径就是 ``sync.json`` 里的 ``SystemRef.file``
（套件里是 ``svg/sys-0001.svg``）。页面按 ``/sys/<相对路径>`` 请求它，
``/svg/<名字>`` 是等价的旧路由。**若这里全部 200 而应用里仍报 404**，
说明请求的根目录不对（例如预览窗口的缓存根目录/落盘层级不一致，见
``docs/requirements.md`` §12.9.1），而不是服务的问题。
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from zpymusic.core.suite import discover_suites  # noqa: E402
from zpymusic.core.sync_model import SyncDoc  # noqa: E402
from zpymusic.sync.local_server import LocalAssetServer  # noqa: E402


def fetch(url: str, timeout: float = 20.0) -> tuple[int, int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, len(r.read()), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, 0, f"HTTPError {e.reason}"
    except Exception as e:  # noqa: BLE001
        return -1, 0, f"{type(e).__name__}: {e}"


def main() -> int:
    suites = discover_suites(REPO / "staff" / "suites")
    print(f"套件数 = {len(suites)}\n")

    print("=== 1) 每个套件各起一个服务，逐个请求它的所有 system SVG ===")
    total = 0
    problems: list[str] = []
    codes: Counter[int] = Counter()
    for suite in suites:
        srv = LocalAssetServer(suite.dir)
        base = srv.start()
        doc = SyncDoc.load(suite.layout.sync)
        files = [s.file for s in doc.systems if s.file]
        bad = 0
        for f in files:
            name = Path(f).name
            status, size, ctype = fetch(f"{base}/svg/{name}")
            codes[status] += 1
            total += 1
            if status != 200 or size <= 0 or "svg" not in ctype:
                bad += 1
                if len(problems) < 8:
                    problems.append(f"{suite.base}/{name} -> {status} {size}B {ctype}")
        flag = "OK " if bad == 0 else "!! "
        print(f"  {flag}{suite.base[:40]:42s} {len(files):4d} 个 SVG，失败 {bad}")
        srv.stop()

    print(f"\n  合计请求 {total} 个，状态码分布 = {dict(codes)}")
    if problems:
        print("  失败样例：")
        for p in problems:
            print("    " + p)

    print("\n=== 2) 反复「停旧服务 → 起新服务 → 请求」（模拟切换套件）===")
    ok = 0
    fail = 0
    srv: LocalAssetServer | None = None
    for round_no in range(1, len(suites) + 1):
        suite = suites[(round_no - 1) % len(suites)]
        if srv is not None:
            srv.stop()
        srv = LocalAssetServer(suite.dir)
        base = srv.start()
        doc = SyncDoc.load(suite.layout.sync)
        f = next((s.file for s in doc.systems if s.file), "")
        status, size, _ct = fetch(f"{base}/svg/{Path(f).name}")
        if status == 200 and size > 0:
            ok += 1
        else:
            fail += 1
            print(f"  第 {round_no} 轮失败：{suite.base} -> {status} ({size}B)")
    if srv is not None:
        srv.stop()
    print(f"  {ok} 轮成功 / {fail} 轮失败")

    print("\n=== 结论 ===")
    if not problems and fail == 0:
        print("  服务层没有问题：所有套件的所有 SVG 都能正常返回 200。")
        print("  → '该行加载失败' 的原因不在服务端，需从浏览器侧（URL/端口/时序）继续查。")
        return 0
    print("  !! 服务层确实存在问题，上面已列出失败项。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
