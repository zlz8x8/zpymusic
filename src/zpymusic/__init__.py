"""zpyMusic —— MusicXML 曲谱转换与 SVG 同步播放工具。

模块划分（见 docs/requirements.md §6）：
  * ``common`` : 配置、日志、路径、外部工具探测、错误类型
  * ``core``   : 不依赖 Qt 的转换核心（可 CLI 复用、可单测）
  * ``sync``   : 时间轴与高亮逻辑（M4 使用）
  * ``ui``     : PySide6 界面
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
