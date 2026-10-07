"""格式转换引擎（需求 §5.4 F3.*）—— **M5 实现**。不导入 Qt。

能力矩阵来自 ``docs/requirements.md §5.4.1``，``cli.py`` 与本模块共用（CLI 只是它的薄壳）。
三种转换形态：

1. **套件形态**：``musicxml → svg / midi / mp3 / wav`` —— 复用「生成套件」流水线
   （需求 §5.4.1 明确要求复用），产物落在 ``<目标目录>/<主名>/`` 里：既有独立可用的
   ``.svg/.mid/.mp3/.wav``，也有 ``sync.json`` + 行 SVG，因此可以直接"载入播放界面"。
2. **单文件形态**：``musicxml → pdf``、``midi → musicxml``、``midi → mp3/wav``、
   ``mp3 ↔ wav``、``svg → musicxml``、``pdf → svg`` —— 产物就是目标目录下的一个文件。
3. **拒绝形态**：P3（``mp3 → musicxml``、``pdf → musicxml``）与依赖缺失的情况，
   返回 ``failed`` + 可操作的原因，绝不静默产出错文件。

本机实测（2026-02，用于选择实现方式）：

===========  ==========================================================  ============
转换          命令 / API                                                  实测
===========  ==========================================================  ============
→ pdf        ``MuseScore4.exe -f -o out.pdf in.musicxml``                0.9 s / 60 KB
→ musicxml   ``music21.converter.parse(mid)`` + ``write("musicxml")``    0.3 s / 131 KB
→ wav        ``fluidsynth -ni -a file …``                                27–28× 实时
→ mp3        上面 + ``ffmpeg -b:a 192k``                                 秒级
===========  ==========================================================  ============

设计取舍：

* **MIDI 由 FluidSynth 合成时不会重写 Program Change**：FluidSynth 会按 MIDI 里的事件走，
  但很多工具导出的 MIDI 里没有 Program（全部落到钢琴）—— 因此 :func:`requirements_for`
  之外还会在结果里给出"检测到的通道-音色"警告与替代做法提示（需求 §5.4.1 的陷阱说明）。
* **``svg → musicxml`` 只认本工具生成的 SVG**：SVG 里没有完整的记谱信息，真正可靠的做法是
  复用同一套件里保留的 MusicXML 侧车文件（本工具生成套件时会复制源 MusicXML）；
  否则明确拒绝并说明原因（需求 §5.4.1）。
* **``pdf → svg`` 依赖 pypdf 提取内嵌 MusicXML**：无 pypdf / 无内嵌乐谱时拒绝并给出
  ``pip install pypdf``（或 pypdfium2 位图方案，非矢量）的提示 —— 本机没装这两个包，
  因此这条路径只做"依赖自检 + 明确拒绝"，不产出低质量结果。
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..common.config import AppConfig
from ..common.errors import ZpyMusicError
from ..common.log import get_logger
from ..common.paths import PROJECT_ROOT, TMP_DIR
from .audio_render import midi_to_wav, resolve_tools, wav_to_mp3
from .musicxml_io import read_musicxml
from .pipeline import GenerateOptions, generate_suite
from .score_render import render_svgs
from .suite import OverwritePolicy, Suite, SuiteLayout, discover_suites, make_layout

log = get_logger(__name__)

__all__ = [
    "CONVERSIONS",
    "SOURCE_FORMATS",
    "TARGET_FORMATS",
    "CONVERTIBLE_SUFFIXES",
    "detect_format",
    "format_suffix",
    "iter_convertible",
    "is_suite_form",
    "ConvertOptions",
    "ConvertResult",
    "Requirement",
    "ConvertError",
    "DEFAULT_NAME_TEMPLATE",
    "NAME_TEMPLATE_TOKENS",
    "FIDELITY_PROMPTS",
    "convert_one",
    "requirements_for",
    "missing_requirements",
    "fidelity_note",
    "is_lossy",
    "build_output_name",
    "plan_output",
    "check_environment",
    "find_suite",
    "suite_layout_of",
]

# --------------------------------------------------------------------------- 能力矩阵
#: ``(源格式, 目标格式) → (优先级, 实现方式)``（需求 §5.4.1）
CONVERSIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("musicxml", "svg"): ("P0", "Verovio 渲染（含音符 ID 与时间轴）"),
    ("musicxml", "midi"): ("P0", "Verovio 导出"),
    ("musicxml", "mp3"): ("P0", "Verovio → FluidSynth → ffmpeg"),
    ("musicxml", "wav"): ("P1", "Verovio → FluidSynth"),
    ("musicxml", "pdf"): ("P1", "MuseScore 4 命令行导出"),
    ("midi", "musicxml"): ("P1", "music21（推断性，小节/声部靠猜）"),
    ("midi", "mp3"): ("P1", "FluidSynth + ffmpeg（按 MIDI 内 Program 选音色）"),
    ("midi", "wav"): ("P2", "FluidSynth"),
    ("svg", "musicxml"): ("P2", "仅支持本工具生成的 SVG（需旁路 MEI）"),
    ("pdf", "svg"): ("P2", "有文本层的 PDF 可提取矢量；内嵌 MusicXML 优先"),
    ("mp3", "wav"): ("P2", "ffmpeg"),
    ("wav", "mp3"): ("P2", "ffmpeg"),
    ("mp3", "musicxml"): ("P3", "不做：需自动转谱（AMT），见需求 §7.4"),
    ("pdf", "musicxml"): ("P3", "不做：需 OMR，建议外部工具桥接"),
}

SOURCE_FORMATS = ("musicxml", "midi", "svg", "pdf", "mp3", "wav")
TARGET_FORMATS = ("svg", "midi", "mp3", "wav", "pdf", "musicxml")

#: 目标格式 → 扩展名
_SUFFIX: dict[str, str] = {
    "svg": ".svg",
    "midi": ".mid",
    "mp3": ".mp3",
    "wav": ".wav",
    "pdf": ".pdf",
    "musicxml": ".musicxml",
}

_EXT_TO_FORMAT = {
    ".xml": "musicxml", ".musicxml": "musicxml", ".mxl": "musicxml",
    ".mid": "midi", ".midi": "midi",
    ".svg": "svg", ".pdf": "pdf", ".mp3": "mp3", ".wav": "wav",
}

#: 命名模板里可用的占位符（F3.7）
NAME_TEMPLATE_TOKENS = ("{base}", "{format}", "{ext}", "{date}")
DEFAULT_NAME_TEMPLATE = "{base}"

#: 套件形态的目标格式
SUITE_TARGETS = frozenset({"svg", "midi", "mp3", "wav"})


def is_suite_form(sfmt: str, tfmt: str) -> bool:
    """该转换是否走"套件形态"（复用生成套件流水线，产物是一个套件目录）。

    **只有 ``musicxml`` 作源**才这样：``midi → wav``、``mp3 → wav`` 等是单文件转换，
    即使目标格式同名也不能当成套件（早期版本只按目标格式判断，导致
    ``midi → wav`` 把输出目录当成了套件目录、最终 ``PermissionError``）。
    """
    return sfmt == "musicxml" and tfmt in SUITE_TARGETS

_TEMPLATE_RE = re.compile(r"\{([a-z_]+)\}")
_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def detect_format(path: Path) -> str:
    """按扩展名判断源格式（不认识返回空串）。"""
    return _EXT_TO_FORMAT.get(Path(path).suffix.lower(), "")


def format_suffix(fmt: str) -> str:
    """目标格式的扩展名（``midi`` → ``.mid``）。"""
    return _SUFFIX.get(fmt, f".{fmt}")


#: 本工具认识的全部源扩展名（"添加目录"用它筛文件）
CONVERTIBLE_SUFFIXES: frozenset[str] = frozenset(_EXT_TO_FORMAT)


def iter_convertible(folder: Path | str, *, recursive: bool = False) -> list[Path]:
    """列出目录下所有**可转换**的源文件（MusicXML / MIDI / SVG / PDF / MP3 / WAV）。

    与 :func:`~zpymusic.core.musicxml_io.iter_source_files` 的区别：后者只认 MusicXML
    （生成套件用），这里要把音频与 SVG 也算进来（格式转换页的"添加目录"用）。
    """
    d = Path(folder)
    if not d.is_dir():
        return []
    it = d.rglob("*") if recursive else d.glob("*")
    files = [p for p in it if p.is_file() and p.suffix.lower() in CONVERTIBLE_SUFFIXES]
    return sorted(files, key=lambda p: p.name.lower())


class ConvertError(ZpyMusicError):
    """转换失败（消息面向用户，可直接显示）。"""


# --------------------------------------------------------------------------- 依赖自检
@dataclass(frozen=True)
class Requirement:
    """一项依赖的运行时就绪状态（F3.2）。"""

    name: str
    ok: bool
    hint: str = ""

    def describe(self) -> str:
        """``✓ verovio`` / ``✗ pypdf（pip install pypdf…）``（提示只在缺失时显示）。"""
        if self.ok:
            return f"✓ {self.name}"
        return f"✗ {self.name}" + (f"（{self.hint}）" if self.hint else "")


def _has_module(name: str) -> bool:
    """只查"装没装"，**不 import** —— music21 的 import 要几百毫秒，不能放在 UI 线程。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_environment(config: AppConfig) -> dict[str, Requirement]:
    """探测转换需要的全部外部能力（每个 key 只查一次，UI 缓存用）。"""
    paths = config.paths.resolved()
    tools = resolve_tools(
        Path(paths.ffmpeg) if paths.ffmpeg else None,
        Path(paths.fluidsynth_dir) if paths.fluidsynth_dir else None,
    )
    musescore = paths.musescore
    soundfont = config.audio.soundfont_path(PROJECT_ROOT)
    return {
        "verovio": Requirement("verovio", _has_module("verovio"), "pip install verovio"),
        "fluidsynth": Requirement("fluidsynth", tools.can_render_wav,
                                  "把 fluidsynth.exe 放入 tools/fluidsynth"),
        "ffmpeg": Requirement("ffmpeg", tools.can_encode_mp3, "需要 ffmpeg.exe（mp3 编码）"),
        "musescore": Requirement("musescore", bool(musescore and Path(musescore).is_file()),
                                 "未找到 MuseScore 4，无法导出 PDF"),
        "music21": Requirement("music21", _has_module("music21"),
                               "pip install music21（MIDI → MusicXML）"),
        "pypdf": Requirement("pypdf", _has_module("pypdf"),
                             "pip install pypdf（提取 PDF 内嵌 MusicXML）"),
        "soundfont": Requirement("soundfont", soundfont.is_file(),
                                 f"未找到音色库 {soundfont.name}"),
    }


