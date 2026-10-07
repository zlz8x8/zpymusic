"""生成流水线：MusicXML → 套件（需求 §5.2 的流程图）。

顺序严格遵循文档 §5.2 的注意事项::

    读取/规范化 → resolve 目录 → Verovio(setOptions→loadData→renderToMIDI
    →renderToTimemap→renderToSVG) → 写 MIDI → 写 sync.json
    → FluidSynth → ffmpeg → 复制源文件

本模块**不导入 Qt**，因此 CLI 与 GUI 共用同一条链路（需求 §6 模块边界原则）。
"""

from __future__ import annotations

import hashlib
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..common.config import AppConfig
from ..common.deps import default_ffmpeg, default_fluidsynth_dir
from ..common.errors import AudioRenderError, TaskCancelled, ZpyMusicError
from ..common.log import get_logger
from . import midi_tools
from .audio_render import AudioTools, midi_to_wav, resolve_tools, wav_to_mp3
from .musicxml_io import MusicXMLDocument, normalize_root_name, read_musicxml
from .score_render import ProgressFn, RenderedScore, render_score
from .suite import (
    OverwritePolicy,
    SuiteLayout,
    copy_source,
    make_layout,
    resolve_target,
    svg_system_name,
)
from .sync_model import build_sync_doc, validate_sync_against_svg

log = get_logger(__name__)

__all__ = ["GenerateOptions", "GenerateResult", "generate_suite", "build_render_snapshot"]

MsgFn = Callable[[str, str], None]
"""消息回调：``(级别, 文本)``，级别为 ``info`` / ``warning`` / ``error``。"""


@dataclass
class GenerateOptions:
    """一次套件生成的全部可调参数。"""

    suites_root: Path
    overwrite: str = OverwritePolicy.SKIP
    keep_wav: bool = False
    render_svg: bool = True
    render_midi: bool = True
    render_audio: bool = True
    encode_mp3: bool = True
    copy_musicxml: bool = True
    verify: bool = True
    soundfont: Path | None = None
    part_overrides: dict[str, dict[str, Any]] | None = None
    """``{part_id: {"midi_channel": int, "midi_program": int}}``，来自 F1.8 表格。"""
    custom_dir: Path | None = None
    """用户手工指定的目标套件目录（对应 F1.2 的"用户可选择其他文件夹"）。"""


@dataclass
class GenerateResult:
    """一次生成的产物与诊断信息。"""

    source: Path
    base: str = ""
    status: str = "pending"
    """``ok`` | ``skipped`` | ``failed`` | ``cancelled``"""
    message: str = ""
    suite_dir: Path | None = None
    musicxml: Path | None = None
    mxl: Path | None = None
    midi: Path | None = None
    wav: Path | None = None
    mp3: Path | None = None
    sync: Path | None = None
    svg_count: int = 0
    note_count: int = 0
    duration_ms: int = 0
    part_count: int = 0
    elapsed_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    program_changes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def summary(self) -> str:
        if self.status == "ok":
            return (
                f"{self.base}: {self.svg_count} 个 system / {self.note_count} 音符 / "
                f"{self.duration_ms/1000:.1f}s / {self.elapsed_s:.1f}s"
            )
        return f"{self.base or self.source.name}: {self.status} —— {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "base": self.base,
            "status": self.status,
            "message": self.message,
            "suite_dir": str(self.suite_dir) if self.suite_dir else "",
            "musicxml": str(self.musicxml) if self.musicxml else "",
            "midi": str(self.midi) if self.midi else "",
            "mp3": str(self.mp3) if self.mp3 else "",
            "sync": str(self.sync) if self.sync else "",
            "svg_count": self.svg_count,
            "note_count": self.note_count,
            "duration_ms": self.duration_ms,
            "elapsed_s": round(self.elapsed_s, 3),
            "warnings": self.warnings,
            "errors": self.errors,
        }


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def build_render_snapshot(config: AppConfig) -> dict[str, Any]:
    """把渲染选项固化成可写进 ``sync.json`` 的快照（需求 D2 / R7 可追溯）。"""
    r = config.render
    return {
        "engine": r.engine,
        "scale": r.scale,
        "page_width": r.page_width,
        "breaks": "none" if r.single_page else r.breaks,
        "page_height": r.max_page_height if r.single_page else r.page_height,
        "adjust_page_height": True if r.single_page else r.adjust_page_height,
        "single_page": r.single_page,
    }


