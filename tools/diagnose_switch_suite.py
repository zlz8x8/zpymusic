"""排障：切换套件后曲谱加载失败。

用户报告：默认的第一首曲子播放与显示都正常；**换一首后就报"该行加载失败"**。

注意："该行加载失败" 是 **WebEngine 后端**（`local_server.PAGE_HTML` 里的
``.sys.error::after { content:"该行加载失败" }``）才会显示的文字 ——
原生 QtSvg 后端根本没有这个概念。所以要么用户配置仍是 web/auto 且探测通过，
要么这条信息另有来源。本脚本按顺序切换多个套件，逐步检查两个后端的实际状态。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from zpymusic.common.config import AppConfig  # noqa: E402
from zpymusic.core.suite import discover_suites  # noqa: E402
from zpymusic.ui.main_window import MainWindow  # noqa: E402

SUITES = REPO / "staff" / "suites"


def main() -> int:
    app = QApplication(sys.argv)
    cfg = AppConfig()
    print(f"配置里的后端偏好 = {cfg.ui.score_backend!r}")
    win = MainWindow(cfg)
    win.show()
    app.processEvents()
    play = win.tab_play
    view = play.score_view
    print(f"实际后端 = {type(view).__name__}")
    print(f"套件根目录 = {play.ed_root.text()}")

    suites = discover_suites(SUITES)
    print(f"发现 {len(suites)} 个套件\n")
    # 只取前 3 个 + 一个最大的，够复现即可
    picks = suites[:3]
    big = next((s for s in suites if s.base == "the-four-seasons-complete"), None)
    if big is not None and big not in picks:
        picks.append(big)

    for i, suite in enumerate(picks, 1):
        print(f"--- {i}) 选择 {suite.base} ---")
        play._select_suite(suite)
        app.processEvents()

        if hasattr(view, "_items"):
            # 原生后端
            view._ensure_visible(force=True)
            app.processEvents()
            mounted = sorted(view._items)
            print(f"    原生后端：已挂载 system = {mounted}")
            if not mounted:
                print("    !! 没有任何 system 挂载 —— 这就是'空白'的原因")
            else:
                # 抽查每个已挂载项：渲染器是否有效
                bad = []
                for idx in mounted:
                    r = view.renderer_for(idx)
                    if r is None or not r.isValid():
                        bad.append(idx)
                print(f"    渲染器无效的 system = {bad or '无'}")
            sysmap = {}
            view.build_system_map(sysmap)
            print(f"    system 映射元素数 = {len(sysmap)}")
        else:
            # WebEngine 后端
            print(f"    WebEngine：server running = {view._server.running}, port = {view._server.port}")
            print(f"    base_url = {getattr(view, '_pending_base', '?')}")
            print(f"    page_loaded = {view._page_loaded}")
            print(f"    systems 数 = {len(view.systems)}")
            if view.systems:
                f0 = view.systems[0].file
                target = view._root / f0
                print(f"    首个 system 文件 = {f0}")
                print(f"    该文件存在 = {target.is_file()}  ({target})")
            print(f"    本地服务根目录 = {view._server.root}")
        print()

    # 再切回第一个，验证"来回切换"是否也正常
    print("--- 回到第一个套件 ---")
    play._select_suite(picks[0])
    app.processEvents()
    if hasattr(view, "_items"):
        view._ensure_visible(force=True)
        app.processEvents()
        print(f"    已挂载 system = {sorted(view._items)}")

    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
