"""pytest 公共配置。

提供：

* 把 ``src`` 加入 ``sys.path``（让测试无需安装包即可运行）。
* **工作区内的 ``tmp_path``**：默认的 ``tmp_path`` 用系统临时目录，在受限的
  文件沙箱里会 ``PermissionError``，因此改写为项目内的 ``.pytest-tmp``。
* 引擎探测辅助（``verovio_available``）。
"""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SAMPLES = REPO / "staff" / "musicxml"
TMP_ROOT = REPO / ".pytest-tmp"


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    """工作区内的临时目录（覆盖 pytest 内置同名 fixture）。

    原因：内置实现落在系统 TEMP，而本项目的运行沙箱只允许写工作区，
    会直接 ``PermissionError``。这里改成 ``<repo>/.pytest-tmp/<用例名>-<随机>``，
    用完即删。
    """
    safe = request.node.name.replace("/", "_").replace("\\", "_").replace(":", "_")
    path = TMP_ROOT / f"{safe}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="session")
def verovio_available() -> bool:
    try:
        import verovio  # noqa: F401

        return True
    except ImportError:
        return False


#: 是否已经尝试创建 QApplication（避免重复创建导致 Qt 报错）
_QT_APP = None


@pytest.fixture(autouse=True)
def _never_write_user_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试**绝不能**改写项目根的 ``config.json``。

    ``MainWindow.closeEvent()`` 会调用 ``config.save()``；测试里构造/关闭主窗口
    （``test_m6_view_fixes.py`` 有两处）就会把当时的配置写回磁盘。
    后果不只是"弄脏工作区"：如果测试为了确定性改过某个配置项
    （例如把曲谱渲染后端钉成 ``native``），关闭窗口时那个值会被**持久化**，
    悄悄覆盖掉代码里的默认值 —— 下一次正常启动就会用错后端。
    真实踩过一次：跑完 pytest 后 ``config.json`` 里的 ``score_backend`` 被写回。
    """
    from zpymusic.common.config import AppConfig

    monkeypatch.setattr(AppConfig, "save", lambda self, path=None: path or TMP_ROOT)


@pytest.fixture(scope="session")
def qapp():
    """会话级 ``QApplication``。

    **必须提供**：任何用到 ``QSvgRenderer`` / ``QPainter`` / ``QGraphicsScene``
    的测试都需要一个 QApplication —— 没有它时 Qt 会在 C++ 层直接
    access violation（进程崩溃、pytest 连汇总都打不出来），而不是抛 Python 异常。
    实测踩过这个坑。

    无显示环境需要先把 ``QT_QPA_PLATFORM`` 设为 ``offscreen``。
    """
    global _QT_APP
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ANN001
    """收尾时清理临时根目录。"""
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