def _decide_layout(
    opts: GenerateOptions, doc: MusicXMLDocument, config: AppConfig
) -> tuple[SuiteLayout, list[str]]:
    """决定最终写入的套件目录。"""
    if opts.custom_dir is not None:
        # 用户直接给了目标文件夹：<custom_dir>/<base>
        layout = make_layout(Path(opts.custom_dir), doc.base_name)
        notes: list[str] = []
        if layout.dir.exists():
            notes.append(f"目标目录 {layout.dir} 已存在，将按 {opts.overwrite} 策略处理")
        return layout, notes
    return resolve_target(opts.suites_root, doc.base_name, opts.overwrite)


def generate_suite(
    source: Path | str,
    config: AppConfig,
    options: GenerateOptions,
    *,
    progress: ProgressFn | None = None,
    message: MsgFn | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> GenerateResult:
    """把单个 MusicXML 源文件生成一个完整套件。

    本函数**不抛异常**（除 ``TaskCancelled`` 以外）：所有失败都体现在
    :class:`GenerateResult` 的 ``status`` / ``errors`` 里，便于批量任务继续。

    Args:
        source: 源 MusicXML（``.xml`` / ``.musicxml`` / ``.mxl``）。
        config: 应用配置。
        options: 生成选项。
        progress: ``(阶段, 已完成, 总数)`` 进度回调。
        message: ``(级别, 文本)`` 消息回调。
        cancelled: 返回 True 表示用户取消。

    Raises:
        TaskCancelled: 用户取消（由调用方决定是记录还是继续下一个文件）。
    """
    src = Path(source)
    res = GenerateResult(source=src, base=normalize_root_name(src))
    t_start = time.perf_counter()

    def say(level: str, text: str) -> None:
        log.log({"info": 20, "warning": 30, "error": 40}.get(level, 20), "%s", text)
        if message is not None:
            message(level, text)

    def check_cancel() -> None:
        if cancelled is not None and cancelled():
            raise TaskCancelled("用户取消了生成任务")

    try:
        # ---------- 1) 读取与规范化 ----------------------------------------
        say("info", f"读取源文件：{src.name}")
        doc = read_musicxml(src)
        res.base = doc.base_name
        res.part_count = doc.part_count
        for w in doc.warnings:
            res.warnings.append(w)
            say("warning", w)

        layout, notes = _decide_layout(options, doc, config)
        res.suite_dir = layout.dir
        for n in notes:
            if "跳过" in n:
                res.warnings.append(n)
            say("info", n)
        if options.overwrite == OverwritePolicy.SKIP and layout.dir.exists():
            existing = {p.name for p in layout.existing()}
            if layout.sync.name in existing and layout.midi.name in existing:
                res.status = "skipped"
                res.message = f"套件已存在（{layout.dir}），按 skip 策略跳过"
                res.elapsed_s = time.perf_counter() - t_start
                say("info", res.message)
                return res

        check_cancel()
        layout.dir.mkdir(parents=True, exist_ok=True)

        # ---------- 2) 用户覆盖音色映射（F1.8） ----------------------------
        if options.part_overrides:
            for part in doc.parts:
                ov = options.part_overrides.get(part.id)
                if not ov:
                    continue
                if ov.get("midi_channel"):
                    part.midi_channel = int(ov["midi_channel"])
                if ov.get("midi_program") is not None:
                    part.midi_program = int(ov["midi_program"])

        # ---------- 3) Verovio 渲染 ----------------------------------------
        say("info", f"渲染 SVG / MIDI / 时间轴（引擎 verovio，{doc.part_count} 个声部）")
        svg_written: list[Path] = []

        def emit_svg(page_no: int, svg_text: str) -> None:
            if not options.render_svg:
                return
            p = layout.svg_system(page_no)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(svg_text, encoding="utf-8")
            svg_written.append(p)

        rendered: RenderedScore = render_score(
            doc.xml_bytes,
            config.render,
            progress=progress,
            cancelled=cancelled,
            emit_svg=emit_svg if options.render_svg else None,
            # 后台线程里渲染时必须显式给出资源目录，否则 Verovio 找不到字形
            # （M5 实测：主线程先建过 toolkit 后，工作线程里的 toolkit 会加载失败）
            resources=config.paths.verovio_resources,
        )
        res.note_count = rendered.note_count
        res.duration_ms = rendered.duration_ms
        res.svg_count = len(rendered.systems)
        for w in rendered.warnings:
            res.warnings.append(w)
            say("warning", w)

        # ---------- 4) MIDI ------------------------------------------------
        if options.render_midi:
            layout.midi.write_bytes(rendered.midi_bytes)
            res.midi = layout.midi
            say("info", f"MIDI 已写出：{layout.midi.name}（{len(rendered.midi_bytes)} 字节）")
            try:
                summary = midi_tools.summarize_midi(layout.midi)
                for w in summary.warnings:
                    res.warnings.append(w)
                    say("warning", w)
                if options.part_overrides:
                    programs = {
                        int(v["midi_channel"]): int(v.get("midi_program", 0))
                        for v in options.part_overrides.values()
                        if v.get("midi_channel") and v.get("midi_program") is not None
                    }
                    changes = midi_tools.apply_channel_programs(
                        layout.midi, layout.midi, programs
                    )
                    res.program_changes = changes
                    for c in changes:
                        say("info", f"音色改写：{c}")
            except AudioRenderError as e:
                res.warnings.append(str(e))
                say("warning", str(e))

        # ---------- 5) sync.json -------------------------------------------
        svg_names = {s.index: layout.svg_rel(s.index) for s in rendered.systems}
        sync_doc = build_sync_doc(
            doc,
            rendered,
            svg_names=svg_names,
            soundfont=_soundfont_rel(config, options),
            audio={
                "sample_rate": config.audio.sample_rate,
                "gain": config.audio.gain,
                "reverb": config.audio.reverb,
                "chorus": config.audio.chorus,
                "bitrate_kbps": config.audio.mp3_bitrate_kbps,
            },
            render_opts=build_render_snapshot(config),
            sha1=_sha1(src.read_bytes()),
        )
        sync_doc.save(layout.sync)
        res.sync = layout.sync
        say(
            "info",
            f"同步数据已写出：{layout.sync.name}"
            f"（{sync_doc.note_count} 音符 / {len(sync_doc.systems)} system / "
            f"{sync_doc.duration_ms/1000:.1f}s）",
        )

        # ---------- 6) 音频 ------------------------------------------------
        if options.render_audio:
            check_cancel()
            _render_audio(res, layout, config, options, rendered, say, check_cancel)

        # ---------- 7) 复制源文件 ------------------------------------------
        if options.copy_musicxml:
            xml_dest, mxl_dest = copy_source(src, layout)
            # 统一以"规范化后的 XML"作为套件内的权威 MusicXML（§5.1.3 步骤 4）
            doc.write_normalized(xml_dest)
            res.musicxml = xml_dest
            res.mxl = mxl_dest
            extra = f" / {mxl_dest.name}（原始压缩源）" if mxl_dest else ""
            say("info", f"源文件已复制到套件目录：{xml_dest.name}{extra}")

        # ---------- 8) 一致性自检 ------------------------------------------
        if options.verify and options.render_svg:
            problems = validate_sync_against_svg(
                sync_doc, lambda name: (layout.dir / name).read_text(encoding="utf-8")
            )
            if problems:
                for p in problems[:5]:
                    res.warnings.append(f"一致性检查：{p}")
                    say("warning", f"一致性检查：{p}")
            else:
                say("info", f"一致性检查通过：{sync_doc.note_count} 个音符 ID 均能在 SVG 中找到")

        res.status = "ok"
        res.message = "生成完成"
        res.elapsed_s = time.perf_counter() - t_start
        say("info", f"✓ {res.summary()}")
        return res

    except TaskCancelled:
        res.status = "cancelled"
        res.message = "已取消"
        res.elapsed_s = time.perf_counter() - t_start
        raise
    except ZpyMusicError as e:
        res.status = "failed"
        res.message = str(e)
        res.errors.append(str(e))
        res.elapsed_s = time.perf_counter() - t_start
        say("error", f"✗ {src.name}: {e}")
        return res
    except Exception as e:  # noqa: BLE001 - 批量任务不能因单个文件崩溃
        res.status = "failed"
        res.message = f"未预期的错误：{type(e).__name__}: {e}"
        res.errors.append(res.message)
        res.elapsed_s = time.perf_counter() - t_start
        log.exception("生成套件失败：%s", src)
        say("error", f"✗ {src.name}: {res.message}")
        return res


def _soundfont_rel(config: AppConfig, options: GenerateOptions) -> str:
    """音色库以**相对项目根**的路径写进 sync.json，保证可移植。"""
    from ..common.paths import PROJECT_ROOT  # noqa: PLC0415

    sf = options.soundfont or config.audio.soundfont_path(PROJECT_ROOT)
    sf = Path(sf)
    try:
        return sf.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return sf.as_posix()


def _render_audio(
    res: GenerateResult,
    layout: SuiteLayout,
    config: AppConfig,
    options: GenerateOptions,
    rendered: RenderedScore,
    say: MsgFn,
    check_cancel: Callable[[], None],
) -> None:
    """渲染 WAV / MP3；失败只记警告，不影响套件的其余部分。"""
    from ..common.paths import PROJECT_ROOT  # noqa: PLC0415

    tools: AudioTools = resolve_tools(
        ffmpeg=Path(config.paths.ffmpeg) if config.paths.ffmpeg else default_ffmpeg(),
        fluidsynth_dir=(
            Path(config.paths.fluidsynth_dir)
            if config.paths.fluidsynth_dir
            else default_fluidsynth_dir()
        ),
    )
    soundfont = Path(options.soundfont or config.audio.soundfont_path(PROJECT_ROOT))
    if not soundfont.is_file():
        msg = f"音色库不存在，跳过音频渲染：{soundfont}"
        res.warnings.append(msg)
        say("warning", msg)
        return
    if not tools.can_render_wav:
        msg = "FluidSynth 不可用，已跳过 WAV/MP3 生成（其余产物已生成）"
        res.warnings.append(msg)
        say("warning", msg)
        return
    if not layout.midi.is_file():
        msg = "没有 MIDI 文件，跳过音频渲染"
        res.warnings.append(msg)
        say("warning", msg)
        return

    check_cancel()

    # MIDI 与乐谱的时间轴同源性校验（需求 D2 / 验收标准 2 的核心断言）
    midi_ms = midi_tools.midi_duration_ms(layout.midi)
    if midi_ms and rendered.duration_ms:
        diff = abs(midi_ms - rendered.duration_ms)
        if diff > max(100, rendered.duration_ms * 0.005):
            msg = (
                f"MIDI 时长 {midi_ms/1000:.2f}s 与乐谱时间轴 "
                f"{rendered.duration_ms/1000:.2f}s 相差 {diff/1000:.2f}s，"
                "音频与曲谱可能不同步"
            )
            res.warnings.append(msg)
            say("warning", msg)
        else:
            say("info", f"MIDI 与时间轴同源校验通过（相差 {diff} ms）")

    say("info", f"渲染音频：{soundfont.name} → {layout.wav.name}")
    try:
        audio = midi_to_wav(
            layout.midi,
            layout.wav,
            soundfont,
            tools,
            sample_rate=config.audio.sample_rate,
            gain=config.audio.gain,
            reverb=config.audio.reverb,
            chorus=config.audio.chorus,
            expected_duration_ms=rendered.duration_ms,
        )
    except AudioRenderError as e:
        msg = f"音频渲染失败：{e}"
        res.warnings.append(msg)
        say("warning", msg)
        if layout.wav.exists():
            layout.wav.unlink(missing_ok=True)
        return

    for w in audio.warnings:
        res.warnings.append(w)
        say("warning", w)

    if options.encode_mp3:
        if not tools.can_encode_mp3:
            msg = "ffmpeg 不可用，保留 WAV 但不生成 MP3"
            res.warnings.append(msg)
            say("warning", msg)
            res.wav = layout.wav
        else:
            check_cancel()
            try:
                wav_to_mp3(
                    layout.wav,
                    layout.mp3,
                    tools,
                    bitrate_kbps=config.audio.mp3_bitrate_kbps,
                )
                res.mp3 = layout.mp3
            except AudioRenderError as e:
                msg = f"MP3 编码失败：{e}"
                res.warnings.append(msg)
                say("warning", msg)
                res.wav = layout.wav

    if not options.keep_wav and res.mp3 is not None:
        layout.wav.unlink(missing_ok=True)
        res.wav = None
        say("info", "已删除中间 WAV（如需保留请在设置中开启）")
    elif res.wav is None and layout.wav.is_file():
        res.wav = layout.wav


def compute_system_svg_names(count: int) -> list[str]:
    """给 UI 预览用的 SVG 文件名列表。"""
    return [svg_system_name(i) for i in range(1, count + 1)]


def clean_partial(layout: SuiteLayout, keep: set[str] | None = None) -> int:
    """删除套件目录中的半成品（需求 §7.2）。

    Returns:
        删除的文件数。
    """
    keep = keep or set()
    removed = 0
    for p in layout.existing():
        if p.name in keep:
            continue
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    if layout.svg_dir.is_dir() and not any(layout.svg_dir.iterdir()):
        try:
            shutil.rmtree(layout.svg_dir)
        except OSError:
            pass
    return removed