def requirements_for(sfmt: str, tfmt: str, env: dict[str, Requirement] | None = None,
                     config: AppConfig | None = None) -> list[Requirement]:
    """该转换需要哪些依赖（F3.2：缺哪个就禁止开始并说明）。"""
    if env is None:
        if config is None:
            raise ValueError("requirements_for 需要 env 或 config 之一")
        env = check_environment(config)

    def need(*names: str) -> list[Requirement]:
        return [env[n] for n in names if n in env]

    if sfmt == "musicxml" and tfmt == "svg":
        return need("verovio")
    if sfmt == "musicxml" and tfmt == "midi":
        return need("verovio")
    if sfmt == "musicxml" and tfmt == "mp3":
        return need("verovio", "fluidsynth", "soundfont", "ffmpeg")
    if sfmt == "musicxml" and tfmt == "wav":
        return need("verovio", "fluidsynth", "soundfont")
    if sfmt == "musicxml" and tfmt == "pdf":
        return need("musescore")
    if sfmt == "midi" and tfmt == "musicxml":
        return need("music21")
    if sfmt == "midi" and tfmt in {"wav", "mp3"}:
        base = need("fluidsynth", "soundfont")
        return base + need("ffmpeg") if tfmt == "mp3" else base
    if sfmt in {"mp3", "wav"}:
        return need("ffmpeg")
    if sfmt == "svg" and tfmt == "musicxml":
        return []  # 需要侧车 MusicXML，运行时再判断（见 _svg_to_musicxml）
    if sfmt == "pdf" and tfmt == "svg":
        return need("pypdf", "verovio")
    return []


