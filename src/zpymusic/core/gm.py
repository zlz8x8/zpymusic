"""General MIDI 音色表与名称匹配（需求 F1.8 / §7.4）。

用途：
  * 把 MusicXML 的 ``midi-program`` 显示成人可读名称；
  * 当源文件**没有**写 ``midi-program`` 时，按乐器名做一次模糊匹配给出建议值——
    但**绝不静默采用**，只作为默认建议交给用户在 F1.8 表格里确认。
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["GM_PROGRAMS", "gm_program_name", "guess_program_from_name", "PROGRAM_CHOICES"]

# General MIDI Level 1 音色表（0-based program number）
GM_PROGRAMS: tuple[str, ...] = (
    # 0-7 Piano
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavi",
    # 8-15 Chromatic Percussion
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    # 16-23 Organ
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    # 24-31 Guitar
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)",
    "Electric Guitar (clean)", "Electric Guitar (muted)", "Overdriven Guitar",
    "Distortion Guitar", "Guitar Harmonics",
    # 32-39 Bass
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    # 40-47 Strings
    "Violin", "Viola", "Cello", "Contrabass",
    "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    # 48-55 Ensemble
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
    "Choir Aahs", "Voice Oohs", "Synth Choir", "Orchestra Hit",
    # 56-63 Brass
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet",
    "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    # 64-71 Reed
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    # 72-79 Pipe
    "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    # 80-87 Synth Lead
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
    "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    # 88-95 Synth Pad
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    # 96-103 Synth Effects
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    # 104-111 Ethnic
    "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    # 112-119 Percussive
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock",
    "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    # 120-127 Sound Effects
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
)

PROGRAM_CHOICES: tuple[tuple[int, str], ...] = tuple(enumerate(GM_PROGRAMS))


def gm_program_name(program: int) -> str:
    """返回 GM 音色名；越界时回退为 ``Program N``。"""
    if 0 <= program < len(GM_PROGRAMS):
        return GM_PROGRAMS[program]
    return f"Program {program}"


# --------------------------------------------------------------------------- 名称匹配
# 乐器名 → 建议 GM program。键为"归一化后的关键词"，按顺序优先匹配长键。
_KEYWORDS: dict[str, int] = {
    # 键盘
    "piccolo": 72, "flute": 73, "flauto": 73, "oboe": 68, "english horn": 69,
    "cor anglais": 69, "bassoon": 70, "fagotto": 70, "clarinet": 71, "clarinetto": 71,
    "bass clarinet": 71, "contrabassoon": 70,
    "harpsichord": 6, "clavichord": 7, "clavi": 7, "organ": 19, "cembalo": 6,
    "celesta": 8, "glockenspiel": 9, "music box": 10, "vibraphone": 11,
    "marimba": 12, "xylophone": 13, "tubular bell": 14, "timpani": 47, "kettledrum": 47,
    # 弦乐
    "violin": 40, "violino": 40, "viola": 41, "cello": 42, "violoncello": 42,
    "contrabass": 43, "double bass": 43, "doublebass": 43, "bass": 32,
    "harp": 46, "arpa": 46, "pizzicato": 45, "tremolo": 44,
    "string ensemble": 48, "strings": 48, "string quartet": 48, "orchestra": 48,
    "fiddle": 110,
    # 铜管
    "trumpet": 56, "tromba": 56, "cornet": 56, "trombone": 57, "trombone": 57,
    "tuba": 58, "french horn": 60, "corno": 60, "horn": 60, "brass": 61,
    # 木管/萨克斯
    "soprano sax": 64, "alto sax": 65, "tenor sax": 66, "baritone sax": 67,
    "saxophone": 65, "sax": 65, "recorder": 74, "pan flute": 75, "ocarina": 79,
    "whistle": 78, "shakuhachi": 77,
    # 拨弦/民族
    "guitar": 24, "chitarra": 24, "lute": 24, "banjo": 105, "mandolin": 24,
    "sitar": 104, "koto": 107, "shamisen": 106, "kalimba": 108, "bagpipe": 109,
    # 人声
    "choir": 52, "voice": 53, "vocal": 52, "soprano": 52, "alto": 52,
    "tenor": 52, "baritone": 52, "chorus": 52,
    # 打击
    "drum": 118, "percussion": 118, "taiko": 116, "woodblock": 115,
    "steel drum": 114, "cymbal": 119,
}


def _normalize(text: str) -> str:
    """归一化乐器名：小写、去重音、合并空白与常见噪声词。"""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.lower()
    # 去掉常见后缀噪声，如 "Violin 1"、"Piano (RH)"
    t = re.sub(r"[\(\)\[\]]", " ", t)
    t = re.sub(r"\b\d+\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def guess_program_from_name(name: str) -> int | None:
    """按乐器名猜测 GM 音色号；无法判断返回 ``None``。

    匹配策略：长关键词优先（避免 ``"bass"`` 抢走 ``"double bass"``）。
    结果是**建议值**，调用方必须让用户确认（需求 F1.8）。
    """
    n = _normalize(name)
    if not n:
        return None
    for key in sorted(_KEYWORDS, key=len, reverse=True):
        if key in n:
            return _KEYWORDS[key]
    return None
