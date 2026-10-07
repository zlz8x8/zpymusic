"""``sync.json`` 契约、MusicXML 读取、GM 音色匹配的单测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zpymusic.common.errors import MusicXMLError, SyncSchemaError
from zpymusic.core.gm import GM_PROGRAMS, gm_program_name, guess_program_from_name
from zpymusic.core.musicxml_io import local_name, normalize_root_name, read_musicxml
from zpymusic.core.sync_model import SYNC_SCHEMA, SyncDoc, build_sync_doc

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "staff" / "musicxml"
requires_samples = pytest.mark.skipif(
    not SAMPLES.is_dir() or not list(SAMPLES.glob("*.mxl")),
    reason="需要 staff/musicxml 下的样本",
)

# --------------------------------------------------------------------------- GM
class TestGM:
    def test_table_size(self) -> None:
        assert len(GM_PROGRAMS) == 128

    @pytest.mark.parametrize(
        "number,name",
        [
            (0, "Acoustic Grand Piano"),
            (40, "Violin"),
            (42, "Cello"),
            (56, "Trumpet"),
            (73, "Flute"),
            (127, "Gunshot"),
        ],
    )
    def test_names(self, number: int, name: str) -> None:
        assert gm_program_name(number) == name

    def test_out_of_range(self) -> None:
        assert gm_program_name(999) == "Program 999"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Violin", 40),
            ("Violin I", 40),
            ("violino", 40),
            ("Cello", 42),
            ("Violoncello", 42),
            ("Double Bass", 43),
            ("Contrabass", 43),
            ("Flute", 73),
            ("Flute 1", 73),
            ("Oboe", 68),
            ("Clarinet in Bb", 71),
            ("Trumpet in C", 56),
            ("French Horn", 60),
            ("Timpani", 47),
            ("Piano", None),  # 不在关键词表里 → 交由"未指定 → 默认钢琴"
            ("", None),
            ("???", None),
        ],
    )
    def test_guess(self, text: str, expected: int | None) -> None:
        assert guess_program_from_name(text) == expected

    def test_longer_keyword_wins(self) -> None:
        """'bass' 不能抢走 'double bass'。"""
        assert guess_program_from_name("Double Bass") == 43
        assert guess_program_from_name("Bass") == 32


# --------------------------------------------------------------------------- MusicXML
class TestMusicXMLHelpers:
    def test_local_name(self) -> None:
        assert local_name("{ns}note") == "note"
        assert local_name("note") == "note"

    @pytest.mark.parametrize(
        "filename,base",
        [
            ("a.mxl", "a"),
            ("a.musicxml", "a"),
            ("a.xml", "a"),
            ("A.MXL", "A"),
            ("no-ext", "no-ext"),
            ("a.b.mxl", "a.b"),
        ],
    )
    def test_normalize_root_name(self, filename: str, base: str) -> None:
        assert normalize_root_name(Path(filename)) == base


@requires_samples
class TestReadMusicXML:
    def test_all_samples_parse(self) -> None:
        """10 个样本必须全部解析成功（含 score.xml / lg-*.xml 两种容器命名）。"""
        files = sorted(SAMPLES.glob("*.mxl"))
        assert len(files) >= 10
        for f in files:
            doc = read_musicxml(f)
            assert doc.root_tag == "score-partwise", f.name
            assert doc.parts, f.name
            assert doc.base_name == f.stem, f.name
            assert doc.xml_bytes.startswith(b"<?xml"), f.name
            assert b"score-partwise" in doc.xml_bytes, f.name

    def test_container_root_naming_variants(self) -> None:
        """样本里既有 score.xml 也有 lg-*.xml，验证两种都能解出真正的根文件。"""
        names = {read_musicxml(f).source.name for f in sorted(SAMPLES.glob("*.mxl"))}
        assert "canon-in-d-easy.mxl" in names
        assert "the-four-seasons-complete.mxl" in names

    def test_part0_piano(self) -> None:
        doc = read_musicxml(SAMPLES / "canon-in-d-easy.mxl")
        assert doc.part_count == 1
        p = doc.parts[0]
        assert p.id == "P1"
        assert p.midi_program == 0  # MusicXML 写 1 → 归一为 GM 0
        assert p.midi_channel == 1
        assert p.program_was_explicit

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(MusicXMLError):
            read_musicxml(tmp_path / "nope.mxl")

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.mxl"
        f.write_bytes(b"")
        with pytest.raises(MusicXMLError):
            read_musicxml(f)

    def test_bad_zip(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.mxl"
        f.write_bytes(b"PK\x03\x04garbage-not-a-real-zip")
        with pytest.raises(MusicXMLError):
            read_musicxml(f)

    def test_bad_xml(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.musicxml"
        f.write_bytes(b"<?xml version='1.0'?><score-partwise><unclosed>")
        with pytest.raises(MusicXMLError):
            read_musicxml(f)

    def test_unsupported_root(self, tmp_path: Path) -> None:
        f = tmp_path / "weird.musicxml"
        f.write_bytes(b"<?xml version='1.0'?><something-else/>")
        with pytest.raises(MusicXMLError, match="根元素"):
            read_musicxml(f)

    def test_strips_doctype(self, tmp_path: Path) -> None:
        f = tmp_path / "dt.musicxml"
        f.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
            '"http://www.musicxml.org/dtds/partwise.dtd">\n'
            '<score-partwise version="3.1"><part-list><score-part id="P1">'
            "<part-name>Music</part-name></score-part></part-list>"
            '<part id="P1"><measure number="1"/></part></score-partwise>',
            encoding="utf-8",
        )
        doc = read_musicxml(f)
        assert doc.part_count == 1
        assert doc.version == "3.1"
        assert b"<!DOCTYPE" not in doc.xml_bytes

    def test_bom_utf8(self, tmp_path: Path) -> None:
        f = tmp_path / "bom.musicxml"
        f.write_bytes(
            b"\xef\xbb\xbf" + b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<score-partwise><part-list><score-part id="P1">'
            b"<part-name>X</part-name></score-part></part-list>"
            b'<part id="P1"><measure number="1"/></part></score-partwise>'
        )
        doc = read_musicxml(f)
        assert doc.part_count == 1

    def test_timewise_converted_to_partwise(self, tmp_path: Path) -> None:
        """score-timewise 必须先转成 partwise（需求 §5.1.3 步骤 2）。"""
        f = tmp_path / "timewise.musicxml"
        f.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<score-timewise><part-list>'
            '<score-part id="P1"><part-name>One</part-name></score-part>'
            '<score-part id="P2"><part-name>Two</part-name></score-part>'
            "</part-list>"
            '<measure number="1">'
            '<part id="P1"><note><rest/></note></part>'
            '<part id="P2"><note><rest/></note></part>'
            "</measure>"
            '<measure number="2">'
            '<part id="P1"><note><rest/></note></part>'
            '<part id="P2"><note><rest/></note></part>'
            "</measure>"
            "</score-timewise>",
            encoding="utf-8",
        )
        doc = read_musicxml(f)
        assert doc.root_tag == "score-partwise"
        assert doc.part_count == 2
        assert any("timewise" in w for w in doc.warnings)
        # 转置后：partwise/part[@id]/measure 各 2 个
        import xml.etree.ElementTree as ET

        root = ET.fromstring(doc.xml_bytes)
        parts = [e for e in root if local_name(e.tag) == "part"]
        assert len(parts) == 2
        for p in parts:
            measures = [e for e in p if local_name(e.tag) == "measure"]
            assert len(measures) == 2
            assert [m.get("number") for m in measures] == ["1", "2"]

    def test_multi_part_channels_assigned(self, tmp_path: Path) -> None:
        """未指定 midi-channel 时按序推断，并跳过打击乐通道 10。"""
        f = tmp_path / "multi.musicxml"
        sps = "".join(
            f'<score-part id="P{i}"><part-name>Part {i}</part-name></score-part>'
            for i in range(1, 13)
        )
        parts = "".join(f'<part id="P{i}"><measure number="1"/></part>' for i in range(1, 13))
        f.write_text(
            f'<?xml version="1.0"?><score-partwise><part-list>{sps}</part-list>{parts}</score-partwise>',
            encoding="utf-8",
        )
        doc = read_musicxml(f)
        channels = [p.midi_channel for p in doc.parts]
        assert 10 not in channels, f"不应占用打击乐通道：{channels}"
        assert len(set(channels)) == 12


# --------------------------------------------------------------------------- sync.json
def _fake_rendered():
    """构造一个最小的 RenderedScore 用于 sync 构造/读写测试。"""
    from zpymusic.core.score_render import MeasureInfo, RenderedScore, SystemInfo, TimelineNote

    return RenderedScore(
        notes=[
            TimelineNote(id="n1", onset_ms=0, dur_ms=300, measure_id="m1"),
            TimelineNote(id="n2", onset_ms=300, dur_ms=300, measure_id="m1"),
            TimelineNote(id="n3-rend2", onset_ms=600, dur_ms=300, measure_id="m2", rendered=True),
        ],
        systems=[
            SystemInfo(index=1, file="", width=840, height=186, first_id="n1",
                       last_id="n2", note_ids=["n1", "n2"]),
            SystemInfo(index=2, file="", width=840, height=190, first_id="n3",
                       last_id="n3", note_ids=["n3"]),
        ],
        measures=[MeasureInfo(id="m1", onset_ms=0), MeasureInfo(id="m2", onset_ms=600)],
        midi_bytes=b"MThd",
        duration_ms=900,
        initial_bpm=100.0,
        engine_version="6.3.0",
    )


def _fake_doc():
    from zpymusic.core.musicxml_io import MusicXMLDocument, PartInfo

    return MusicXMLDocument(
        source=Path("x.mxl"),
        base_name="x",
        root_tag="score-partwise",
        title="T",
        parts=[PartInfo(id="P1", name="Piano", midi_channel=1, midi_program=0)],
        xml_bytes=b"<score-partwise/>",
    )


class TestSyncDoc:
    def _doc(self) -> SyncDoc:
        return build_sync_doc(
            _fake_doc(),
            _fake_rendered(),
            svg_names={1: "svg/sys-0001.svg", 2: "svg/sys-0002.svg"},
            soundfont="sound/x.sf3",
            audio={"sample_rate": 44100, "gain": 0.6},
            render_opts={"engine": "verovio", "scale": 40},
            sha1="abc",
        )

    def test_schema_and_roundtrip(self, tmp_path: Path) -> None:
        doc = self._doc()
        assert doc.schema == SYNC_SCHEMA
        p = tmp_path / "x.sync.json"
        doc.save(p)
        loaded = SyncDoc.load(p)

        assert loaded.schema == doc.schema
        assert loaded.duration_ms == 900
        assert loaded.note_count == 3
        assert [s.file for s in loaded.systems] == ["svg/sys-0001.svg", "svg/sys-0002.svg"]
        assert [m.id for m in loaded.measures] == ["m1", "m2"]
        assert loaded.parts[0]["midi_program"] == 0
        assert loaded.audio["soundfont"] == "sound/x.sf3"

    def test_compact_keys_written(self, tmp_path: Path) -> None:
        p = tmp_path / "x.sync.json"
        self._doc().save(p)
        raw = json.loads(p.read_text(encoding="utf-8"))
        first = raw["notes"][0]
        assert set(first) <= {"id", "s", "d", "q", "r", "m", "x"}
        assert first["s"] == 0 and first["d"] == 300

    def test_long_keys_still_readable(self, tmp_path: Path) -> None:
        """兼容文档初稿的长键写法。"""
        p = tmp_path / "long.sync.json"
        p.write_text(
            json.dumps(
                {
                    "schema": "zpymusic-sync/1.0",
                    "audio": {"duration_ms": 500},
                    "notes": [{"id": "n", "onset_ms": 10, "dur_ms": 20, "is_rest": True}],
                }
            ),
            encoding="utf-8",
        )
        doc = SyncDoc.load(p)
        assert doc.notes[0]["onset_ms"] == 10
        assert doc.notes[0]["is_rest"] is True

    def test_rendered_flag_inferred_on_load(self, tmp_path: Path) -> None:
        doc = self._doc()
        assert any(n.get("x") for n in doc.notes)
        p = tmp_path / "x.sync.json"
        doc.save(p)
        loaded = SyncDoc.load(p)
        rendered = [n for n in loaded.notes if n.get("rendered")]
        assert len(rendered) == 1

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(SyncSchemaError, match="找不到"):
            SyncDoc.load(tmp_path / "nope.sync.json")

    def test_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.sync.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(SyncSchemaError, match="JSON"):
            SyncDoc.load(p)

    def test_unknown_schema_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "other.sync.json"
        p.write_text(json.dumps({"schema": "something/1.0"}), encoding="utf-8")
        with pytest.raises(SyncSchemaError, match="schema"):
            SyncDoc.load(p)

    def test_guessed_program_marked(self) -> None:
        """源文件没写 midi-program 时，猜测值必须被标记（需求 F1.8：不静默猜测）。"""
        from zpymusic.core.musicxml_io import MusicXMLDocument, PartInfo

        doc = MusicXMLDocument(
            source=Path("v.mxl"),
            base_name="v",
            root_tag="score-partwise",
            parts=[PartInfo(id="P1", name="Violin", midi_channel=1, midi_program=None)],
            xml_bytes=b"",
        )
        sync = build_sync_doc(
            doc, _fake_rendered(), svg_names={}, soundfont="s", audio={}, render_opts={}
        )
        part = sync.parts[0]
        assert part["explicit_program"] is False
        assert part["midi_program"] == 40  # Violin
        assert part.get("program_guessed") is True

    def test_validate_against_svg(self, tmp_path: Path) -> None:
        from zpymusic.core.sync_model import validate_sync_against_svg

        doc = self._doc()
        files = {
            "svg/sys-0001.svg": '<svg><g id="n1"/><g id="n2"/></svg>',
            "svg/sys-0002.svg": '<svg><g id="n3"/></svg>',
        }
        problems = validate_sync_against_svg(doc, lambda n: files[n])
        assert problems == []

        files["svg/sys-0002.svg"] = "<svg></svg>"
        problems = validate_sync_against_svg(doc, lambda n: files[n])
        assert problems and "sys-0002" in problems[0]
