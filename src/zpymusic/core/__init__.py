"""``zpymusic.core`` —— 不依赖 Qt 的转换核心。

设计原则（需求 §6）：本包内**不允许** ``import PySide6``，
以保证 CLI 与单元测试可以脱离 GUI 运行。
"""

from __future__ import annotations

from .audio_render import AudioTools, midi_to_wav, probe_audio, resolve_tools, wav_to_mp3
from .gm import GM_PROGRAMS, PROGRAM_CHOICES, gm_program_name, guess_program_from_name
from .musicxml_io import MusicXMLDocument, PartInfo, iter_source_files, read_musicxml
from .pipeline import GenerateOptions, GenerateResult, generate_suite
from .score_render import (
    PREVIEW_MAX_SYSTEMS,
    MeasureInfo,
    RenderedScore,
    SystemInfo,
    TimelineNote,
    build_notes_from_timemap,
    render_preview_svgs,
    render_score,
    svg_size,
    verovio_version,
)
from .suite import (
    OverwritePolicy,
    Suite,
    SuiteLayout,
    discover_suites,
    make_layout,
    resolve_target,
    svg_system_name,
)
from .sync_model import SYNC_SCHEMA, SyncDoc, build_sync_doc, validate_sync_against_svg

__all__ = [
    # musicxml
    "MusicXMLDocument",
    "PartInfo",
    "read_musicxml",
    "iter_source_files",
    # render
    "RenderedScore",
    "TimelineNote",
    "SystemInfo",
    "MeasureInfo",
    "render_score",
    "render_preview_svgs",
    "svg_size",
    "PREVIEW_MAX_SYSTEMS",
    "build_notes_from_timemap",
    "verovio_version",
    # sync
    "SyncDoc",
    "SYNC_SCHEMA",
    "build_sync_doc",
    "validate_sync_against_svg",
    # suite
    "Suite",
    "SuiteLayout",
    "OverwritePolicy",
    "discover_suites",
    "make_layout",
    "resolve_target",
    "svg_system_name",
    # audio
    "AudioTools",
    "resolve_tools",
    "midi_to_wav",
    "wav_to_mp3",
    "probe_audio",
    # gm
    "GM_PROGRAMS",
    "PROGRAM_CHOICES",
    "gm_program_name",
    "guess_program_from_name",
    # pipeline
    "GenerateOptions",
    "GenerateResult",
    "generate_suite",
]
