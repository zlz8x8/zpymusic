"""M5 需求变更：源文件「预览」按钮 + 两类非模态预览窗口的回归测试。

需求原文：源文件「选择」按钮**左侧**增加「预览」按钮；没选文件时不可用，
选了 ``musicxml/svg/midi/mp3`` 才可用；MusicXML/SVG → 非模态窗口**显示**；
MIDI/MP3 → 非模态窗口**播放**（有停止/播放按钮）。

本文件覆盖：

* :func:`~zpymusic.ui.preview.preview_kind` 的格式判定（含 wav/pdf 明确不支持）；
* 「预览」按钮的位置（在「选择…」左侧）、初始置灰、按后缀启用/禁用与原因 tooltip；
* 窗口是非模态的、按类型复用（不堆叠）；
* SVG 预览真的把 SVG 交给曲谱视图渲染；
* MusicXML 预览走 Verovio 渲染（工作线程）→ 载入渲染结果；
* 渲染失败时**退回显示源码文本**（不出现空白窗口）；
* MIDI 走"合成 WAV 再播放"（QMediaPlayer 放不了 .mid，实测 FormatError），
  MP3 直接播放；播放/停止按钮都在且可用。

**顺带锁住一个 Verovio 的坑**：主线程先渲染过之后，工作线程里的 toolkit 会找不到
字体资源（``loadData`` 失败）—— 见 :func:`test_worker_thread_render_with_resources`。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from zpymusic.common.config import AppConfig
from zpymusic.ui.preview import (
    PREVIEW_AUDIO_SUFFIXES,
    PREVIEW_SCORE_SUFFIXES,
    AudioPreviewWindow,
    ScorePreviewWindow,
    preview_kind,
)

REPO = Path(__file__).resolve().parents[1]
SUITES = REPO / "staff" / "suites"
MUSICXML_DIR = REPO / "staff" / "musicxml"
CANON = SUITES / "canon-in-d-easy"

needs_suite = pytest.mark.skipif(
    not (CANON / "canon-in-d-easy.sync.json").is_file(), reason="需要先生成 canon 套件"
)
needs_musicxml = pytest.mark.skipif(
    not (MUSICXML_DIR / "canon-in-d-easy.mxl").is_file(), reason="缺少 MusicXML 样本"
)


def _config(tmp_path: Path | None = None) -> AppConfig:
    cfg = AppConfig()
    cfg.ui.score_backend = "native"  # 测试里不走 WebEngine（本机也不可用）
    if tmp_path is not None:
        cfg.suites_dir = str(tmp_path)
    return cfg


class _LogDock:
    """假日志区：只记录 ``add_note``。"""

    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    def add_note(self, level: str, text: str) -> None:
        self.notes.append((level, text))


# ===========================================================================
# 1. 格式判定
# ===========================================================================
class TestPreviewKind:
    @pytest.mark.parametrize("name", ["a.musicxml", "a.xml", "a.mxl", "A.MXL", "a.svg", "a.SVG"])
    def test_score_formats(self, name: str) -> None:
        assert preview_kind(name) == "score"

    @pytest.mark.parametrize("name", ["a.mid", "a.midi", "a.mp3", "A.MP3"])
    def test_audio_formats(self, name: str) -> None:
        assert preview_kind(name) == "audio"

    @pytest.mark.parametrize("name", ["a.wav", "a.pdf", "a.txt", "没有扩展名", ""])
    def test_unsupported_formats(self, name: str) -> None:
        """需求只点名四种：wav / pdf 等一律不可预览（按钮据此置灰）。"""
        assert preview_kind(name) == ""

    def test_suffix_sets_match_requirement(self) -> None:
        assert PREVIEW_SCORE_SUFFIXES == {".musicxml", ".xml", ".mxl", ".svg"}
        assert PREVIEW_AUDIO_SUFFIXES == {".mid", ".midi", ".mp3"}

    def test_accepts_path_objects(self) -> None:
        assert preview_kind(Path("x/y/z.mid")) == "audio"


class TestPreviewCache:
    def test_prunes_old_entries(self, tmp_path: Path, monkeypatch) -> None:
        """预览缓存不能无限增长：只保留最近使用的若干项。"""
        import os
        import time

        import zpymusic.ui.preview as preview_mod

        monkeypatch.setattr(preview_mod, "PREVIEW_DIR", tmp_path)
        audio = tmp_path / "audio"
        audio.mkdir(parents=True)
        for i in range(5):
            f = audio / f"clip-{i}.wav"
            f.write_bytes(b"x")
            os.utime(f, (time.time() - (10 - i) * 60, time.time() - (10 - i) * 60))

        preview_mod._prune_cache("audio", keep=2)
        left = sorted(p.name for p in audio.iterdir())
        assert left == ["clip-3.wav", "clip-4.wav"], f"应只留最新的两个，实际 {left}"

    def test_prune_handles_missing_dir(self, tmp_path: Path, monkeypatch) -> None:
        import zpymusic.ui.preview as preview_mod

        monkeypatch.setattr(preview_mod, "PREVIEW_DIR", tmp_path / "不存在")
        preview_mod._prune_cache("score")  # 不该抛异常


# ===========================================================================
# 2. 「转换」页的按钮：位置 / 可用性 / 原因
# ===========================================================================
def _convert_tab(qapp):  # noqa: ANN001, ANN202
    from zpymusic.ui.tab_convert import ConvertTab

    return ConvertTab(_config(), _LogDock())


class TestPreviewButton:
    def test_button_is_left_of_pick_button(self, qapp) -> None:  # noqa: ANN001
        tab = _convert_tab(qapp)
        gv = tab.btn_preview.parentWidget().layout()
        row = next(
            gv.itemAt(i).layout()
            for i in range(gv.count())
            if gv.itemAt(i).layout() is not None
            and gv.itemAt(i).layout().indexOf(tab.btn_preview) >= 0
        )
        idx_preview = row.indexOf(tab.btn_preview)
        idx_pick = next(
            row.indexOf(row.itemAt(i).widget())
            for i in range(row.count())
            if row.itemAt(i).widget() is not None and row.itemAt(i).widget().text() == "选择…"
        )
        assert idx_preview < idx_pick, "「预览」必须在「选择…」左侧"

    def test_disabled_without_source(self, qapp) -> None:  # noqa: ANN001
        tab = _convert_tab(qapp)
        assert not tab.btn_preview.isEnabled()
        assert "源文件" in tab.btn_preview.toolTip()

    @pytest.mark.parametrize("suffix", [".musicxml", ".svg", ".mid", ".mp3"])
    def test_enabled_for_supported_formats(self, qapp, tmp_path: Path, suffix: str) -> None:  # noqa: ANN001
        tab = _convert_tab(qapp)
        tab.ed_source.setText(str(tmp_path / f"demo{suffix}"))
        assert tab.btn_preview.isEnabled(), f"{suffix} 应该可以预览"

    @pytest.mark.parametrize("suffix", [".wav", ".pdf", ".txt"])
    def test_disabled_for_other_formats_with_reason(self, qapp, tmp_path: Path, suffix: str) -> None:  # noqa: ANN001
        tab = _convert_tab(qapp)
        tab.ed_source.setText(str(tmp_path / f"demo{suffix}"))
        assert not tab.btn_preview.isEnabled()
        assert "暂不支持预览" in tab.btn_preview.toolTip()

    def test_disabled_again_when_cleared(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        tab = _convert_tab(qapp)
        tab.ed_source.setText(str(tmp_path / "a.mp3"))
        assert tab.btn_preview.isEnabled()
        tab.ed_source.setText("")
        assert not tab.btn_preview.isEnabled()

    def test_click_without_source_does_nothing(self, qapp) -> None:  # noqa: ANN001
        tab = _convert_tab(qapp)
        tab.btn_preview.click()
        assert tab._score_preview is None and tab._audio_preview is None


# ===========================================================================
# 3. 乐谱预览窗口（SVG / MusicXML）
# ===========================================================================
@needs_suite
class TestScorePreviewWindow:
    def test_svg_preview_is_non_modal_and_loads_view(self, qapp) -> None:  # noqa: ANN001
        from PySide6.QtCore import Qt

        doc = _config()
        win = ScorePreviewWindow(doc, None, _LogDock())
        try:
            svg = CANON / "svg" / "sys-0001.svg"
            win.open_file(svg)
            qapp.processEvents()

            assert win.isVisible()
            assert not win.isModal(), "需求要求非模态窗口"
            assert win.windowModality() == Qt.WindowModality.NonModal
            assert svg.name in win.windowTitle()
            assert win.source == svg
            assert win.stack.currentIndex() == 0, "应显示乐谱页而不是文本页"
            # 视图真的载入了这一行，且尺寸取自 SVG（否则"适应宽度"会按 840 当成页宽）
            assert len(win.score_view.systems) == 1
            ref = win.score_view.systems[0]
            assert ref.width > 800 and ref.height > 200
            assert win.score_view.loaded_system_count() >= 1
        finally:
            win.close()

    @needs_musicxml
    def test_musicxml_preview_renders_in_background(
        self, qapp, tmp_path: Path, monkeypatch
    ) -> None:  # noqa: ANN001
        """渲染在工作线程里做（Job），完成后载入前若干行。"""
        import zpymusic.ui.preview as preview_mod

        monkeypatch.setattr(preview_mod, "PREVIEW_DIR", tmp_path)  # 用空缓存，确保真的渲染

        cfg = _config()
        logs = _LogDock()
        win = ScorePreviewWindow(cfg, None, logs, max_systems=2)
        try:
            win.open_file(MUSICXML_DIR / "canon-in-d-easy.mxl")
            for _ in range(400):  # 等后台渲染（实测 ~0.2 s）
                qapp.processEvents()
                if win._job is None:
                    break
                threading.Event().wait(0.02)
            assert win._job is None, "渲染任务应已结束"

            assert win.stack.currentIndex() == 0, f"渲染应成功，状态：{win.lbl_status.text()}"
            assert len(win.score_view.systems) == 2, "应按 max_systems 截断"
            assert (tmp_path / "score").is_dir(), "应把渲染结果写进预览缓存目录"
        finally:
            win.close()
            win.shutdown()

    def test_render_failure_falls_back_to_source_text(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        """不是乐谱的 .xml → 不弹空白窗口，改为显示源码 + 原因。"""
        bad = tmp_path / "broken.musicxml"
        bad.write_text("<score-partwise><garbage></score-partwise>", encoding="utf-8")
        win = ScorePreviewWindow(_config(), None, _LogDock(), max_systems=1)
        try:
            win.open_file(bad)
            for _ in range(200):
                qapp.processEvents()
                if win._job is None:
                    break
                threading.Event().wait(0.02)
            win.shutdown()
            assert win.stack.currentIndex() == 1, "应退回文本页"
            assert "score-partwise" in win.text.toPlainText(), "应能看到源码内容"
            assert "失败" in win.lbl_status.text()
        finally:
            win.close()

    def test_missing_file_shows_message(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        win = ScorePreviewWindow(_config(), None, _LogDock())
        try:
            win.open_file(tmp_path / "不存在.musicxml")
            assert win.isVisible()
            assert "不存在" in win.text.toPlainText()
        finally:
            win.close()

    def test_reuse_same_window(self, qapp) -> None:  # noqa: ANN001
        tab = _convert_tab(qapp)
        tab.ed_source.setText(str(CANON / "svg" / "sys-0001.svg"))
        tab.btn_preview.click()
        qapp.processEvents()
        first = tab._score_preview
        tab.btn_preview.click()
        qapp.processEvents()
        assert tab._score_preview is first, "同一类型应复用窗口而不是每次新建"

    def test_switching_kind_uses_other_window(self, qapp) -> None:  # noqa: ANN001
        tab = _convert_tab(qapp)
        tab.ed_source.setText(str(CANON / "svg" / "sys-0001.svg"))
        tab.btn_preview.click()
        qapp.processEvents()
        score_win = tab._score_preview
        tab.ed_source.setText(str(CANON / "canon-in-d-easy.mp3"))
        tab.btn_preview.click()
        qapp.processEvents()
        assert score_win is not None and score_win.source == CANON / "svg" / "sys-0001.svg"
        assert tab._audio_preview is not None
        assert tab._audio_preview.source == CANON / "canon-in-d-easy.mp3"
        tab.shutdown_previews()


# ===========================================================================
# 4. 音频预览窗口（MIDI / MP3）
# ===========================================================================
@needs_suite
class TestAudioPreviewWindow:
    def test_mp3_preview_plays_directly(self, qapp) -> None:  # noqa: ANN001
        mp3 = CANON / "canon-in-d-easy.mp3"
        win = AudioPreviewWindow(_config(), None, _LogDock(), autoplay=False)
        try:
            win.open_file(mp3)
            assert win.isVisible() and not win.isModal()
            assert mp3.name in win.windowTitle()
            assert win.btn_play.isEnabled() and win.btn_stop.isEnabled()
            assert "播放" in win.btn_play.text() and "停止" in win.btn_stop.text()
            # 直接播放 MP3 本体（不合成）
            assert win.player.info.path == mp3
            for _ in range(100):  # 等媒体时长就绪
                qapp.processEvents()
                if win.player.duration() > 0:
                    break
                threading.Event().wait(0.02)
            assert win.player.duration() > 0
            assert win.lbl_total.text() != "00:00.0"
        finally:
            win.close()
            win.shutdown()

    def test_midi_is_synthesized_before_playing(
        self, qapp, tmp_path: Path, monkeypatch
    ) -> None:  # noqa: ANN001
        """QMediaPlayer 放不了 .mid（实测 FormatError）→ 必须先合成成 WAV。

        这里把合成函数换成"写一个假 WAV"，只验证**决策与状态机**，
        不真的跑 FluidSynth（那要几秒）。
        """
        import zpymusic.ui.preview as preview_mod

        monkeypatch.setattr(preview_mod, "PREVIEW_DIR", tmp_path)
        mid = CANON / "canon-in-d-easy.mid"
        calls: list[Path] = []
        win = AudioPreviewWindow(_config(), None, _LogDock(), autoplay=False)
        try:
            def fake_synth(path: Path, wav: Path, tools, soundfont) -> Path:  # noqa: ANN001
                calls.append(path)
                wav.parent.mkdir(parents=True, exist_ok=True)
                wav.write_bytes(b"RIFF0000WAVEfmt ")
                return wav

            win._synth_worker = fake_synth  # type: ignore[method-assign]
            win.open_file(mid)
            assert win.bar.isVisible(), "合成期间应显示不确定进度条"
            for _ in range(300):
                qapp.processEvents()
                if win._job is None:
                    break
                threading.Event().wait(0.02)
            win.shutdown()
            assert calls == [mid], "MIDI 必须走合成路径"
            assert win.btn_play.isEnabled(), "合成完成后应可以播放"
            assert win.player.info.path is not None
            assert win.player.info.path.suffix == ".wav"
            assert win.player.info.path.parent == tmp_path / "audio"
        finally:
            win.close()

    def test_midi_without_fluidsynth_explains_why(
        self, qapp, tmp_path: Path, monkeypatch
    ) -> None:  # noqa: ANN001
        import zpymusic.ui.preview as preview_mod

        monkeypatch.setattr(preview_mod, "PREVIEW_DIR", tmp_path)  # 避开已有缓存

        class _NoTools:
            can_render_wav = False

        monkeypatch.setattr(preview_mod, "resolve_tools", lambda *a, **k: _NoTools())
        win = AudioPreviewWindow(_config(), None, _LogDock(), autoplay=False)
        try:
            win.open_file(CANON / "canon-in-d-easy.mid")
            assert not win.btn_play.isEnabled()
            assert "fluidsynth" in win.lbl_status.text().lower()
        finally:
            win.close()

    def test_stop_button_resets_transport(self, qapp) -> None:  # noqa: ANN001
        win = AudioPreviewWindow(_config(), None, _LogDock(), autoplay=False)
        try:
            win.open_file(CANON / "canon-in-d-easy.mp3")
            win.play()
            qapp.processEvents()
            win.slider.setValue(500)
            win._on_slider_released()
            win.stop()
            assert win.slider.value() == 0
            assert win.lbl_time.text() == "00:00.0"
            assert "播放" in win.btn_play.text()
        finally:
            win.close()
            win.shutdown()

    def test_missing_audio_file_shows_message(self, qapp, tmp_path: Path) -> None:  # noqa: ANN001
        win = AudioPreviewWindow(_config(), None, _LogDock(), autoplay=False)
        try:
            win.open_file(tmp_path / "没有这个.mp3")
            assert win.isVisible()
            assert "不存在" in win.lbl_status.text()
            assert not win.btn_play.isEnabled()
        finally:
            win.close()


# ===========================================================================
# 5. Verovio 工作线程渲染（M5 定位到的坑）
# ===========================================================================
@needs_musicxml
class TestWorkerThreadRender:
    def test_worker_thread_render_with_resources(self, qapp) -> None:  # noqa: ANN001
        """主线程渲染过之后，工作线程仍必须能渲染。

        修复前：工作线程里的 toolkit 找不到字体资源，``loadData()`` 失败
        （``Bravura font could not be loaded``）——GUI 里"生成套件 / 预览"整条链路都会挂。
        """
        from zpymusic.core.musicxml_io import read_musicxml
        from zpymusic.core.score_render import (
            render_preview_svgs,
            verovio_resource_path,
        )

        doc = read_musicxml(MUSICXML_DIR / "canon-in-d-easy.mxl")
        resources = verovio_resource_path()
        assert resources and Path(resources).is_dir(), "应能找到 verovio 自带的资源目录"

        main_svgs = render_preview_svgs(  # 主线程先渲染一次（制造"污染"条件）
            doc.xml_bytes, _config().render, max_systems=1, resources=resources
        )
        assert len(main_svgs) == 1

        out: dict = {}

        def work() -> None:
            try:
                svgs = render_preview_svgs(
                    doc.xml_bytes, _config().render, max_systems=1, resources=resources
                )
                out["n"] = len(svgs)
            except Exception as e:  # noqa: BLE001
                out["err"] = f"{type(e).__name__}: {e}"

        t = threading.Thread(target=work)
        t.start()
        t.join(180)
        assert "err" not in out, f"工作线程渲染失败：{out.get('err')}"
        assert out.get("n") == 1

    def test_sanitize_layout_for_preview(self) -> None:
        """预览渲染必须把资源目录传下去（否则上面那个坑会复现）。"""
        import inspect

        from zpymusic.ui.preview import ScorePreviewWindow

        src = inspect.getsource(ScorePreviewWindow._render_worker)
        assert "resources=" in src

    @needs_musicxml
    def test_preview_forces_line_layout(self) -> None:
        """配置里开了 single_page（单页连续）时，预览仍按行渲染（不生成单页巨图）。"""
        from zpymusic.core.musicxml_io import read_musicxml
        from zpymusic.core.score_render import render_preview_svgs

        cfg = _config().render
        cfg.single_page = True
        doc = read_musicxml(MUSICXML_DIR / "canon-in-d-easy.mxl")
        svgs = render_preview_svgs(doc.xml_bytes, cfg, max_systems=2)
        assert len(svgs) == 2, "预览应逐行排版（single_page=True 会得到 1 个巨页）"