def missing_requirements(
    sfmt: str, tfmt: str, config: AppConfig, env: dict[str, Requirement] | None = None
) -> list[Requirement]:
    """不满足的依赖列表（空 = 可以开始转换）。"""
    return [r for r in requirements_for(sfmt, tfmt, env, config) if not r.ok]


# --------------------------------------------------------------------------- 保真度提示（F3.3）
_LOSSY_NOTES: dict[tuple[str, str], str] = {
    ("musicxml", "midi"): "⚠ 有损：记谱、连音线、歌词、力度、排版都会丢失（需求 §1.2 第 2 条）。",
    ("musicxml", "mp3"): "⚠ 有损：记谱、连音线、歌词、力度、排版都会丢失；音色取决于音色库。",
    ("musicxml", "wav"): "⚠ 有损：记谱信息全部丢失，只保留演奏信息。",
    ("midi", "musicxml"): "⚠ 推断性：小节 / 拍号 / 调号 / 声部由工具猜测，不是「还原」。",
    ("midi", "mp3"): "⚠ 试听质量取决于 MIDI 内的 Program Change 与音色库。",
    ("midi", "wav"): "⚠ 试听质量取决于 MIDI 内的 Program Change 与音色库。",
    ("svg", "musicxml"): "⚠ 推断性：仅支持本工具生成的 SVG，且依赖同套件保留的 MusicXML。",
    ("pdf", "svg"): "⚠ 仅当 PDF 内嵌 MusicXML 时能保持矢量；否则需要 pypdfium2 位图方案。",
    ("musicxml", "pdf"): "排版由 MuseScore 4 重新生成，与播放界面看到的行号/小节位置可能不同。",
    ("mp3", "wav"): "无损（仅转封装/重编码，WAV 体积会大得多）。",
    ("wav", "mp3"): "有损压缩（默认 192 kbps）。",
}

#: 需求 F3.3 点名的"需要一次性保真度提示"的组合
FIDELITY_PROMPTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("musicxml", "midi"),
        ("musicxml", "mp3"),
        ("midi", "musicxml"),
        ("svg", "musicxml"),
    }
)


def fidelity_note(sfmt: str, tfmt: str) -> str:
    """转换前提示（F3.3）：会丢什么 / 会推断什么；没有需要说明的返回空串。"""
    return _LOSSY_NOTES.get((sfmt, tfmt), "")


def is_lossy(sfmt: str, tfmt: str) -> bool:
    """是否需要弹一次"保真度提示"对话框（只对需求点名的组合打扰用户）。"""
    return (sfmt, tfmt) in FIDELITY_PROMPTS


