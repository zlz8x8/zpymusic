"""项目路径解析。

项目根目录约定：``<project_root>/src/zpymusic/common/paths.py`` → 上溯 4 层。
所有数据目录（``sound`` / ``staff`` / ``tools`` / ``config.json`` / ``logs``）都相对项目根。
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "PROJECT_ROOT",
    "SOUND_DIR",
    "STAFF_DIR",
    "SUITES_DIR",
    "TOOLS_DIR",
    "FLUIDSYNTH_DIR",
    "LOGS_DIR",
    "CONFIG_FILE",
    "MUSICXML_DIR",
    "MUSICXML_SUFFIXES",
    "TMP_DIR",
    "PREVIEW_DIR",
]

PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
"""项目根目录（即包含 ``sound/`` 与 ``staff/`` 的那一层）。"""

SOUND_DIR: Path = PROJECT_ROOT / "sound"
STAFF_DIR: Path = PROJECT_ROOT / "staff"
SUITES_DIR: Path = STAFF_DIR / "suites"
MUSICXML_DIR: Path = STAFF_DIR / "musicxml"
TOOLS_DIR: Path = PROJECT_ROOT / "tools"
FLUIDSYNTH_DIR: Path = TOOLS_DIR / "fluidsynth"
LOGS_DIR: Path = PROJECT_ROOT / "logs"
CONFIG_FILE: Path = PROJECT_ROOT / "config.json"

TMP_DIR: Path = PROJECT_ROOT / ".zpy-tmp"
"""项目内临时目录。

**不要用系统 TEMP**：受限文件沙箱下写系统临时目录会被拒绝（实测 ``PermissionError``），
WebEngine 的探测脚本也因此放在这里（见 :func:`zpymusic.sync.web_view._probe_temp_root`）。
"""
PREVIEW_DIR: Path = TMP_DIR / "preview"
"""源文件预览的缓存目录（渲染出的 SVG / 合成的试听 WAV）。"""

MUSICXML_SUFFIXES: frozenset[str] = frozenset({".xml", ".musicxml", ".mxl"})
"""被视为 MusicXML 源文件的扩展名。"""
