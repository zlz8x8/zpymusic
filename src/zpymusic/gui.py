"""``python -m zpymusic.gui`` 启动界面。

也支持**按文件路径直接执行**（``python src/zpymusic/gui.py``），这在 IDE 里点"运行"
时很常见。直接执行时 Python 不会设置 ``__package__``，相对导入
（``from .ui.main_window import …``）会抛
``ImportError: attempted relative import with no known parent package``。
这里用标准的 ``__package__`` 守卫解决：先补好 ``sys.path`` 再走**绝对导入**。

注意：本文件刻意**不直接** ``from zpymusic.ui.main_window import run_gui``，
而是调用 ``zpymusic.ui`` 暴露的延迟导入包装函数 —— 否则在 ``-m`` 场景下
``ui.main_window`` 会被导入两次（``zpymusic.ui.main_window`` 与 ``__main__``），
PySide6 的 QObject 元类会因此触发难以排查的初始化问题。
"""

from __future__ import annotations

import sys

if __package__ in (None, ""):
    # 按文件路径执行：把包的父目录（.../src）加入 sys.path，
    # 之后用绝对导入，行为与 `python -m zpymusic.gui` 完全一致。
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from zpymusic.ui import run_gui  # noqa: E402
else:
    from .ui import run_gui


def main(argv: list[str] | None = None) -> int:
    """GUI 入口。

    支持 ``--selftest``：不弹窗口，只校验导入链与环境自检，
    便于在无显示环境（CI / 远程）验证启动路径是否正常。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:
        return _selftest()
    return run_gui([sys.argv[0], *args] if argv is None else [sys.argv[0], *args])


def _selftest() -> int:
    import logging

    from zpymusic import __version__
    from zpymusic.common.deps import probe_all
    from zpymusic.common.log import setup_logging

    setup_logging(logging.WARNING)
    print(f"zpyMusic {__version__} selftest")
    print(f"  sys.path[0] = {sys.path[0]}")
    print(f"  __package__ = {__package__!r}")
    report = probe_all()
    for line in report.summary_lines():
        print("  " + line)
    # 真正导入界面模块，确认 Qt 侧依赖链完整（不创建窗口）
    import zpymusic.ui.main_window as mw  # noqa: F401

    print("  UI 导入链 OK")
    print("selftest PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
