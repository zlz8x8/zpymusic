"""M5 格式转换引擎与界面的回归测试（需求 §5.4 F3.1–F3.8）。

分两层：

* **引擎**（:mod:`zpymusic.core.convert`，不依赖 Qt）：能力矩阵、依赖自检、命名模板、
  重名策略、跳过/覆盖、错误路径（P3 / 不支持 / 缺依赖 / 文件不存在），以及**真实转换**
  （musicxml → midi/pdf、midi → musicxml、mp3 → wav、svg → musicxml）——
  外部引擎缺失时对应用例自动跳过。
* **界面**（:mod:`zpymusic.ui.tab_convert`）：批量列表增删去重、混合格式取交集、
  依赖不足时禁止开始、后台批量执行与逐项状态、结果区按钮、保真度提示（F3.3）。

另外锁住两个实测踩到的坑：

1. **套件形态只对 ``musicxml`` 作源生效**：早期只按目标格式判断，
   于是 ``midi → wav`` 把输出目录当成了套件目录，最后 ``PermissionError``；
2. **调 MuseScore 必须清掉 ``QT_QPA_PLATFORM``**：继承 ``offscreen`` 会让
   MuseScore 4 直接崩溃（``returncode=0xC0000409``）。
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from zpymusic.common.config import AppConfig
from zpymusic.core import convert as cv
from zpymusic.core.suite import OverwritePolicy

REPO = Path(__file__).resolve().parents[1]
SUITES = REPO / "staff" / "suites"
MUSICXML_DIR = REPO / "staff" / "musicxml"
CANON_SUITE = SUITES / "canon-in-d-easy"

MXL = MUSICXML_DIR / "canon-in-d-easy.mxl"
MID = CANON_SUITE / "canon-in-d-easy.mid"
MP3 = CANON_SUITE / "canon-in-d-easy.mp3"
SVG = CANON_SUITE / "svg" / "sys-0001.svg"

needs_mxl = pytest.mark.skipif(not MXL.is_file(), reason="缺少 MusicXML 样本")
needs_suite = pytest.mark.skipif(not CANON_SUITE.is_dir(), reason="需要先生成 canon 套件")


def _config() -> AppConfig:
    cfg = AppConfig()
    cfg.ui.score_backend = "native"
    return cfg


def _env(**overrides) -> dict[str, cv.Requirement]:
    """造一份可控的依赖表（默认全 OK），避免测试受本机环境影响。"""
    names = ("verovio", "fluidsynth", "ffmpeg", "musescore", "music21", "pypdf", "soundfont")
    env = {n: cv.Requirement(n, True) for n in names}
    for k, v in overrides.items():
        env[k] = cv.Requirement(k, bool(v), "" if v else f"缺少 {k}")
    return env


# ===========================================================================
# 1. 格式判定 / 能力矩阵
# ===========================================================================
class TestFormats:
    @pytest.mark.parametrize(
        "name,fmt",
        [
            ("a.musicxml", "musicxml"), ("a.xml", "musicxml"), ("a.mxl", "musicxml"),
            ("a.mid", "midi"), ("a.midi", "midi"), ("a.svg", "svg"),
            ("a.pdf", "pdf"), ("a.mp3", "mp3"), ("a.wav", "wav"),
            ("A.MP3", "mp3"), ("a.txt", ""), ("无扩展名", ""),
        ],
    )
    def test_detect_format(self, name: str, fmt: str) -> None:
        assert cv.detect_format(Path(name)) == fmt

    def test_suffix_mapping(self) -> None:
        assert cv.format_suffix("midi") == ".mid"
        assert cv.format_suffix("musicxml") == ".musicxml"
        assert cv.format_suffix("mp3") == ".mp3"

    def test_suite_form_only_for_musicxml_source(self) -> None:
        """套件形态只对 musicxml 作源生效（midi → wav 是单文件）。"""
        assert cv.is_suite_form("musicxml", "mp3")
        assert cv.is_suite_form("musicxml", "wav")
        assert not cv.is_suite_form("midi", "wav")
        assert not cv.is_suite_form("mp3", "wav")
        assert not cv.is_suite_form("musicxml", "pdf")

    def test_matrix_covers_documented_pairs(self) -> None:
        for pair in (("musicxml", "pdf"), ("midi", "musicxml"), ("svg", "musicxml"),
                     ("pdf", "svg"), ("mp3", "wav"), ("wav", "mp3")):
            assert pair in cv.CONVERSIONS

    def test_iter_convertible_includes_audio_and_svg(self, tmp_path: Path) -> None:
        for name in ("a.musicxml", "b.mid", "c.mp3", "d.svg", "e.txt"):
            (tmp_path / name).write_bytes(b"x")
        names = [p.name for p in cv.iter_convertible(tmp_path)]
        assert names == ["a.musicxml", "b.mid", "c.mp3", "d.svg"], names


# ===========================================================================
# 2. 命名模板与重名策略（F3.7）
# ===========================================================================
class TestNaming:
    def test_default_is_source_base(self) -> None:
        assert cv.build_output_name("canon", "mp3") == "canon"

    def test_tokens(self) -> None:
        assert cv.build_output_name("canon", "mp3", "{base}_{format}") == "canon_mp3"
        assert cv.build_output_name("canon", "midi", "{base}.{ext}") == "canon.mid"
        today = time.strftime("%Y%m%d")
        assert cv.build_output_name("canon", "wav", "{base}-{date}") == f"canon-{today}"

    def test_unknown_token_is_rejected(self) -> None:
        with pytest.raises(cv.ConvertError) as e:
            cv.build_output_name("canon", "mp3", "{oops}")
        assert "{oops}" in str(e.value) and "{base}" in str(e.value)

    def test_path_separators_are_sanitized(self) -> None:
        """模板里塞路径分隔符也不能越出目标目录（``..`` 会被中和成普通字符）。"""
        name = cv.build_output_name("a", "mp3", "{base}/../evil")
        assert "/" not in name and "\\" not in name
        assert Path(name).name == name, "必须是单一文件名，不能带目录层级"

    def test_plan_output_single_file(self, tmp_path: Path) -> None:
        src = tmp_path / "song.mid"
        src.write_bytes(b"x")
        opts = cv.ConvertOptions(target="wav", out_dir=tmp_path)
        path, skip = cv.plan_output(src, opts)
        assert path == tmp_path / "song.wav" and not skip

    def test_plan_output_suite_dir(self, tmp_path: Path) -> None:
        """``musicxml → wav`` 的产物是**目录**（复用套件流水线）。"""
        src = tmp_path / "song.musicxml"
        src.write_bytes(b"x")
        opts = cv.ConvertOptions(target="wav", out_dir=tmp_path)
        path, _skip = cv.plan_output(src, opts)
        assert path == tmp_path / "song" and path.suffix == ""

    def test_overwrite_policies(self, tmp_path: Path) -> None:
        src = tmp_path / "song.mid"
        src.write_bytes(b"x")
        existing = tmp_path / "song.wav"
        existing.write_bytes(b"old")

        skip_path, skip = cv.plan_output(src, cv.ConvertOptions("wav", tmp_path, overwrite=OverwritePolicy.SKIP))
        assert skip and skip_path == existing

        ow_path, ow = cv.plan_output(
            src, cv.ConvertOptions("wav", tmp_path, overwrite=OverwritePolicy.OVERWRITE)
        )
        assert not ow and ow_path == existing

        nd_path, nd = cv.plan_output(
            src, cv.ConvertOptions("wav", tmp_path, overwrite=OverwritePolicy.NEWDIR)
        )
        assert not nd and nd_path.name == "song_2.wav"


# ===========================================================================
# 3. 依赖自检（F3.2）与保真度提示（F3.3）
# ===========================================================================
class TestRequirements:
    @pytest.mark.parametrize(
        "sfmt,tfmt,names",
        [
            ("musicxml", "svg", ["verovio"]),
            ("musicxml", "mp3", ["verovio", "fluidsynth", "soundfont", "ffmpeg"]),
            ("musicxml", "wav", ["verovio", "fluidsynth", "soundfont"]),
            ("musicxml", "pdf", ["musescore"]),
            ("midi", "musicxml", ["music21"]),
            ("midi", "mp3", ["fluidsynth", "soundfont", "ffmpeg"]),
            ("midi", "wav", ["fluidsynth", "soundfont"]),
            ("mp3", "wav", ["ffmpeg"]),
            ("wav", "mp3", ["ffmpeg"]),
            ("pdf", "svg", ["pypdf", "verovio"]),
        ],
    )
    def test_required_engines(self, sfmt: str, tfmt: str, names: list[str]) -> None:
        got = [r.name for r in cv.requirements_for(sfmt, tfmt, env=_env())]
        assert got == names

    def test_missing_reported(self) -> None:
        env = _env(ffmpeg=False, soundfont=False)
        missing = cv.missing_requirements("musicxml", "mp3", _config(), env)
        assert {r.name for r in missing} == {"ffmpeg", "soundfont"}
        assert all(r.hint for r in missing)

    def test_svg_to_musicxml_has_no_external_requirement(self) -> None:
        assert cv.requirements_for("svg", "musicxml", env=_env()) == []

    def test_environment_probe_shape(self) -> None:
        env = cv.check_environment(_config())
        assert set(env) == {"verovio", "fluidsynth", "ffmpeg", "musescore", "music21", "pypdf", "soundfont"}
        assert env["verovio"].ok, "本机装了 verovio"

    @pytest.mark.parametrize(
        "pair,prompt",
        [
            (("musicxml", "midi"), True),
            (("musicxml", "mp3"), True),
            (("midi", "musicxml"), True),
            (("svg", "musicxml"), True),
            (("mp3", "wav"), False),
            (("wav", "mp3"), False),
            (("musicxml", "svg"), False),
        ],
    )
    def test_fidelity_prompt_selection(self, pair: tuple[str, str], prompt: bool) -> None:
        assert cv.is_lossy(*pair) is prompt

    def test_fidelity_text_mentions_what_is_lost(self) -> None:
        assert "记谱" in cv.fidelity_note("musicxml", "midi")
        assert "推断" in cv.fidelity_note("midi", "musicxml")
        assert cv.fidelity_note("musicxml", "svg") == ""


# ===========================================================================
# 4. 错误路径（不依赖外部引擎）
# ===========================================================================
class TestErrorPaths:
    def _run(self, src: Path, target: str, tmp_path: Path, **kw) -> cv.ConvertResult:
        opts = cv.ConvertOptions(target=target, out_dir=tmp_path, **kw)
        return cv.convert_one(src, opts, _config(), env=_env())

    def test_missing_source(self, tmp_path: Path) -> None:
        r = self._run(tmp_path / "不存在.mid", "wav", tmp_path)
        assert r.status == "failed" and "不存在" in r.message

    def test_unknown_source_format(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        r = self._run(f, "wav", tmp_path)
        assert r.status == "failed" and "不认识的源格式" in r.message

    def test_unsupported_pair(self, tmp_path: Path) -> None:
        f = tmp_path / "a.mid"
        f.write_bytes(b"x")
        r = self._run(f, "svg", tmp_path)
        assert r.status == "failed" and "不支持" in r.message

    def test_p3_pair_refused(self, tmp_path: Path) -> None:
        f = tmp_path / "a.mp3"
        f.write_bytes(b"x")
        r = self._run(f, "musicxml", tmp_path)
        assert r.status == "failed" and "P3" in r.message

    def test_missing_dependency_blocks(self, tmp_path: Path) -> None:
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF-1.4")
        opts = cv.ConvertOptions(target="svg", out_dir=tmp_path)
        r = cv.convert_one(f, opts, _config(), env=_env(pypdf=False))
        assert r.status == "failed" and "pypdf" in r.message

    def test_bad_template(self, tmp_path: Path) -> None:
        f = tmp_path / "a.mid"
        f.write_bytes(b"x")
        r = self._run(f, "wav", tmp_path, name_template="{nope}")
        assert r.status == "failed" and "{nope}" in r.message

    def test_skip_when_exists(self, tmp_path: Path) -> None:
        f = tmp_path / "a.mid"
        f.write_bytes(b"x")
        (tmp_path / "a.wav").write_bytes(b"old")
        r = self._run(f, "wav", tmp_path, overwrite=OverwritePolicy.SKIP)
        assert r.status == "skipped" and "已存在" in r.message
        assert (tmp_path / "a.wav").read_bytes() == b"old"

    def test_svg_without_sidecar_is_refused(self, tmp_path: Path) -> None:
        """第三方 SVG（没有配套 MusicXML）→ 明确拒绝，不做"从图形反推乐谱"。"""
        svg = tmp_path / "score.svg"
        svg.write_text('<svg><g id="n1" class="note"/></svg>', encoding="utf-8")
        r = self._run(svg, "musicxml", tmp_path)
        assert r.status == "failed" and "MusicXML" in r.message

    def test_third_party_svg_is_refused(self, tmp_path: Path) -> None:
        svg = tmp_path / "plain.svg"
        svg.write_text("<svg><rect/></svg>", encoding="utf-8")
        r = self._run(svg, "musicxml", tmp_path)
        assert r.status == "failed" and "本工具生成" in r.message


# ===========================================================================
# 5. 真实转换（缺外部引擎则跳过）
# ===========================================================================
class TestRealConversions:
    def _run(self, src: Path, target: str, tmp_path: Path, **kw) -> cv.ConvertResult:
        opts = cv.ConvertOptions(target=target, out_dir=tmp_path, overwrite=OverwritePolicy.OVERWRITE, **kw)
        return cv.convert_one(src, opts, _config(), env=cv.check_environment(_config()))

    @needs_suite
    def test_mp3_to_wav(self, tmp_path: Path) -> None:
        if not shutil.which("ffmpeg"):
            pytest.skip("没有 ffmpeg")
        r = self._run(MP3, "wav", tmp_path)
        assert r.status == "ok", r.message
        assert r.outputs[0].name == "canon-in-d-easy.wav"
        assert r.outputs[0].stat().st_size > 1000

    @needs_mxl
    def test_musicxml_to_midi_is_suite_form(self, tmp_path: Path) -> None:
        if not cv.check_environment(_config())["verovio"].ok:
            pytest.skip("没有 verovio")
        r = self._run(MXL, "midi", tmp_path)
        assert r.status == "ok", r.message
        assert r.suite_dir is not None and r.suite_dir.is_dir()
        assert r.outputs == [r.suite_dir / "canon-in-d-easy.mid"]
        # 套件里应有 sync.json（可直接载入播放界面看谱）
        assert (r.suite_dir / "canon-in-d-easy.sync.json").is_file()
        assert not r.playable, "只有 MIDI 不算可播放（QMediaPlayer 放不了 .mid）"

    @needs_mxl
    def test_naming_template_applied_to_suite_dir(self, tmp_path: Path) -> None:
        r = self._run(MXL, "midi", tmp_path, name_template="{base}_{format}")
        assert r.status == "ok", r.message
        assert r.suite_dir is not None and r.suite_dir.name == "canon-in-d-easy_midi"
        # 套件内部同名产物也要跟着改名（否则播放页按 base 找不到文件）
        assert (r.suite_dir / "canon-in-d-easy_midi.mid").is_file()
        assert (r.suite_dir / "canon-in-d-easy_midi.sync.json").is_file()

    @needs_suite
    def test_midi_to_musicxml(self, tmp_path: Path) -> None:
        if not cv.check_environment(_config())["music21"].ok:
            pytest.skip("没有 music21")
        r = self._run(MID, "musicxml", tmp_path)
        assert r.status == "ok", r.message
        out = r.outputs[0]
        assert out.suffix == ".musicxml" and out.stat().st_size > 1000
        from zpymusic.core.musicxml_io import read_musicxml

        doc = read_musicxml(out)
        assert doc.part_count >= 1 and doc.title  # 标题不再是 "Music21 Fragment"

    @needs_mxl
    def test_musicxml_to_pdf_survives_offscreen_qt(self, tmp_path: Path, monkeypatch) -> None:
        """回归：``QT_QPA_PLATFORM=offscreen`` 会让 MuseScore 4 崩溃（0xC0000409）。

        修法是给子进程清掉这些 Qt 变量。这里**故意**把 offscreen 塞进环境变量，
        转换仍必须成功（若有人删掉 ``_child_env``，本用例会失败）。
        """
        env = cv.check_environment(_config())
        if not env["musescore"].ok:
            pytest.skip("没有 MuseScore 4")
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
        r = self._run(MXL, "pdf", tmp_path)
        assert r.status == "ok", r.message
        assert r.outputs[0].suffix == ".pdf" and r.outputs[0].stat().st_size > 1000

    def test_child_env_strips_qt_platform(self, monkeypatch) -> None:
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
        monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
        env = cv._child_env()
        assert "QT_QPA_PLATFORM" not in env
        assert env.get("QTWEBENGINE_CHROMIUM_FLAGS") == "--disable-gpu"

    @needs_suite
    def test_svg_to_musicxml_via_sidecar(self, tmp_path: Path) -> None:
        r = self._run(SVG, "musicxml", tmp_path)
        assert r.status == "ok", r.message
        assert r.outputs[0].suffix == ".musicxml" and r.outputs[0].stat().st_size > 1000
        assert any("复用配套的 MusicXML" in w for w in r.warnings)

    @needs_suite
    def test_suite_is_loadable_after_conversion(self, tmp_path: Path) -> None:
        """转换产出的套件要能被播放页直接载入（F3.6 的"载入播放界面"）。"""
        if not cv.check_environment(_config())["fluidsynth"].ok:
            pytest.skip("没有 FluidSynth")
        r = self._run(MXL, "wav", tmp_path)
        assert r.status == "ok", r.message
        assert r.playable and r.suite_dir is not None
        suite = cv.find_suite(r.suite_dir)
        assert suite is not None, "应能在父目录里发现该套件"
        assert suite.has_sync and suite.audio_files
        assert not suite.validate(), "套件完整性校验应通过"


# ===========================================================================
# 6. 界面（F3.2 / F3.3 / F3.4 / F3.6）
# ===========================================================================
class _LogDock:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    def add_note(self, level: str, text: str) -> None:
        self.notes.append((level, text))


def _tab(qapp, tmp_path: Path):  # noqa: ANN001, ANN202
    from zpymusic.ui.tab_convert import ConvertTab

    cfg = _config()
    cfg.ui.skip_fidelity_prompts = []
    tab = ConvertTab(cfg, _LogDock())
    tab.ed_out.setText(str(tmp_path))
    tab.cmb_overwrite.setCurrentIndex(1)  # 覆盖，便于重复运行
    return tab


def _pump(qapp, seconds: float = 0.4) -> None:
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)


def app_events(qapp) -> None:  # noqa: ANN001
    """跑几轮事件循环（让 combo / 面板的联动生效）。"""
    for _ in range(3):
        qapp.processEvents()


@needs_mxl
class TestConvertTabBatch:
    def test_add_and_dedupe(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        tab = _tab(qapp, tmp_path)
        assert tab.add_source(MXL, select=False)
        assert not tab.add_source(MXL, select=False), "同一文件不该重复入列"
        assert tab.table.rowCount() == 1
        assert tab.table.item(0, 1).text() == "等待"

    def test_unsupported_file_rejected(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        tab = _tab(qapp, tmp_path)
        bad = tmp_path / "a.txt"
        bad.write_text("x", encoding="utf-8")
        assert not tab.add_source(bad)
        assert tab.table.rowCount() == 0

    def test_remove_and_clear(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        tab = _tab(qapp, tmp_path)
        tab.add_source(MXL, select=False)
        tab.add_source(MID, select=False)
        tab.table.selectRow(0)
        tab._remove_selected()
        assert tab.table.rowCount() == 1
        tab.clear_list()
        assert tab.table.rowCount() == 0

    def test_mixed_formats_limit_targets_to_intersection(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        """列表里同时有 musicxml 与 midi 时，只有两者都支持的目标可选。

        （``musicxml → musicxml`` 不在能力矩阵里，因此 ``musicxml`` 目标也被排除。）
        """
        tab = _tab(qapp, tmp_path)
        tab.add_source(MXL, select=False)
        tab.add_source(MID, select=False)
        allowed = tab._allowed_targets()
        assert allowed == {"mp3", "wav"}, allowed
        # 选一个对 midi 无效的目标 → 自动切回可用目标
        tab.cmb_target.setCurrentText("pdf")
        qapp.processEvents()
        assert tab.cmb_target.currentText() in allowed

    def test_no_common_target_is_not_a_dead_end(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        """四种格式混排时交集为空 —— 不能把目标全禁掉，否则用户无路可走。

        改为放开全部目标 + 在依赖自检里逐个列出"哪个文件不支持当前目标"，
        并提示分批转换。
        """
        tab = _tab(qapp, tmp_path)
        tab.add_source(MXL, select=False)
        tab.add_source(MID, select=False)
        tab.add_source(MP3, select=False)
        tab.add_source(SVG, select=False)
        assert tab._common_targets_empty()
        assert tab._allowed_targets() == set(cv.TARGET_FORMATS)
        tab.cmb_target.setCurrentText("mp3")
        app_events(qapp)
        assert not tab.btn_convert.isEnabled(), "不该让用户按下必然失败的转换"
        text = tab.lbl_deps.text()
        assert "不支持" in text and "分批" in text

    def test_requirement_describe_hides_hint_when_ok(self) -> None:
        ok = cv.Requirement("verovio", True, "pip install verovio")
        bad = cv.Requirement("pypdf", False, "pip install pypdf")
        assert ok.describe() == "✓ verovio"
        assert bad.describe() == "✗ pypdf（pip install pypdf）"

    def test_target_must_be_valid_for_every_item(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        """即使目标被程序设成不支持的组合，也必须禁止开始并说明原因。"""
        tab = _tab(qapp, tmp_path)
        tab.add_source(MXL, select=False)
        tab.add_source(MID, select=False)
        # 绕过"自动切回可用目标"的兜底，直接构造非法组合
        tab.cmb_target.blockSignals(True)
        tab.cmb_target.setCurrentText("pdf")  # 对 midi 无效
        tab.cmb_target.blockSignals(False)
        tab._refresh_dependencies()
        assert not tab.btn_convert.isEnabled()
        assert "不支持" in tab.lbl_deps.text()

    def test_dependency_check_blocks_missing_engine(self, qapp, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        tab = _tab(qapp, tmp_path)
        tf = tmp_path / "x.pdf"
        tf.write_bytes(b"%PDF-1.4")
        tab.add_source(tf, select=False)
        tab.cmb_target.setCurrentText("svg")
        tab._refresh_dependencies()
        text = tab.lbl_deps.text()
        if cv.check_environment(_config())["pypdf"].ok:
            assert tab.btn_convert.isEnabled()
        else:
            assert not tab.btn_convert.isEnabled()
            assert "pypdf" in text

    def test_dependency_check_shows_ready_engines(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        tab = _tab(qapp, tmp_path)
        tab.add_source(MXL, select=False)
        tab.cmb_target.setCurrentText("midi")
        tab._refresh_dependencies()
        text = tab.lbl_deps.text()
        assert "依赖齐备" in text and "verovio" in text
        assert tab.btn_convert.isEnabled()

    def test_output_preview_uses_template(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        tab = _tab(qapp, tmp_path)
        tab.add_source(MXL, select=False)
        tab.cmb_target.setCurrentText("pdf")
        tab.ed_template.setText("{base}_{format}")
        qapp.processEvents()
        assert "canon-in-d-easy_pdf.pdf" in tab.lbl_out_preview.text()

        tab.ed_template.setText("{oops}")
        qapp.processEvents()
        assert "命名模板有误" in tab.lbl_out_preview.text()

    def test_batch_run_updates_status_and_summary(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        """后台批量：逐项状态 + 产物绝对路径 + 汇总（F3.4 / F3.5 / F3.6）。"""
        tab = _tab(qapp, tmp_path)
        tab.add_source(MXL, select=False)
        tab.cmb_target.setCurrentText("midi")  # 快（只渲染 SVG+MIDI）
        tab._skipped_pairs.add(("musicxml", "midi"))  # 跳过保真度对话框（另有专门用例）
        qapp.processEvents()
        assert tab.btn_convert.isEnabled()

        tab._start()
        assert tab._runner is not None and tab.btn_cancel.isEnabled()
        deadline = time.time() + 120
        while time.time() < deadline and tab._runner is not None:
            qapp.processEvents()
            time.sleep(0.05)

        assert tab._runner is None
        assert tab.table.item(0, 1).text() == "成功", tab.table.item(0, 3).text()
        assert str(tmp_path) in tab.table.item(0, 2).text()
        assert "成功 1" in tab.lbl_summary.text()
        tab.table.selectRow(0)
        qapp.processEvents()
        assert tab.btn_reveal.isEnabled()
        assert not tab.btn_load_play.isEnabled(), "midi 目标没有音频，不可载入播放"

    def test_convert_disabled_without_list(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        tab = _tab(qapp, tmp_path)
        tab._refresh_dependencies()
        assert not tab.btn_convert.isEnabled()
        assert "先选择源文件" in tab.lbl_deps.text()

    def test_result_buttons_follow_results(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        """载入播放界面按钮：只有产出带音频的套件时才可用（F3.6）。"""
        tab = _tab(qapp, tmp_path)
        tab.add_source(MXL, select=False)
        suite_dir = tmp_path / "demo"
        suite_dir.mkdir()
        audio = suite_dir / "demo.mp3"
        audio.write_bytes(b"x")
        tab._results[0] = cv.ConvertResult(
            source=MXL, target="mp3", status="ok", outputs=[audio], suite_dir=suite_dir
        )
        tab.table.selectRow(0)
        qapp.processEvents()
        assert tab.btn_load_play.isEnabled()
        seen: list[Path] = []
        tab.suite_ready.connect(seen.append)
        tab._load_selected_into_player()
        assert seen == [suite_dir]

    def test_fidelity_prompt_can_be_remembered(self, qapp, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        """F3.3：弹一次提示；勾"不再提示"后写进 tab 状态。"""
        from PySide6.QtWidgets import QMessageBox

        tab = _tab(qapp, tmp_path)
        tab.add_source(MXL, select=False)
        tab.cmb_target.setCurrentText("mp3")
        qapp.processEvents()

        asked: dict = {}

        def fake_exec(self):
            asked["text"] = self.informativeText()
            asked["has_checkbox"] = self.checkBox() is not None
            self.checkBox().setChecked(True)
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)
        assert tab._confirm_fidelity(tab._pending_jobs()) is True
        assert "丢失" in asked["text"] and asked["has_checkbox"]
        assert ("musicxml", "mp3") in tab._skipped_pairs
        # 记住之后再问就不弹了
        asked.clear()
        assert tab._confirm_fidelity(tab._pending_jobs()) is True
        assert not asked

    def test_fidelity_prompt_can_be_declined(self, qapp, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from PySide6.QtWidgets import QMessageBox

        tab = _tab(qapp, tmp_path)
        tab.add_source(MXL, select=False)
        tab.cmb_target.setCurrentText("mp3")
        monkeypatch.setattr(
            QMessageBox, "exec", lambda self: QMessageBox.StandardButton.No
        )
        assert tab._confirm_fidelity(tab._pending_jobs()) is False
        assert "取消" in tab.lbl_summary.text()

    def test_lossless_pair_does_not_prompt(self, qapp, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from PySide6.QtWidgets import QMessageBox

        tab = _tab(qapp, tmp_path)
        tab.add_source(MXL, select=False)
        tab.cmb_target.setCurrentText("svg")
        monkeypatch.setattr(
            QMessageBox, "exec", lambda self: pytest.fail("无损转换不该弹提示")
        )
        assert tab._confirm_fidelity(tab._pending_jobs()) is True
