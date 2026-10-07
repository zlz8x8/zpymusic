"""``zpymusic.common`` —— 配置、路径、日志、外部依赖探测、错误类型。"""

from __future__ import annotations

from .config import (
    AppConfig,
    AudioConfig,
    PathsConfig,
    RenderConfig,
    UiConfig,
    load_config,
)
from .errors import (
    AudioRenderError,
    DependencyMissingError,
    MusicXMLError,
    RenderError,
    SuiteError,
    SyncSchemaError,
    TaskCancelled,
    ZpyMusicError,
)
from .log import get_logger, setup_logging

__all__ = [
    "AppConfig",
    "AudioConfig",
    "PathsConfig",
    "RenderConfig",
    "UiConfig",
    "load_config",
    "ZpyMusicError",
    "DependencyMissingError",
    "MusicXMLError",
    "RenderError",
    "AudioRenderError",
    "SuiteError",
    "SyncSchemaError",
    "TaskCancelled",
    "get_logger",
    "setup_logging",
]
