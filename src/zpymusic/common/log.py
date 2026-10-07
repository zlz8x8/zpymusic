"""日志配置（需求 §7.2）。

输出三处：
  * GUI 日志区（由 ``ui.log_dock`` 安装的 handler）
  * ``<project_root>/logs/zpymusic_YYYYMMDD.log``（按天，保留 7 天）
  * stderr

用法::

    from zpymusic.common.log import setup_logging, get_logger
    setup_logging()
    log = get_logger(__name__)
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path

from .paths import LOGS_DIR

__all__ = ["setup_logging", "get_logger", "LOG_FORMAT", "DATE_FORMAT", "log_file_path"]

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-28s %(message)s"
DATE_FORMAT = "%H:%M:%S"
_CONSOLE_FORMAT = "%(levelname)-7s %(name)s: %(message)s"

_configured = False


def log_file_path(when: datetime | None = None) -> Path:
    when = when or datetime.now()
    return LOGS_DIR / f"zpymusic_{when:%Y%m%d}.log"


def setup_logging(level: int = logging.INFO, *, to_file: bool = True) -> Path | None:
    """配置根 logger。重复调用是安全的（幂等）。

    Returns:
        日志文件路径；``to_file=False`` 或目录不可写时返回 ``None``。
    """
    global _configured
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    if _configured:
        return log_file_path() if to_file else None

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    root.addHandler(console)

    path: Path | None = None
    if to_file:
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            path = log_file_path()
            fh = logging.handlers.TimedRotatingFileHandler(
                path, when="midnight", backupCount=7, encoding="utf-8"
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
            root.addHandler(fh)
        except OSError:
            root.warning("无法创建日志文件，仅输出到控制台", exc_info=False)
            path = None

    # 第三方库降噪
    for noisy in ("verovio", "music21", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    return path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
