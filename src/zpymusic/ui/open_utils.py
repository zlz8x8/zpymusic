"""用系统默认程序打开文件 / 目录，以及在资源管理器里定位一个文件。

需求 F3.6：结果区要提供"打开所在文件夹"。主窗口与「格式转换」页都要用，
因此从 :mod:`zpymusic.ui.main_window` 里抽出来共用。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..common.log import get_logger

log = get_logger(__name__)

__all__ = ["open_path", "reveal_in_folder"]


def open_path(path: Path, log_dock=None) -> bool:  # noqa: ANN001
    """用系统默认方式打开文件或目录（失败只记日志，不抛异常）。"""
    p = Path(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return True
    except OSError as e:
        _say(log_dock, "error", f"无法打开 {p}：{e}")
        return False


def reveal_in_folder(path: Path, log_dock=None) -> bool:  # noqa: ANN001
    """在文件管理器里打开该文件所在的文件夹（Windows 下并选中它）。"""
    p = Path(path)
    folder = p if p.is_dir() else p.parent
    if not folder.is_dir():
        _say(log_dock, "warning", f"文件夹不存在：{folder}")
        return False
    if sys.platform.startswith("win"):
        try:
            # explorer 的 /select 需要反斜杠路径；它对"成功"也返回非 0，故不检查返回码
            subprocess.Popen(["explorer", f"/select,{p}"])
            return True
        except OSError as e:  # 退化：直接打开文件夹
            _say(log_dock, "warning", f"无法定位到文件，改为打开文件夹：{e}")
    return open_path(folder, log_dock)


def _say(log_dock, level: str, text: str) -> None:  # noqa: ANN001
    log.warning("%s", text)
    add_note = getattr(log_dock, "add_note", None)
    if callable(add_note):
        add_note(level, text)
