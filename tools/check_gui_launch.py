"""验证 GUI 真的能起来（开窗后 2 秒自动退出），用于排障「点运行没反应」。

用法::

    $env:QT_QPA_PLATFORM = "offscreen"   # 无显示环境
    python tools/check_gui_launch.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from zpymusic import __version__  # noqa: E402
from zpymusic.common.config import AppConfig  # noqa: E402
from zpymusic.ui.main_window import MainWindow, app_icon  # noqa: E402


def main() -> int:
    print(f"zpyMusic {__version__} 启动检查")
    app = QApplication(sys.argv)
    app.setApplicationName("zpyMusic")
    print(f"  Qt 平台 = {app.platformName()}")
    win = MainWindow(AppConfig())
    print("  MainWindow 构造完成")
    win.show()
    print(f"  可见 = {win.isVisible()}  尺寸 = {win.width()}x{win.height()}")
    print(f"  标签页 = {[win.tabs.tabText(i) for i in range(win.tabs.count())]}")
    print(f"  曲谱视图后端 = {type(win.tab_play.score_view).__name__}")
    print(f"  图标非空 = {not app_icon().isNull()}")

    QTimer.singleShot(2000, app.quit)
    app.exec()
    print("GUI 事件循环正常退出")
    print("check PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
