"""外部依赖探测（需求 §3.3、风险 R1）。

设计要点：
  * **不抛异常**，只返回探测结果，让 UI 决定怎么呈现（缺失项以警告显示，不阻塞启动）。
  * FluidSynth 需要显式把 DLL 目录加入搜索路径：本机实测 ``import fluidsynth`` 会因
    找不到 ``libfluidsynth-3.dll`` 而失败，因此不依赖 ``import``，改由本模块用 ctypes 加载。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .paths import FLUIDSYNTH_DIR, PROJECT_ROOT

__all__ = [
    "Dependency",
    "DependencyReport",
    "default_musescore",
    "default_ffmpeg",
    "default_fluidsynth_dir",
    "find_fluidsynth_dll",
    "prepare_fluidsynth",
    "probe_all",
    "require",
]

_MUSESCORE_CANDIDATES = (
    Path(r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe"),
    Path(r"C:\Program Files\MuseScore 3\bin\MuseScore3.exe"),
    Path(r"C:\Program Files (x86)\MuseScore 4\bin\MuseScore4.exe"),
)
_FFMPEG_CANDIDATES = (
    Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
    Path(r"C:\ffmpeg\ffmpeg.exe"),
)
_FLUIDSYNTH_DIR_CANDIDATES = (
    FLUIDSYNTH_DIR,  # <project_root>/tools/fluidsynth —— 推荐位置
    Path(r"C:\ffmpeg\fluidsynth"),
    Path(r"C:\Program Files\FluidSynth\bin"),
    Path(r"C:\Program Files (x86)\FluidSynth\bin"),
)
_FLUIDSYNTH_DLL_NAMES = ("libfluidsynth-3.dll", "libfluidsynth-2.dll", "libfluidsynth.dll")


@dataclass(frozen=True)
class Dependency:
    """单个依赖的探测结果。"""

    name: str
    available: bool
    version: str = ""
    path: Path | None = None
    hint: str = ""
    required: bool = False

    def describe(self) -> str:
        state = "OK" if self.available else ("缺失" if self.required else "不可用")
        loc = f" @ {self.path}" if self.path else ""
        ver = f" v{self.version}" if self.version else ""
        text = f"[{state}] {self.name}{ver}{loc}"
        if not self.available and self.hint:
            text += f" —— {self.hint}"
        return text


@dataclass
class DependencyReport:
    """全部依赖的探测结果集合。"""

    items: list[Dependency] = field(default_factory=list)

    def get(self, name: str) -> Dependency | None:
        for it in self.items:
            if it.name == name:
                return it
        return None

    @property
    def missing_required(self) -> list[Dependency]:
        return [i for i in self.items if i.required and not i.available]

    @property
    def warnings(self) -> list[Dependency]:
        return [i for i in self.items if not i.available]

    def summary_lines(self) -> list[str]:
        return [i.describe() for i in self.items]


# --------------------------------------------------------------------------- 探测辅助
def default_musescore(candidates: Iterable[Path] = _MUSESCORE_CANDIDATES) -> Path | None:
    """返回可用的 MuseScore 可执行文件，用于生成 PDF（需求 §5.4.1）。"""
    for c in candidates:
        if c.is_file():
            return c
    found = shutil.which("MuseScore4") or shutil.which("mscore") or shutil.which("musescore")
    return Path(found) if found else None


def default_ffmpeg(candidates: Iterable[Path] = _FFMPEG_CANDIDATES) -> Path | None:
    for c in candidates:
        if c.is_file():
            return c
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def find_fluidsynth_dll(search_dirs: Iterable[Path] = _FLUIDSYNTH_DIR_CANDIDATES) -> Path | None:
    """在候选目录中查找 libfluidsynth 动态库。"""
    for d in search_dirs:
        if not d.is_dir():
            continue
        for name in _FLUIDSYNTH_DLL_NAMES:
            f = d / name
            if f.is_file():
                return f
    return None


def default_fluidsynth_dir() -> Path | None:
    dll = find_fluidsynth_dll()
    return dll.parent if dll else None


def _dll_version(dll_path: Path) -> str:
    """读取 libfluidsynth 版本号；失败返回空串。

    注意：``fluid_version`` 是 ``void fluid_version(int*, int*, int*)``，
    必须传三个指针，否则会 access violation（本机实测踩过）。
    """
    try:
        lib = ctypes.WinDLL(str(dll_path))
        major, minor, micro = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        lib.fluid_version(ctypes.byref(major), ctypes.byref(minor), ctypes.byref(micro))
        return f"{major.value}.{minor.value}.{micro.value}"
    except Exception:  # noqa: BLE001 - 探测阶段任何异常都只意味着"不可用"
        return ""


def prepare_fluidsynth(search_dir: Path | None = None) -> tuple[bool, str, Path | None]:
    """把 FluidSynth DLL 目录加入搜索路径，并尝试加载。

    Returns:
        ``(ok, version, dll_path)``。``ok=False`` 时其余字段可能为空。

    必须在 ``import fluidsynth`` **之前**调用。
    """
    dll = find_fluidsynth_dll([search_dir] if search_dir else _FLUIDSYNTH_DIR_CANDIDATES)
    if dll is None:
        return False, "", None
    d = str(dll.parent)
    # 1) 保证依赖 DLL（SDL3.dll / sndfile.dll）能被找到
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(d)  # Python 3.8+ Windows
    except (AttributeError, OSError):
        pass
    version = _dll_version(dll)
    if not version:
        return False, "", dll
    try:
        lib = ctypes.WinDLL(str(dll))
        lib.fluid_synth_set_sample_rate  # 触发一次属性查找，确认导出符号可用
    except Exception:  # noqa: BLE001
        return False, version, dll
    return True, version, dll


# --------------------------------------------------------------------------- 顶层探测
def probe_all(
    *,
    project_root: Path = PROJECT_ROOT,
    fluidsynth_dir: Path | None = None,
    ffmpeg: Path | None = None,
    musescore: Path | None = None,
) -> DependencyReport:
    """探测全部外部依赖，返回报告。**不抛异常。**"""
    report = DependencyReport()

    # --- verovio -----------------------------------------------------------
    try:
        import verovio  # noqa: PLC0415

        tk = verovio.toolkit()
        raw = tk.getVersion()  # 形如 "6.3.0[undefined]"
        version = raw.split("[")[0].strip()
        report.items.append(Dependency("verovio", True, version, required=True))
    except Exception as e:  # noqa: BLE001
        report.items.append(
            Dependency(
                "verovio",
                False,
                hint=f"pip install verovio（{type(e).__name__}: {e}）",
                required=True,
            )
        )

    # --- FluidSynth --------------------------------------------------------
    ok, version, dll = prepare_fluidsynth(fluidsynth_dir)
    if ok:
        report.items.append(Dependency("fluidsynth", True, version, dll, required=True))
    else:
        report.items.append(
            Dependency(
                "fluidsynth",
                False,
                hint=(
                    "未找到 libfluidsynth 动态库。请下载 FluidSynth 官方 Windows 版，"
                    f"把 libfluidsynth-3.dll / SDL3.dll / sndfile.dll 放入 {FLUIDSYNTH_DIR}"
                ),
                required=True,
            )
        )

    # --- ffmpeg ------------------------------------------------------------
    ff = ffmpeg or default_ffmpeg()
    if ff:
        report.items.append(Dependency("ffmpeg", True, _tool_version(ff), ff))
    else:
        report.items.append(
            Dependency("ffmpeg", False, hint="未找到 ffmpeg.exe，wav→mp3 不可用")
        )

    # --- MuseScore 4 -------------------------------------------------------
    ms = musescore or default_musescore()
    if ms:
        report.items.append(Dependency("musescore", True, _tool_version(ms), ms))
    else:
        report.items.append(
            Dependency("musescore", False, hint="未找到 MuseScore 4，PDF 导出不可用")
        )
    # --- music21（反向转换用） ---------------------------------------------
    try:
        import music21  # noqa: PLC0415

        report.items.append(Dependency("music21", True, music21.__version__))
    except Exception as e:  # noqa: BLE001
        report.items.append(
            Dependency("music21", False, hint=f"pip install music21（{type(e).__name__}）")
        )

    # --- 音色库 ------------------------------------------------------------
    from .paths import SOUND_DIR  # noqa: PLC0415

    fonts = sorted(SOUND_DIR.glob("*.sf2")) + sorted(SOUND_DIR.glob("*.sf3"))
    if fonts:
        report.items.append(
            Dependency("soundfonts", True, f"{len(fonts)} 个", SOUND_DIR)
        )
    else:
        report.items.append(
            Dependency(
                "soundfonts",
                False,
                hint=f"未在 {SOUND_DIR} 找到 .sf2/.sf3 音色库，无法渲染 MP3",
            )
        )

    return report


def _tool_version(exe: Path) -> str:
    """尽力获取工具版本号，失败返回空串。

    **不调用子进程**（早期版本会执行 ``exe --version``，实测这会引发数据竞争：
    探测跑在工作线程、同时主线程创建 ``QSvgRenderer``，导致 QtSvg access violation）。
    改为读取 exe 所在的安装目录名（MuseScore 的 ``bin`` 上级目录名即版本）。
    """
    parts = [p for p in exe.parts if p]
    for part in reversed(parts[:-1]):
        if any(ch.isdigit() for ch in part) and len(part) <= 24:
            return part
    return ""


def require(name: str, report: DependencyReport) -> Dependency:
    """从报告中取出依赖，不可用则抛 DependencyMissingError。"""
    from .errors import DependencyMissingError  # noqa: PLC0415

    dep = report.get(name)
    if dep is None or not dep.available:
        hint = dep.hint if dep else "未探测"
        raise DependencyMissingError(name, hint)
    return dep
