"""zpyMusic 异常类型。"""

from __future__ import annotations


class ZpyMusicError(Exception):
    """所有 zpyMusic 异常的基类。"""


class DependencyMissingError(ZpyMusicError):
    """外部依赖（引擎 / 工具 / DLL）缺失。

    Attributes:
        name: 依赖名，如 ``"fluidsynth"``。
        hint: 面向用户的可操作提示。
    """

    def __init__(self, name: str, hint: str = "") -> None:
        self.name = name
        self.hint = hint
        msg = f"缺少依赖：{name}"
        if hint:
            msg += f"（{hint}）"
        super().__init__(msg)


class MusicXMLError(ZpyMusicError):
    """MusicXML 解析 / 规范化失败。"""


class RenderError(ZpyMusicError):
    """SVG / MIDI 渲染失败。"""


class AudioRenderError(ZpyMusicError):
    """音频渲染或编码失败。"""


class SuiteError(ZpyMusicError):
    """套件读写 / 校验失败。"""


class SyncSchemaError(ZpyMusicError):
    """sync.json 不满足契约。"""


class TaskCancelled(ZpyMusicError):
    """用户取消任务。"""
