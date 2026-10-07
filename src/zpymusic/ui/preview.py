"""源文件预览（需求 §5.4.2 F3.8，M5 需求变更）。

「格式转换」页的源文件行新增一个 **预览** 按钮：没有选文件时不可用，
选了 ``MusicXML / SVG / MIDI / MP3`` 才可用。点开后弹出一个**非模态**窗口：

* **乐谱类（MusicXML / SVG）→** :class:`ScorePreviewWindow` 显示乐谱。
  SVG 直接渲染；MusicXML 先用 Verovio 渲染前 :data:`~zpymusic.core.score_render.PREVIEW_MAX_SYSTEMS`
  行到缓存目录再显示（复用播放页那套"适应宽度 / 缩放 / 按行懒加载"）。
  渲染失败（文件损坏、不是乐谱等）时**退回显示源码文本**并说明原因 ——
  "显示文件"两种理解都能满足，也绝不出现空白窗口。
* **音频类（MIDI / MP3）→** :class:`AudioPreviewWindow` 播放（播放 / 停止按钮 + 进度条）。

两条实测结论决定了这里的实现（本机 Windows + PySide6 6.9.3）：

1. ``QMediaPlayer`` **放不了 .mid**（``FormatError: Could not open file``，MediaStatus=InvalidMedia）
   → MIDI 必须先用 FluidSynth 合成成 WAV 再交给播放器；合成结果按
   ``路径 + mtime + size`` 缓存在 :data:`~zpymusic.common.paths.PREVIEW_DIR`，重复预览秒开。
2. 渲染 / 合成都不能在 UI 线程做（Verovio 与 FluidSynth 都是秒级阻塞）
   → 都走 :class:`~zpymusic.ui.job.Job`（QThread + 信号回主线程）。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..common.config import AppConfig
from ..common.log import get_logger
from ..common.paths import PREVIEW_DIR, PROJECT_ROOT
from ..core.audio_render import midi_to_wav, resolve_tools
from ..core.musicxml_io import read_musicxml
from ..core.score_render import PREVIEW_MAX_SYSTEMS, render_preview_svgs, svg_size
from ..sync.player import PlayerState, QtMediaPlayer
from ..sync.score_view import SystemRef, create_score_view
from .job import Job
from .score_host import ScoreViewHost

log = get_logger(__name__)

__all__ = [
    "preview_kind",
    "ScorePreviewWindow",
    "AudioPreviewWindow",
    "PREVIEW_SCORE_SUFFIXES",
    "PREVIEW_AUDIO_SUFFIXES",
]

#: 可"显示"的源文件（乐谱）
PREVIEW_SCORE_SUFFIXES = frozenset({".musicxml", ".xml", ".mxl", ".svg"})
#: 可"播放"的源文件（音频）
PREVIEW_AUDIO_SUFFIXES = frozenset({".mid", ".midi", ".mp3"})

#: 文本回退时最多显示多少字符（MusicXML 动辄几 MB）
_TEXT_LIMIT = 200_000

#: 预览缓存的保留上限（按最近使用）。
#: 合成的试听 WAV 一个就有 20+ MB（130 s 立体声），不能无限攒 —— 超过就删最旧的。
_CACHE_KEEP = {"score": 20, "audio": 3}

_ERROR_COLOR = "#e05252"


def _prune_cache(subdir: str, keep: int = 0) -> None:
    """只保留最近使用的 ``keep`` 项（``score`` 下是目录、``audio`` 下是文件）。"""
    limit = keep or _CACHE_KEEP.get(subdir, 10)
    root = PREVIEW_DIR / subdir
    if not root.is_dir():
        return
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in entries[limit:]:
        try:
            if old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
            else:
                old.unlink(missing_ok=True)
        except OSError as e:  # noqa: BLE001 - 清理失败不该影响预览
            log.debug("清理预览缓存失败：%s（%s）", old, e)


def preview_kind(path: str | Path) -> str:
    """返回该文件用哪种预览：``"score"`` / ``"audio"`` / ``""``（不支持）。

    只认需求点名的四种：MusicXML（.musicxml/.xml/.mxl）、SVG、MIDI（.mid/.midi）、MP3。
    ``wav`` / ``pdf`` 等一律返回空串 —— 按钮据此置灰（并给出原因 tooltip）。
    """
    suffix = Path(path).suffix.lower()
    if suffix in PREVIEW_SCORE_SUFFIXES:
        return "score"
    if suffix in PREVIEW_AUDIO_SUFFIXES:
        return "audio"
    return ""


def _cache_key(path: Path, extra: str = "") -> str:
    """按 ``路径 + mtime + size (+extra)`` 生成缓存键（内容变了就重新生成）。"""
    try:
        st = path.stat()
        raw = f"{path.resolve()}|{st.st_mtime_ns}|{st.st_size}|{extra}"
    except OSError:
        raw = f"{path.resolve()}|{extra}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


class _PreviewDialog(QDialog):
    """预览窗口基类：非模态、关闭时只隐藏（实例由调用方复用）。"""

    def __init__(self, parent: QWidget | None, *, autoplay: bool = True) -> None:
        super().__init__(parent)
        # 非模态：不阻塞主窗口，用户可以一边看预览一边做转换
        self.setModal(False)
        # 不随关闭销毁：窗口实例由 ConvertTab 持有并复用（重建成本高，
        # 且 QSvgRenderer 被 Python GC 掉会让视图留下悬垂指针 —— 详见 score_view）
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.autoplay = bool(autoplay)
        self._source: Path | None = None

    @property
    def source(self) -> Path | None:
        """当前预览的源文件。"""
        return self._source

    def open_file(self, path: str | Path) -> None:
        raise NotImplementedError

    def _raise_existing(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()


# ===========================================================================
# 乐谱预览
# ===========================================================================
class ScorePreviewWindow(_PreviewDialog):
    """非模态窗口：显示 SVG / MusicXML 的乐谱（渲染失败退回源码文本）。"""

    def __init__(
        self,
        config: AppConfig,
        parent: QWidget | None = None,
        log_dock=None,  # noqa: ANN001 - 与其它页一致的日志接口（可选）
        *,
        max_systems: int = PREVIEW_MAX_SYSTEMS,
        prefer: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.log_dock = log_dock
        self.max_systems = int(max_systems)
        self._job: Job | None = None

        self.score_view = create_score_view(
            prefer=prefer or str(getattr(config.ui, "score_backend", "") or "auto"),
            log_sink=log,
        )
        self.view_host = ScoreViewHost(self.score_view)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setStyleSheet("font-family:Consolas,'Courier New',monospace;font-size:9pt;")

        self.stack = QStackedWidget()
        self.stack.addWidget(self.view_host)  # 0 = 乐谱
        self.stack.addWidget(self.text)  # 1 = 源码文本（回退）

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color:#555;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addWidget(self.lbl_status)
        lay.addWidget(self.stack, 1)
        self.resize(1000, 640)

    # ------------------------------------------------------------------ 打开
    def open_file(self, path: str | Path) -> None:
        p = Path(path)
        if self._source == p and self.isVisible():
            self._raise_existing()
            return
        self._source = p
        self.setWindowTitle(f"乐谱预览 — {p.name}")
        self._raise_existing()

        if not p.is_file():
            self._show_text(None, f"文件不存在或不可读：{p}")
            return

        if p.suffix.lower() == ".svg":
            self._show_svg(p)
        else:
            self._render_musicxml(p)

    # ------------------------------------------------------------------ SVG
    def _show_svg(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self._show_text(None, f"无法读取文件：{e}")
            return

        width, height = svg_size(text)
        refs = [SystemRef(index=1, file=path.name, width=width, height=height, note_ids=[])]
        self.score_view.load(path.parent, refs)
        self.stack.setCurrentIndex(0)

        # QtSvg 只支持 SVG Tiny 1.2 的子集：解析不了就退回文本，别让用户看白板
        renderer_for = getattr(self.score_view, "renderer_for", None)
        if callable(renderer_for) and renderer_for(1) is None:
            self._show_text(path, "该 SVG 无法用 QtSvg 渲染（可能用了它不支持的 SVG 特性）")
            return
        self.lbl_status.setText(
            f"SVG 预览：{path.name}（{width or '?'}×{height or '?'} px，已按窗口宽度自适应）"
        )
        self._log(f"预览 SVG：{path}")

    # ------------------------------------------------------------------ MusicXML
    def _render_musicxml(self, path: Path) -> None:
        cache = PREVIEW_DIR / "score" / _cache_key(path, f"n{self.max_systems}")
        cached = sorted(cache.glob("sys-*.svg"))
        if cached:
            self._load_rendered(path, cached, cached=True)
            return

        self.stack.setCurrentIndex(0)
        self.lbl_status.setText(
            f"正在渲染预览…（Verovio，只渲染前 {self.max_systems} 行，"
            "完整套件请用「生成套件」页）"
        )
        self._log(f"渲染 MusicXML 预览：{path.name}")
        self._job = Job(lambda: self._render_worker(path, cache), parent=self)
        self._job.finished.connect(self._on_rendered)
        self._job.start()

    def _render_worker(self, path: Path, cache: Path) -> list[Path]:
        """工作线程：渲染前 N 行并落盘（Verovio 每个线程用各自的 toolkit）。"""
        doc = read_musicxml(path)
        svgs = render_preview_svgs(
            doc.xml_bytes,
            self.config.render,
            max_systems=self.max_systems,
            # 后台线程渲染必须显式给资源目录（见 core.score_render.verovio_resource_path）
            resources=self.config.paths.verovio_resources,
        )
        cache.mkdir(parents=True, exist_ok=True)
        out: list[Path] = []
        for i, text in enumerate(svgs, 1):
            f = cache / f"sys-{i:04d}.svg"
            f.write_text(text, encoding="utf-8")
            out.append(f)
        return out

    def _on_rendered(self, result: object, error: str) -> None:
        self._job = None
        path = self._source
        if error or not result:
            self._show_text(path, f"渲染失败：{error or '未知错误'}")
            self._log(f"预览渲染失败：{path} —— {error}", level="error")
            return
        _prune_cache("score")  # 缓存只保留最近若干个套件
        self._load_rendered(path, [Path(x) for x in result], cached=False)  # type: ignore[arg-type]

    def _load_rendered(self, path: Path | None, svgs: list[Path], *, cached: bool) -> None:
        refs = []
        for i, f in enumerate(svgs, 1):
            try:
                width, height = svg_size(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                width = height = 0
            refs.append(
                SystemRef(index=i, file=f.name, width=width, height=height, note_ids=[])
            )
        root = svgs[0].parent if svgs else PREVIEW_DIR
        self.score_view.load(root, refs)
        self.stack.setCurrentIndex(0)
        total = len(svgs)
        more = "（已截断，仅预览前若干行）" if total >= self.max_systems else ""
        origin = "缓存" if cached else "新渲染"
        self.lbl_status.setText(
            f"MusicXML 预览：{path.name if path else ''} —— {total} 行（{origin}）{more}"
        )
        if not cached:
            self._log(f"MusicXML 预览完成：{path.name if path else ''}，{total} 行")

    # ------------------------------------------------------------------ 文本回退
    def _show_text(self, path: Path | None, message: str) -> None:
        body = ""
        if path is not None and path.is_file():
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                body = f"（无法读取文件内容：{e}）"
            if len(body) > _TEXT_LIMIT:
                body = body[:_TEXT_LIMIT] + f"\n…（已截断，原文共 {len(body)} 字符）"
        self.text.setPlainText(f"{message}\n\n{body}" if body else message)
        self.stack.setCurrentIndex(1)
        self.lbl_status.setText(f"<span style='color:{_ERROR_COLOR}'>{message}</span>")

    # ------------------------------------------------------------------ 收尾
    def shutdown(self) -> None:
        """退出程序时调用：等工作线程结束（最多几秒），避免 QThread 被销毁时仍在运行。"""
        if self._job is not None and self._job.is_running:
            self._job.wait(8000)

    def _log(self, text: str, *, level: str = "info") -> None:
        if level == "info":
            log.info("%s", text)
        else:
            log.error("%s", text)
        add_note = getattr(self.log_dock, "add_note", None)
        if callable(add_note):
            add_note(level, text)


# ===========================================================================
# 音频预览
# ===========================================================================
class AudioPreviewWindow(_PreviewDialog):
    """非模态窗口：试听 MIDI / MP3（播放 / 停止按钮 + 进度条）。

    MP3 直接交给 ``QMediaPlayer``；MIDI 先合成成 WAV（见模块文档）。
    """

    def __init__(
        self,
        config: AppConfig,
        parent: QWidget | None = None,
        log_dock=None,  # noqa: ANN001
        *,
        autoplay: bool = True,
    ) -> None:
        super().__init__(parent, autoplay=autoplay)
        self.config = config
        self.log_dock = log_dock
        self._job: Job | None = None
        self._seeking = False
        self._ready = False

        self.player = QtMediaPlayer(self)
        self.player.error.connect(self._on_player_error)
        self.player.state_changed.connect(self._on_state_changed)
        self.player.position_changed.connect(self._on_position)
        self.player.duration_changed.connect(self._on_duration)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color:#555;")

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # 不确定进度（合成中）
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)

        row = QHBoxLayout()
        self.lbl_time = QLabel("00:00.0")
        self.lbl_time.setMinimumWidth(66)
        self.lbl_time.setStyleSheet("font-family:Consolas,monospace;")
        row.addWidget(self.lbl_time)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        row.addWidget(self.slider, 1)
        self.lbl_total = QLabel("00:00.0")
        self.lbl_total.setMinimumWidth(66)
        self.lbl_total.setStyleSheet("font-family:Consolas,monospace;color:#888;")
        row.addWidget(self.lbl_total)

        btns = QHBoxLayout()
        self.btn_play = QPushButton("▶ 播放")
        self.btn_play.setMinimumWidth(104)
        self.btn_play.clicked.connect(self._toggle)
        btns.addWidget(self.btn_play)
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.clicked.connect(self.stop)
        btns.addWidget(self.btn_stop)
        btns.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        lay.addWidget(self.lbl_status)
        lay.addWidget(self.bar)
        lay.addLayout(row)
        lay.addLayout(btns)
        self._set_transport_enabled(False)
        self.resize(560, 170)

    # ------------------------------------------------------------------ 打开
    def open_file(self, path: str | Path) -> None:
        p = Path(path)
        if self._source == p and self.isVisible():
            self._raise_existing()
            return
        self.stop()
        self._source = p
        self._ready = False
        self._set_transport_enabled(False)
        self.setWindowTitle(f"音频预览 — {p.name}")
        self._raise_existing()

        if not p.is_file():
            self.lbl_status.setText(
                f"<span style='color:{_ERROR_COLOR}'>文件不存在或不可读：{p}</span>"
            )
            return
        if p.suffix.lower() in (".mid", ".midi"):
            self._prepare_midi(p)
        else:
            self._use_audio(p, note="直接播放")

    def _prepare_midi(self, path: Path) -> None:
        """MIDI：先合成成 WAV（有缓存就直接用）。"""
        wav = PREVIEW_DIR / "audio" / f"{path.stem}-{_cache_key(path)}.wav"
        if wav.is_file() and wav.stat().st_size > 0:
            self._use_audio(wav, note="试听音频（缓存）")
            return

        # 用 resolved() 自动探测 ffmpeg / FluidSynth（配置里为空时也找得到），
        # 与生成套件那条链路保持一致
        paths = self.config.paths.resolved()
        tools = resolve_tools(
            Path(paths.ffmpeg) if paths.ffmpeg else None,
            Path(paths.fluidsynth_dir) if paths.fluidsynth_dir else None,
        )
        if not tools.can_render_wav:
            self.lbl_status.setText(
                f"<span style='color:{_ERROR_COLOR}'>"
                "无法试听 MIDI：未找到 fluidsynth.exe。"
                "请把 FluidSynth 放到 tools/fluidsynth（或先在「生成套件」页把 MIDI 转成 MP3）。"
                "</span>"
            )
            self._log(f"MIDI 预览不可用（缺 FluidSynth）：{path.name}", level="error")
            return

        soundfont = self.config.audio.soundfont_path(PROJECT_ROOT)
        self._set_busy(True, "正在用 FluidSynth 合成试听音频…（首次较慢，之后会缓存）")
        self._log(f"合成 MIDI 试听音频：{path.name}")
        self._job = Job(
            lambda: self._synth_worker(path, wav, tools, soundfont), parent=self
        )
        self._job.finished.connect(self._on_synth_done)
        self._job.start()

    def _synth_worker(self, path: Path, wav: Path, tools, soundfont: Path) -> Path:  # noqa: ANN001
        midi_to_wav(
            path,
            wav,
            soundfont,
            tools,
            sample_rate=int(self.config.audio.sample_rate),
            gain=float(self.config.audio.gain),
            reverb=bool(self.config.audio.reverb),
            chorus=bool(self.config.audio.chorus),
            timeout_s=600,
        )
        return wav

    def _on_synth_done(self, result: object, error: str) -> None:
        self._job = None
        self._set_busy(False)
        if error or not result:
            self.lbl_status.setText(
                f"<span style='color:{_ERROR_COLOR}'>MIDI 合成失败：{error or '未知错误'}</span>"
            )
            self._log(f"MIDI 合成失败：{self._source} —— {error}", level="error")
            return
        _prune_cache("audio")  # 合成的 WAV 很大：只保留最近几个
        self._use_audio(Path(result), note="试听音频（已合成）")  # type: ignore[arg-type]

    def _use_audio(self, media: Path, *, note: str) -> None:
        """载入真正要播放的媒体（MP3 本体，或 MIDI 合成出的 WAV）。"""
        self.player.load(media)
        self._ready = True
        self._set_transport_enabled(True)
        name = self._source.name if self._source else media.name
        self.lbl_status.setText(f"{note}：{name}（{media.name}）")
        if self.autoplay:
            self.play()

    # ------------------------------------------------------------------ 传输
    def play(self) -> None:
        if not self._ready:
            return
        self.player.play()

    def stop(self) -> None:
        self.player.stop()
        if self.slider.value() != 0:
            self.slider.blockSignals(True)
            self.slider.setValue(0)
            self.slider.blockSignals(False)
        self.lbl_time.setText(_fmt_ms(0))
        self.btn_play.setText("▶ 播放")

    def _toggle(self) -> None:
        if not self._ready:
            return
        if self.player.state() == PlayerState.PLAYING:
            self.player.pause()
        else:
            self.player.play()

    def _set_transport_enabled(self, ok: bool) -> None:
        self.btn_play.setEnabled(ok)
        self.btn_stop.setEnabled(ok)
        self.slider.setEnabled(ok)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.bar.setVisible(busy)
        if busy:
            self.lbl_status.setText(message)

    # ------------------------------------------------------------------ 回调
    def _on_state_changed(self, state: str) -> None:
        playing = state == PlayerState.PLAYING.value
        self.btn_play.setText("⏸ 暂停" if playing else "▶ 播放")

    def _on_position(self, position: int) -> None:
        if self._seeking:
            return
        duration = self.player.duration()
        if duration > 0:
            self.slider.blockSignals(True)
            self.slider.setValue(int(position / duration * 1000))
            self.slider.blockSignals(False)
        self.lbl_time.setText(_fmt_ms(position))

    def _on_duration(self, duration: int) -> None:
        self.lbl_total.setText(_fmt_ms(duration))

    def _on_player_error(self, text: str) -> None:
        self.lbl_status.setText(f"<span style='color:{_ERROR_COLOR}'>{text}</span>")
        self._log(f"预览播放失败：{text}", level="error")

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_slider_moved(self, value: int) -> None:
        self._seeking = True
        duration = self.player.duration()
        if duration:
            self.lbl_time.setText(_fmt_ms(int(value / 1000 * duration)))

    def _on_slider_released(self) -> None:
        self._seeking = False
        duration = self.player.duration()
        if duration:
            self.player.seek(int(self.slider.value() / 1000 * duration))

    # ------------------------------------------------------------------ 收尾
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """关窗只停止播放（窗口实例保留复用，避免重复创建 QMediaPlayer / 线程）。"""
        self.stop()
        super().closeEvent(event)

    def shutdown(self) -> None:
        self.stop()
        if self._job is not None and self._job.is_running:
            self._job.wait(8000)

    def _log(self, text: str, *, level: str = "info") -> None:
        if level == "info":
            log.info("%s", text)
        else:
            log.error("%s", text)
        add_note = getattr(self.log_dock, "add_note", None)
        if callable(add_note):
            add_note(level, text)


def _fmt_ms(ms: int) -> str:
    """毫秒 → ``mm:ss.d``（与播放页的时间显示一致）。"""
    ms = max(0, int(ms))
    total_s, tenth = divmod(ms // 100, 10)
    minutes, seconds = divmod(total_s, 60)
    return f"{minutes:02d}:{seconds:02d}.{tenth}"
