"""MIDI 元数据读取与 Program Change 覆写（需求 §5.4.1 ``midi → mp3`` / F1.8）。

两个用途：

1. **反向场景**（``midi → mp3``）：很多工具导出的 MIDI 丢了 Program Change，
   导致所有乐器都落到钢琴。需要先读出"通道 → 音色"，展示给用户确认。
2. **正向场景**（生成套件）：用户在 F1.8 表格里改了音色映射后，把 Verovio
   导出的 MIDI 就地改写为新的 Program Change，再交给 FluidSynth 渲染。

用 ``mido`` 实现（已在 ibase 环境中）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..common.errors import AudioRenderError
from ..common.log import get_logger

log = get_logger(__name__)

__all__ = ["MidiChannelInfo", "MidiSummary", "summarize_midi", "apply_channel_programs"]


@dataclass
class MidiChannelInfo:
    """一个 MIDI 通道的使用情况。"""

    channel: int
    """0-based MIDI 通道（0–15）。"""
    program: int | None = None
    """0-based GM 音色号；None 表示该通道没有任何 Program Change。"""
    note_count: int = 0
    track: int = 0
    is_percussion: bool = False

    @property
    def channel_1based(self) -> int:
        return self.channel + 1

    def to_dict(self) -> dict:
        from .gm import gm_program_name  # noqa: PLC0415

        return {
            "channel": self.channel_1based,
            "midi_channel": self.channel_1based,
            "midi_program": self.program if self.program is not None else 0,
            "program_name": gm_program_name(self.program) if self.program is not None else "",
            "explicit_program": self.program is not None,
            "note_count": self.note_count,
            "percussion": self.is_percussion,
        }


@dataclass
class MidiSummary:
    """MIDI 文件概览。"""

    channels: list[MidiChannelInfo] = field(default_factory=list)
    track_count: int = 0
    duration_ticks: int = 0
    ticks_per_beat: int = 480
    warnings: list[str] = field(default_factory=list)

    @property
    def missing_programs(self) -> list[int]:
        """没有任何 Program Change 的通道（1-based）。"""
        return [c.channel_1based for c in self.channels if c.program is None and c.note_count]


def _import_mido():
    try:
        import mido  # noqa: PLC0415

        return mido
    except ImportError as e:  # pragma: no cover - 环境问题
        raise AudioRenderError("缺少 mido，无法解析 MIDI（pip install mido）") from e


def summarize_midi(path: Path | str) -> MidiSummary:
    """读取 MIDI，汇总每个通道的音色与音符数。

    Raises:
        AudioRenderError: 文件不存在或不是有效 MIDI。
    """
    mido = _import_mido()
    p = Path(path)
    if not p.is_file():
        raise AudioRenderError(f"MIDI 文件不存在：{p}")
    try:
        mid = mido.MidiFile(str(p))
    except Exception as e:  # noqa: BLE001 - mido 抛出的异常类型不固定
        raise AudioRenderError(f"{p.name} 不是有效的 MIDI：{e}") from e

    info: dict[int, MidiChannelInfo] = {}
    for ti, track in enumerate(mid.tracks):
        for msg in track:
            if msg.type == "program_change":
                c = info.setdefault(msg.channel, MidiChannelInfo(channel=msg.channel, track=ti))
                c.program = msg.program
            elif msg.type in ("note_on", "note_off") and getattr(msg, "velocity", 0) > 0:
                c = info.setdefault(msg.channel, MidiChannelInfo(channel=msg.channel, track=ti))
                c.note_count += 1

    for ch, c in info.items():
        c.is_percussion = ch == 9  # MIDI 通道 10（0-based 9）为打击乐

    summary = MidiSummary(
        channels=[info[k] for k in sorted(info)],
        track_count=len(mid.tracks),
        duration_ticks=int(mid.length or 0),
        ticks_per_beat=mid.ticks_per_beat,
    )
    missing = summary.missing_programs
    if missing:
        summary.warnings.append(
            "以下通道没有任何 Program Change，将回退到钢琴音色："
            + ", ".join(str(c) for c in missing)
            + "（建议在音色映射表中手动指定）"
        )
    return summary


def apply_channel_programs(
    src: Path,
    dest: Path,
    programs: dict[int, int],
) -> list[str]:
    """把 ``{1-based channel: 0-based GM program}`` 覆写进 MIDI 文件。

    行为：
      * 若通道已有 ``program_change``，改写其 ``program``；
      * 若没有，在含该通道首个事件的轨道**开头**插入一个 ``program_change``。

    Returns:
        变更说明列表（供日志展示）。

    Raises:
        AudioRenderError: 源文件无效或读写失败。
    """
    mido = _import_mido()
    src, dest = Path(src), Path(dest)
    if not src.is_file():
        raise AudioRenderError(f"MIDI 文件不存在：{src}")
    if not programs:
        if src.resolve() != dest.resolve():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
        return []

    try:
        mid = mido.MidiFile(str(src))
    except Exception as e:  # noqa: BLE001
        raise AudioRenderError(f"{src.name} 不是有效的 MIDI：{e}") from e

    changes: list[str] = []
    wanted = {ch - 1: prog for ch, prog in programs.items() if 1 <= ch <= 16}

    # 1) 记录每个通道出现在哪个轨道，便于按需插入
    channel_track: dict[int, int] = {}
    for ti, track in enumerate(mid.tracks):
        for msg in track:
            ch = getattr(msg, "channel", None)
            if ch is not None and ch not in channel_track:
                channel_track[ch] = ti

    done: set[int] = set()
    for ti, track in enumerate(mid.tracks):
        for idx, msg in enumerate(track):
            if msg.type == "program_change" and msg.channel in wanted:
                new_prog = wanted[msg.channel]
                if msg.program != new_prog:
                    changes.append(
                        f"通道 {msg.channel + 1}：Program {msg.program} → {new_prog}"
                    )
                    track[idx] = msg.copy(program=new_prog)
                done.add(msg.channel)

    # 2) 没有 Program Change 的通道：在其首个事件之前插入
    for ch, prog in wanted.items():
        if ch in done or ch not in channel_track:
            continue
        track = mid.tracks[channel_track[ch]]
        pos = next(
            (idx for idx, m in enumerate(track) if getattr(m, "channel", None) == ch),
            0,
        )
        track.insert(pos, mido.Message("program_change", channel=ch, program=prog, time=0))
        changes.append(f"通道 {ch + 1}：新增 Program {prog}（原本未指定音色）")

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        mid.save(str(dest))
    except OSError as e:
        raise AudioRenderError(f"无法写出 MIDI：{dest}（{e}）") from e
    return changes


def read_programs(path: Path | str) -> dict[int, int]:
    """读出 ``{1-based channel: 0-based program}``（仅含显式指定过的通道）。"""
    summary = summarize_midi(path)
    return {c.channel_1based: c.program for c in summary.channels if c.program is not None}


def midi_duration_ms(path: Path | str) -> int:
    """读取 MIDI 的总时长（毫秒，含速度变化）。失败返回 0。

    这是"音频与曲谱同源"的**核心校验**：MIDI 由 Verovio 同一次渲染导出，
    其时长必须与 ``sync.json`` 的时间轴总时长一致（实测误差 < 10 ms）。
    """
    mido = _import_mido()
    try:
        mid = mido.MidiFile(str(path))
        return int(round(float(mid.length) * 1000))
    except Exception:  # noqa: BLE001 - 校验用，失败不致命
        return 0