# --------------------------------------------------------------------------- 选项 / 结果
@dataclass
class ConvertOptions:
    """一次转换的参数。"""

    target: str
    out_dir: Path | None = None
    """目标目录；``None`` = 源文件所在目录。"""
    name_template: str = DEFAULT_NAME_TEMPLATE
    """F3.7 命名模板（``{base}`` / ``{format}`` / ``{ext}`` / ``{date}``）。"""
    overwrite: str = OverwritePolicy.SKIP
    """``skip`` 跳过已存在；``overwrite`` 覆盖；``newdir`` 追加序号（转换没有目录概念，
    因此退化为"带序号的文件名"，见 :func:`plan_output`）。"""
    soundfont: Path | None = None
    quantize: bool = True
    """``midi → musicxml`` 是否做量化（把演奏时间对齐到记谱网格）。"""
    keep_wav: bool = False
    """``musicxml → mp3`` 时是否保留中间 WAV。"""
    also_svg: bool = False
    """``musicxml → midi`` 时是否同时产出 SVG（复用套件流水线时用）。"""


@dataclass
class ConvertResult:
    """一次转换的产物与诊断。"""

    source: Path
    target: str
    status: str = "pending"
    """``ok`` | ``skipped`` | ``failed`` | ``cancelled``"""
    outputs: list[Path] = field(default_factory=list)
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    suite_dir: Path | None = None
    """套件形态转换产出的套件目录（F3.6"载入播放界面"用它）。"""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def playable(self) -> bool:
        """是否产出了可直接载入播放界面的套件。

        播放界面用 ``QMediaPlayer``，而它**放不了 .mid**（M5 实测 ``FormatError``），
        因此只有产物里带 MP3/WAV 才算"能播放"。
        """
        if self.suite_dir is None:
            return False
        return any(p.suffix.lower() in {".mp3", ".wav"} for p in self.outputs)

    def summary(self) -> str:
        if self.status == "ok":
            return f"{self.source.name} → {self.target}：{len(self.outputs)} 个产物（{self.elapsed_s:.1f}s）"
        return f"{self.source.name} → {self.target}：{self.status} —— {self.message}"

    def to_dict(self) -> dict:
        return {
            "source": str(self.source),
            "target": self.target,
            "status": self.status,
            "message": self.message,
            "outputs": [str(p) for p in self.outputs],
            "elapsed_s": round(self.elapsed_s, 3),
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- 命名 / 路径（F3.7）
def build_output_name(source_base: str, target: str, template: str = DEFAULT_NAME_TEMPLATE) -> str:
    """按模板算出输出主名（不含扩展名）。

    未知占位符会抛 :class:`ConvertError`（UI 上给出可行提示，而不是静默生成怪名字）；
    结果里的路径分隔符与 Windows 非法字符会被替换成 ``_``。
    """
    tmpl = (template or DEFAULT_NAME_TEMPLATE).strip() or DEFAULT_NAME_TEMPLATE
    unknown = [t for t in _TEMPLATE_RE.findall(tmpl) if f"{{{t}}}" not in NAME_TEMPLATE_TOKENS]
    if unknown:
        raise ConvertError(
            f"命名模板里有未知占位符：{'、'.join('{'+u+'}' for u in unknown)}；"
            f"可用：{' '.join(NAME_TEMPLATE_TOKENS)}"
        )
    name = (
        tmpl.replace("{base}", source_base)
        .replace("{format}", target)
        .replace("{ext}", format_suffix(target).lstrip("."))
        .replace("{date}", datetime.now().strftime("%Y%m%d"))
    )
    name = _BAD_NAME_CHARS.sub("_", name).strip(" .")
    if not name:
        raise ConvertError("命名模板生成的文件名为空，请检查模板")
    return name


def _numbered(path: Path) -> Path:
    """``newdir`` 策略：找一个没被占用的带序号文件名。"""
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise ConvertError(f"目标目录里同名文件太多：{path.name}")


def plan_output(source: Path, options: ConvertOptions) -> tuple[Path, bool]:
    """算出主产物的最终路径。

    套件形态（``musicxml → svg/midi/mp3/wav``）的"产物"是**一个目录**；
    其余转换是**一个文件**。两者都按 ``overwrite`` 策略处理。

    Returns:
        ``(路径, 是否跳过)`` —— ``skip`` 策略下目标已存在时返回 ``(原路径, True)``。
    """
    out_dir = Path(options.out_dir) if options.out_dir else Path(source).parent
    base = build_output_name(Path(source).stem, options.target, options.name_template)
    if is_suite_form(detect_format(source), options.target):
        path = out_dir / base  # 套件目录
    else:
        path = out_dir / f"{base}{format_suffix(options.target)}"
    if not path.exists():
        return path, False
    if options.overwrite == OverwritePolicy.OVERWRITE:
        return path, False
    if options.overwrite == OverwritePolicy.NEWDIR:
        return _numbered(path), False
    return path, True


def _tmp_dir(source: Path) -> Path:
    """转换用的临时目录（中间 WAV / 规范化 MusicXML 等）。"""
    key = re.sub(r"[^0-9A-Za-z_.-]", "_", Path(source).stem)[:40]
    d = TMP_DIR / "convert" / f"{key}-{abs(hash(str(Path(source).resolve()))) % 100000:05d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rename_suite(suite_dir: Path, new_base: str) -> Path:
    """把套件目录与内部同名产物一起改名（让 F3.7 命名模板对套件形态也生效）。

    ``sync.json`` 里记录的是**原始**源文件名与相对 ``svg/`` 路径，因此不需要改内容。
    """
    old_base = suite_dir.name
    if new_base == old_base:
        return suite_dir
    target = suite_dir.with_name(new_base)
    if target.exists():
        target = _numbered_dir(target)
    suite_dir.rename(target)
    for f in sorted(target.iterdir()):
        if f.is_file() and f.name.startswith(old_base + "."):
            f.rename(target / (new_base + f.name[len(old_base):]))
    return target


def _numbered_dir(path: Path) -> Path:
    for i in range(2, 1000):
        cand = path.with_name(f"{path.name}_{i}")
        if not cand.exists():
            return cand
    raise ConvertError(f"目标目录里同名文件夹太多：{path.name}")


# --------------------------------------------------------------------------- 主入口
ProgressFn = Callable[[str, int, int], None]


def convert_one(
    source: Path | str,
    options: ConvertOptions,
    config: AppConfig,
    *,
    progress: ProgressFn | None = None,
    cancelled: Callable[[], bool] | None = None,
    env: dict[str, Requirement] | None = None,
) -> ConvertResult:
    """转换一个文件；**不抛异常**（失败信息放在结果里，便于批量逐项汇报）。

    Args:
        source: 源文件。
        options: 目标格式 / 目录 / 命名 / 覆盖策略等。
        config: 应用配置（渲染与音频参数、外部工具路径）。
        progress: ``(阶段, 已完成, 总数)`` 回调（批量进度用）。
        cancelled: 返回 True 时尽快停下（步骤之间检查；外部命令无法中途打断）。
        env: 预先探测好的依赖表（批量时复用，避免每个文件重探）。
    """
    src = Path(source)
    t0 = time.perf_counter()
    result = ConvertResult(source=src, target=options.target)

    def say(stage: str, done: int, total: int) -> None:
        if progress is not None:
            progress(stage, done, total)

    def is_cancelled() -> bool:
        return cancelled is not None and bool(cancelled())

    def check_cancel() -> None:
        if is_cancelled():
            raise _Cancelled()

    sfmt = detect_format(src)
    try:
        if not src.is_file():
            raise ConvertError(f"源文件不存在：{src}")
        if not sfmt:
            raise ConvertError(f"不认识的源格式：{src.suffix or '(无扩展名)'}")
        entry = CONVERSIONS.get((sfmt, options.target))
        if entry is None:
            raise ConvertError(
                f"不支持 {sfmt} → {options.target}；可用目标："
                + "、".join(sorted(t for (s, t) in CONVERSIONS if s == sfmt))
            )
        priority, how = entry
        if priority == "P3":
            raise ConvertError(f"{sfmt} → {options.target} 本期不做（P3）：{how}")

        if env is None:
            env = check_environment(config)
        missing = missing_requirements(sfmt, options.target, config, env)
        if missing:
            raise ConvertError(
                "缺少依赖：" + "；".join(f"{r.name}（{r.hint}）" for r in missing)
            )

        out_path, skip = plan_output(src, options)
        if skip:
            result.status = "skipped"
            result.message = f"目标已存在（{options.overwrite} 策略）：{out_path.name}"
            result.elapsed_s = time.perf_counter() - t0
            return result

        out_path.parent.mkdir(parents=True, exist_ok=True)
        check_cancel()

        if sfmt == "musicxml" and options.target in SUITE_TARGETS:
            _run_suite_conversion(                src, options, config, out_path, result, say, check_cancel, is_cancelled
            )
        elif sfmt == "musicxml" and options.target == "pdf":
            result.outputs = [_musicxml_to_pdf(src, out_path, config, check_cancel)]
        elif sfmt == "midi" and options.target == "musicxml":
            result.outputs = [_midi_to_musicxml(src, out_path, options, check_cancel)]
        elif sfmt == "midi" and options.target in {"wav", "mp3"}:
            result.outputs = _midi_to_audio(src, options, config, out_path, result, check_cancel)
        elif sfmt in {"mp3", "wav"} and options.target in {"mp3", "wav"}:
            result.outputs = [_audio_to_audio(src, out_path, config, check_cancel)]
        elif sfmt == "svg" and options.target == "musicxml":
            result.outputs = [_svg_to_musicxml(src, out_path, result, check_cancel)]
        elif sfmt == "pdf" and options.target == "svg":
            result.outputs = [_pdf_to_svg(src, options, config, out_path, result, check_cancel)]
        else:  # pragma: no cover - 上面的分支已覆盖全部非 P3 组合
            raise ConvertError(f"{sfmt} → {options.target} 尚未实现")

        result.status = "ok"
        result.message = how
        say("完成", 1, 1)
    except _Cancelled:
        result.status = "cancelled"
        result.message = "已取消"
    except ConvertError as e:
        result.status = "failed"
        result.message = str(e)
        log.warning("转换失败：%s → %s —— %s", src.name, options.target, e)
    except ZpyMusicError as e:  # 渲染 / 音频链路的领域错误
        result.status = "failed"
        result.message = f"{type(e).__name__}: {e}"
        log.exception("转换失败：%s", src)
    except Exception as e:  # noqa: BLE001 - 批量里单项失败不能中断
        result.status = "failed"
        result.message = f"{type(e).__name__}: {e}"
        log.exception("转换异常：%s", src)
    result.elapsed_s = time.perf_counter() - t0
    return result


class _Cancelled(Exception):
    """内部信号：用户取消（不会冒泡到调用方）。"""


# --------------------------------------------------------------------------- 套件形态
def _run_suite_conversion(
    src: Path,
    options: ConvertOptions,
    config: AppConfig,
    suite_dir: Path,
    result: ConvertResult,
    say: ProgressFn,
    check_cancel: Callable[[], None],
    is_cancelled: Callable[[], bool],
) -> None:
    """``musicxml → svg/midi/mp3/wav``：复用生成套件流水线，产物是一个套件目录。

    为什么用套件形态（而不是"一个 .mp3 文件"）：需求 §5.4.1 明确要求这几条转换
    **复用生成套件流水线**；而且套件里同时有行 SVG + ``sync.json``，
    于是产物可以直接「载入播放界面」看谱跟读（F3.6）。
    """
    target = options.target
    name = suite_dir.name  # 命名模板已算好的主名
    out_dir = suite_dir.parent
    if suite_dir.exists() and options.overwrite == OverwritePolicy.OVERWRITE:
        shutil.rmtree(suite_dir, ignore_errors=True)

    gen = GenerateOptions(
        suites_root=out_dir,
        overwrite=OverwritePolicy.OVERWRITE,
        keep_wav=(target == "wav") or options.keep_wav,
        render_svg=(target == "svg") or options.also_svg or target in {"mp3", "wav"},
        render_midi=target in {"midi", "mp3", "wav"} or options.also_svg,
        render_audio=target in {"mp3", "wav"},
        encode_mp3=(target == "mp3"),
        # 套件里保留源 MusicXML：套件契约要求（F1.7 / validate 会检查），
        # 而且它正是 svg → musicxml 依赖的"侧车文件"
        copy_musicxml=True,
        verify=False,
        soundfont=options.soundfont,
        custom_dir=out_dir,
    )

    def on_progress(stage: str, done: int, total: int) -> None:
        check_cancel()
        say(stage, done, total)

    res = generate_suite(src, config, gen, progress=on_progress, cancelled=is_cancelled)
    if res.status != "ok":
        raise ConvertError(res.message or f"生成失败（{res.status}）")

    result.suite_dir = _rename_suite(res.suite_dir or suite_dir, name)
    result.warnings.extend(res.warnings)

    # 只把"用户要的那一种"产物列出来（同步数据 / 行 SVG 是流水线的正常附件）
    def pick(suffixes: tuple[str, ...]) -> list[Path]:
        return [p for p in sorted(result.suite_dir.iterdir()) if p.suffix.lower() in suffixes]

    if target == "svg":
        result.outputs = sorted((result.suite_dir / "svg").glob("*.svg")) or pick((".svg",))
        result.warnings.append(
            "SVG 按行输出（每行一个文件，播放界面据此按行懒加载）；"
            "套件目录里还有 sync.json，可直接「载入播放界面」。"
        )
    elif target == "midi":
        result.outputs = pick((".mid", ".midi"))
    elif target == "wav":
        result.outputs = pick((".wav",))
    else:
        result.outputs = pick((".mp3",))
    if not result.outputs:
        raise ConvertError(f"流水线没有产出 {target} 文件（{result.suite_dir}）")


# --------------------------------------------------------------------------- 单文件形态
def _norm_musicxml(src: Path, work: Path) -> Path:
    """把（可能是 ``.mxl`` 的）源规范化成 MuseScore / music21 能直接吃的 MusicXML 文件。"""
    doc = read_musicxml(src)
    xml = work / f"{src.stem}.musicxml"
    xml.write_bytes(doc.xml_bytes)
    return xml


def _child_env() -> dict[str, str]:
    """给外部 GUI 程序（MuseScore）的环境变量。

    **必须去掉我们自己的 Qt 平台设置**：实测（MuseScore 4 + QT_QPA_PLATFORM=offscreen）
    子进程会直接崩溃（``returncode=0xC0000409``，栈保护终止），而干净环境下同一命令
    0.9 s 就导出成功。原因是 MuseScore 自己也是 Qt 程序，会继承这些变量。
    """
    import os  # noqa: PLC0415

    env = dict(os.environ)
    for key in (
        "QT_QPA_PLATFORM",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_PLUGIN_PATH",
        "QT_DEBUG_PLUGINS",
    ):
        env.pop(key, None)
    return env


def _musicxml_to_pdf(
    src: Path, out_pdf: Path, config: AppConfig, check_cancel: Callable[[], None]
) -> Path:
    """MuseScore 4 CLI 导出 PDF（排版质量最好，需求 §5.4.1）。"""
    ms = config.paths.resolved().musescore
    if not ms or not Path(ms).is_file():
        raise ConvertError("未找到 MuseScore 4，无法导出 PDF（设置 → 路径 里可手工指定）")
    work = _tmp_dir(src)
    xml = _norm_musicxml(src, work)
    check_cancel()
    cmd = [str(ms), "-f", "-o", str(out_pdf), str(xml)]
    log.debug("MuseScore 导出：%s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=900,
            env=_child_env(),
        )
    except subprocess.TimeoutExpired as e:
        raise ConvertError("MuseScore 导出超时（>900 s）") from e
    except OSError as e:
        raise ConvertError(f"无法启动 MuseScore：{e}") from e
    if not out_pdf.is_file() or out_pdf.stat().st_size == 0:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-400:]
        raise ConvertError(
            f"MuseScore 导出失败（returncode={proc.returncode}）：{tail}"
            "（提示：MuseScore 是独立 GUI 程序，若在无显示环境里运行需要可用的显示会话）"
        )
    return out_pdf


