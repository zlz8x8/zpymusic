"""``sync.json`` 的数据契约、构造与读写（需求 §5.1.4）。

设计取舍（M0 实测后确定）：

* ``notes`` 数组在交响乐量级可达 **29324 条**，若每条都写全字段，文件会明显膨胀。
  因此对外的 ``notes`` 采用**紧凑键**（``s`` = onset_ms，``d`` = dur_ms，``r`` = rest，
  ``m`` = measure，``x`` = rendered），读取时同时兼容 v2.0 文档里的长键名。
* ``schema`` 定为 ``zpymusic-sync/1.1``：相对文档初稿的 ``1.0`` **新增** ``systems``
  （每行 SVG 的音符归属，供懒加载与自动滚动）与 ``measures``（当前小节显示），
  并确定 ``notes`` 的紧凑键形式。文档已同步更新。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..common.errors import SyncSchemaError
from ..common.log import get_logger
from .musicxml_io import MusicXMLDocument, PartInfo
from .score_render import MeasureInfo, RenderedScore, SystemInfo

log = get_logger(__name__)

__all__ = ["SYNC_SCHEMA", "SyncDoc", "build_sync_doc"]

SYNC_SCHEMA = "zpymusic-sync/1.1"
_COMPACT_KEYS = {"s", "d", "q", "r", "m", "x"}


@dataclass
class SyncDoc:
    """播放界面唯一依赖的数据源。"""

    schema: str = SYNC_SCHEMA
    source: dict[str, Any] = field(default_factory=dict)
    render: dict[str, Any] = field(default_factory=dict)
    audio: dict[str, Any] = field(default_factory=dict)
    parts: list[dict[str, Any]] = field(default_factory=list)
    tempo: dict[str, Any] = field(default_factory=dict)
    systems: list[SystemInfo] = field(default_factory=list)
    measures: list[MeasureInfo] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------ 属性
    @property
    def duration_ms(self) -> int:
        return int(self.audio.get("duration_ms", 0))

    @property
    def base_name(self) -> str:
        return str(self.source.get("base", ""))

    @property
    def note_count(self) -> int:
        return len(self.notes)

    @property
    def svg_files(self) -> list[str]:
        return [s.file for s in self.systems if s.file]

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source": self.source,
            "render": self.render,
            "audio": self.audio,
            "parts": self.parts,
            "tempo": self.tempo,
            "systems": [s.to_dict() for s in self.systems],
            "measures": [m.to_dict() for m in self.measures],
            "notes": self.notes,
        }

    def to_json(self, *, compact: bool = True) -> str:
        if compact:
            return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=1)

    def save(self, path: Path, *, compact: bool = True) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.to_json(compact=compact) + "\n", encoding="utf-8")
        tmp.replace(path)  # 原子替换，避免留下半成品（需求 §7.2）
        return path

    @classmethod
    def load(cls, path: Path) -> "SyncDoc":
        """读取并校验 ``sync.json``。

        Raises:
            SyncSchemaError: 文件缺失、JSON 非法或 schema 前缀不认识。
        """
        p = Path(path)
        if not p.is_file():
            raise SyncSchemaError(f"找不到同步数据文件：{p}")
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise SyncSchemaError(f"{p.name} 不是有效的 JSON：{e}") from e
        if not isinstance(raw, dict):
            raise SyncSchemaError(f"{p.name} 顶层应为 JSON 对象")

        schema = str(raw.get("schema", ""))
        if not schema.startswith("zpymusic-sync/"):
            raise SyncSchemaError(
                f"{p.name} 的 schema 不受支持：{schema!r}（期望 zpymusic-sync/*）"
            )

        return cls(
            schema=schema,
            source=dict(raw.get("source") or {}),
            render=dict(raw.get("render") or {}),
            audio=dict(raw.get("audio") or {}),
            parts=list(raw.get("parts") or []),
            tempo=dict(raw.get("tempo") or {}),
            systems=[SystemInfo.from_dict(d) for d in raw.get("systems") or []],
            measures=[MeasureInfo.from_dict(d) for d in raw.get("measures") or []],
            notes=[_expand_note(d) for d in raw.get("notes") or []],
        )


def _expand_note(d: dict[str, Any]) -> dict[str, Any]:
    """把紧凑键展开为长键，兼容文档里的长键写法。"""
    if not any(k in d for k in _COMPACT_KEYS):
        return dict(d)  # 已是长键
    out: dict[str, Any] = {"id": d.get("id", "")}
    out["onset_ms"] = int(d.get("s", d.get("onset_ms", 0)))
    out["dur_ms"] = int(d.get("d", d.get("dur_ms", 0)))
    if d.get("q"):
        out["qstamp"] = float(d["q"])
    if d.get("r"):
        out["is_rest"] = True
    if d.get("m"):
        out["measure"] = str(d["m"])
    if d.get("x"):
        out["rendered"] = True
    return out


def _compact_note(note: dict[str, Any]) -> dict[str, Any]:
    """把长键压缩为紧凑键（对外写出的形式）。"""
    out: dict[str, Any] = {
        "id": note["id"],
        "s": int(note["onset_ms"]),
        "d": int(note["dur_ms"]),
    }
    if note.get("qstamp"):
        out["q"] = round(float(note["qstamp"]), 6)
    if note.get("is_rest"):
        out["r"] = 1
    if note.get("measure"):
        out["m"] = note["measure"]
    if note.get("rendered"):
        out["x"] = 1
    return out


# --------------------------------------------------------------------------- 构造
def build_sync_doc(
    doc: MusicXMLDocument,
    rendered: RenderedScore,
    *,
    svg_names: dict[int, str],
    soundfont: str,
    audio: dict[str, Any],
    render_opts: dict[str, Any],
    sha1: str = "",
) -> SyncDoc:
    """把文档元数据 + 渲染结果组装成 ``SyncDoc``。

    Args:
        doc: 已规范化的 MusicXML 文档。
        rendered: Verovio 渲染结果。
        svg_names: ``{system_index: 相对文件名}``。
        soundfont: 音色库的相对路径（写入套件，保证可复现）。
        audio: ``AudioConfig`` 的有效取值（sample_rate / gain / reverb / chorus）。
        render_opts: 渲染选项快照（engine / scale / page_width / breaks ...）。
        sha1: 源文件摘要，用于识别套件是否与源同步。
    """
    for s in rendered.systems:
        s.file = svg_names.get(s.index, "")

    parts = [_part_dict(p) for p in doc.parts]
    tempo_marks = (
        [{"at_ms": 0, "bpm": rendered.initial_bpm}] if rendered.initial_bpm else []
    )

    sync = SyncDoc(
        schema=SYNC_SCHEMA,
        source={
            "file": doc.source.name,
            "base": doc.base_name,
            "sha1": sha1,
            "title": doc.title,
            "composer": doc.composer,
            "part_count": doc.part_count,
            "musicxml_version": doc.version,
        },
        render={
            **render_opts,
            "engine_version": rendered.engine_version,
            "system_count": len(rendered.systems),
            "svg_files": [s.file for s in rendered.systems if s.file],
        },
        audio={
            "soundfont": soundfont,
            "duration_ms": rendered.duration_ms,
            "offset_ms": 0.0,
            **audio,
        },
        parts=parts,
        tempo={
            "initial_bpm": rendered.initial_bpm,
            "tempo_marks": tempo_marks,
        },
        systems=rendered.systems,
        measures=rendered.measures,
        notes=[_compact_note(n.to_dict()) for n in rendered.notes],
    )
    if rendered.warnings:
        sync.source["warnings"] = list(rendered.warnings)
    return sync


def _part_dict(p: PartInfo) -> dict[str, Any]:
    from .gm import gm_program_name, guess_program_from_name  # noqa: PLC0415

    channel = p.midi_channel if p.midi_channel is not None else 1
    program = p.midi_program
    guessed = False
    if program is None:
        # 需求 F1.8：不静默猜测，但给出建议值，UI 必须让用户确认
        program = guess_program_from_name(p.name or p.instrument_name)
        guessed = program is not None

    d: dict[str, Any] = {
        "id": p.id,
        "name": p.name,
        "part_name": p.part_name,
        "instrument": p.instrument_name,
        "midi_channel": int(channel),
        "midi_program": int(program) if program is not None else 0,
        "program_name": gm_program_name(int(program)) if program is not None else "",
        "explicit_program": bool(p.program_was_explicit),
    }
    if guessed:
        d["program_guessed"] = True
    if p.is_percussion:
        d["percussion"] = True
    return d


def validate_sync_against_svg(sync: SyncDoc, read_svg: Any) -> list[str]:
    """校验 ``sync.notes`` 里的 ID 是否都能在 SVG 中找到（需求 §8.2 验收 2）。

    Args:
        sync: 待校验的同步数据。
        read_svg: ``Callable[[str], str]``，按文件名读取 SVG 文本。

    Returns:
        问题清单（空列表 = 全部一致）。为避免大乐谱内存爆炸，逐文件处理。
    """
    import re  # noqa: PLC0415

    problems: list[str] = []
    id_re = re.compile(r'id="([^"]+)"')
    for s in sync.systems:
        if not s.file:
            continue
        try:
            text = read_svg(s.file)
        except OSError as e:
            problems.append(f"无法读取 {s.file}：{e}")
            continue
        present = set(id_re.findall(text))
        missing = [i for i in s.note_ids if i not in present]
        if missing:
            problems.append(
                f"{s.file} 缺少 {len(missing)} 个音符元素（前 3 个：{missing[:3]}）"
            )
    return problems


def iter_note_tuples(notes: Iterable[dict[str, Any]]) -> Iterable[tuple[str, int, int]]:
    """把（可能是混合写法的）notes 统一成 ``(id, onset_ms, dur_ms)`` 序列。"""
    for d in notes:
        yield str(d["id"]), int(d.get("onset_ms", d.get("s", 0))), int(d.get("dur_ms", d.get("d", 0)))