def _midi_to_musicxml(
    src: Path, out_xml: Path, options: ConvertOptions, check_cancel: Callable[[], None]
) -> Path:
    """music21 把 MIDI 转成 MusicXML（**推断性**：小节 / 声部靠猜）。"""
    check_cancel()
    try:
        from music21 import converter, metadata  # noqa: PLC0415
    except ImportError as e:
        raise ConvertError(f"未安装 music21，无法 MIDI → MusicXML：{e}") from e

    score = converter.parse(str(src), quantizePost=bool(options.quantize))
    if score.metadata is None:
        score.metadata = metadata.Metadata()
    if not score.metadata.title:
        score.metadata.title = src.stem  # 否则导出的是"Music21 Fragment"
    check_cancel()
    try:
        score.write("musicxml", fp=str(out_xml))
    except Exception as e:  # noqa: BLE001 - music21 的异常类型很杂
        raise ConvertError(f"music21 写 MusicXML 失败：{e}") from e
    if not out_xml.is_file() or out_xml.stat().st_size == 0:
        raise ConvertError("music21 没有产出 MusicXML")
    return out_xml


def _audio_tools(config: AppConfig):  # noqa: ANN201
    paths = config.paths.resolved()
    return resolve_tools(
        Path(paths.ffmpeg) if paths.ffmpeg else None,
        Path(paths.fluidsynth_dir) if paths.fluidsynth_dir else None,
    )


def _midi_to_audio(
    src: Path,
    options: ConvertOptions,
    config: AppConfig,
    out_path: Path,
    result: ConvertResult,
    check_cancel: Callable[[], None],
) -> list[Path]:
    """MIDI → WAV/MP3（FluidSynth 离线渲染，必要时 ffmpeg 编码）。"""
    tools = _audio_tools(config)
    soundfont = options.soundfont or config.audio.soundfont_path(PROJECT_ROOT)
    work = _tmp_dir(src)
    wav = work / f"{src.stem}.wav"
    check_cancel()
    from .midi_tools import summarize_midi  # noqa: PLC0415

    try:  # 音色/通道自检：很多 MIDI 没有 Program Change，全部落到钢琴（需求 §5.4.1 的陷阱）
        summary = summarize_midi(src)
    except Exception:  # noqa: BLE001 - 自检失败不影响转换
        summary = None
    if summary is not None:
        guessed = [c for c in summary.channels if c.program is None]
        if guessed:
            result.warnings.append(
                f"MIDI 里有 {len(guessed)} 个通道没有 Program Change，FluidSynth 会按音色库默认"
                "（通常是钢琴）发声；如需指定音色，请用「生成套件」页的音色映射表先重写 MIDI。"
            )

    midi_to_wav(
        src,
        wav,
        Path(soundfont),
        tools,
        sample_rate=int(config.audio.sample_rate),
        gain=float(config.audio.gain),
        reverb=bool(config.audio.reverb),
        chorus=bool(config.audio.chorus),
        timeout_s=900,
    )
    if options.target == "wav":
        if wav != out_path:
            wav.replace(out_path)
        return [out_path]
    check_cancel()
    if not tools.can_encode_mp3:
        raise ConvertError("未找到 ffmpeg，无法编码 MP3")
    wav_to_mp3(wav, out_path, tools, bitrate_kbps=int(config.audio.mp3_bitrate_kbps))
    return [out_path]


def _audio_to_audio(
    src: Path, out_path: Path, config: AppConfig, check_cancel: Callable[[], None]
) -> Path:
    """``mp3 ↔ wav``（ffmpeg）。"""
    tools = _audio_tools(config)
    if not tools.can_encode_mp3:
        raise ConvertError("未找到 ffmpeg")
    check_cancel()
    cmd = [str(tools.ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if out_path.suffix.lower() == ".mp3":
        cmd += ["-codec:a", "libmp3lame", "-b:a", f"{int(config.audio.mp3_bitrate_kbps)}k"]
    else:
        cmd += ["-codec:a", "pcm_s16le"]
    cmd.append(str(out_path))
    log.debug("ffmpeg 转码：%s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=900)
    except subprocess.TimeoutExpired as e:
        raise ConvertError("ffmpeg 转码超时（>900 s）") from e
    except OSError as e:
        raise ConvertError(f"无法启动 ffmpeg：{e}") from e
    if not out_path.is_file() or out_path.stat().st_size == 0:
        tail = (proc.stderr or "").strip()[-400:]
        raise ConvertError(f"ffmpeg 转码失败（returncode={proc.returncode}）：{tail}")
    return out_path


def _svg_to_musicxml(
    src: Path,
    out_xml: Path,
    result: ConvertResult,
    check_cancel: Callable[[], None],
) -> Path:
    """``svg → musicxml``：只支持本工具生成的 SVG，且需要同套件保留的 MusicXML。

    SVG 本身不含完整记谱信息（小节结构、调号、力度…），真正的可靠来源是生成 SVG 时
    同目录/同套件保留的 MusicXML。找不到就明确拒绝（需求 §5.4.1）。
    """
    check_cancel()
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise ConvertError(f"无法读取 SVG：{e}") from e
    if 'class="note"' not in text and "class='note'" not in text:
        raise ConvertError(
            "该 SVG 不是本工具生成的曲谱（找不到 <g class=\"note\"> 结构）。"
            "第三方 SVG（如 MuseScore 导出）无法反推 MusicXML，请用「生成套件」页从源文件重做。"
        )

    sidecar = _find_sidecar_musicxml(src)
    if sidecar is None:
        raise ConvertError(
            "找不到配套的 MusicXML：本工具生成的套件会把源 MusicXML 一并保留在套件目录里，"
            "请对套件里的 SVG 或原 MusicXML 操作。"
        )
    doc = read_musicxml(sidecar)
    out_xml.write_bytes(doc.xml_bytes)
    result.warnings.append(
        f"已复用配套的 MusicXML（{sidecar.name}）并规范化输出；"
        "SVG 结构本身不含完整记谱信息，因此这不是「从图形反推乐谱」。"
    )
    return out_xml


def _find_sidecar_musicxml(svg: Path) -> Path | None:
    """按"同目录同名 → 套件目录里同名 → 套件目录里任意 MusicXML"的顺序找侧车文件。"""
    suffixes = (".musicxml", ".xml", ".mxl")
    candidates: list[Path] = []
    for suffix in suffixes:
        candidates.append(svg.with_suffix(suffix))
    # 套件布局：<suite>/svg/sys-000X.svg → <suite>/<base>.musicxml
    for up in (svg.parent, svg.parent.parent):
        for suffix in suffixes:
            candidates.append(up / f"{up.name}{suffix}")
        if up.is_dir():
            for suffix in suffixes:
                candidates.extend(sorted(up.glob(f"*{suffix}")))
    for c in candidates:
        if c.is_file() and c != svg:
            return c
    return None


def _pdf_to_svg(
    src: Path,
    options: ConvertOptions,
    config: AppConfig,
    out_path: Path,
    result: ConvertResult,
    check_cancel: Callable[[], None],
) -> list[Path]:
    """``pdf → svg``：优先提取 PDF 内嵌的 MusicXML 再渲染（保持矢量）。"""
    if not _has_module("pypdf"):
        raise ConvertError(
            "PDF → SVG 需要 pypdf 提取内嵌 MusicXML（pip install pypdf）；"
            "或安装 pypdfium2 走位图方案（非矢量，不保证可缩放）"
        )
    check_cancel()
    from pypdf import PdfReader  # type: ignore[import-not-found]  # noqa: PLC0415

    reader = PdfReader(str(src))
    attachments = getattr(reader, "attachments", None) or {}
    payload: bytes | None = None
    name = ""
    for fname, files in attachments.items():
        for data in files:
            if Path(fname).suffix.lower() in {".musicxml", ".xml", ".mxl"}:
                payload, name = bytes(data), str(fname)
                break
        if payload is not None:
            break
    if payload is None:
        raise ConvertError(
            "该 PDF 没有内嵌 MusicXML 附件，无法矢量转换（本工具不做 OMR / 位图降级）。"
            "可安装 pypdfium2 走位图方案，或用 MuseScore 打开后另存 SVG。"
        )
    work = _tmp_dir(src)
    xml = work / (name or "embedded.musicxml")
    xml.write_bytes(payload)
    doc = read_musicxml(xml)
    svgs = render_svgs(
        doc.xml_bytes, config.render, max_systems=0, resources=config.paths.verovio_resources
    )
    result.warnings.append(f"已从 PDF 提取内嵌乐谱（{name}）并重新渲染为 SVG")
    if len(svgs) == 1:
        out_path.write_text(svgs[0], encoding="utf-8")
        return [out_path]
    # 多页：写成同名的目录 + 逐行 SVG（与套件布局一致，便于继续使用）
    out_dir = out_path.with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    outs = []
    for i, text in enumerate(svgs, 1):
        f = out_dir / f"sys-{i:04d}.svg"
        f.write_text(text, encoding="utf-8")
        outs.append(f)
    return outs


# --------------------------------------------------------------------------- 展示辅助
def suite_layout_of(suite_dir: Path) -> SuiteLayout:
    """给一个套件目录造 :class:`SuiteLayout`（UI 里"载入播放界面"等用）。"""
    d = Path(suite_dir)
    return make_layout(d.parent, d.name)


def find_suite(suite_dir: Path) -> Suite | None:
    """在套件目录的父目录里找到该套件（供播放页载入）。"""
    d = Path(suite_dir)
    root = d.parent
    if not d.is_dir():
        return None
    return next((s for s in discover_suites(root) if s.dir == d), None)
