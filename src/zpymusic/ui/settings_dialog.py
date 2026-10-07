"""设置对话框（需求 F4.6 / §3.3）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..common.config import AppConfig
from ..common.deps import default_ffmpeg, default_fluidsynth_dir, default_musescore
from ..common.log import get_logger
from ..common.paths import PROJECT_ROOT, SOUND_DIR, TOOLS_DIR

log = get_logger(__name__)


class SettingsDialog(QDialog):
    """路径 / 音频 / 渲染 / 界面 四个分组。"""

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(680)
        self.config = config

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_paths(), "路径与依赖")
        tabs.addTab(self._build_audio(), "音频渲染")
        tabs.addTab(self._build_render(), "SVG 渲染")
        tabs.addTab(self._build_ui(), "界面")
        root.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------ 路径
    def _build_paths(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.ed_musescore = _path_row(
            form, "MuseScore 4", self.config.paths.musescore, default_musescore, "*.exe"
        )
        self.ed_ffmpeg = _path_row(
            form, "ffmpeg", self.config.paths.ffmpeg, default_ffmpeg, "*.exe"
        )
        self.ed_fluidsynth = _dir_row(
            form, "FluidSynth 目录", self.config.paths.fluidsynth_dir, default_fluidsynth_dir
        )

        hint = QLabel(
            "FluidSynth 目录需包含 fluidsynth.exe / libfluidsynth-3.dll / SDL3.dll / sndfile.dll。\n"
            f"推荐放在：{TOOLS_DIR / 'fluidsynth'}"
        )
        hint.setStyleSheet("color:#888;")
        form.addRow("", hint)

        self.ed_musicxml_dir = QLineEdit(self.config.musicxml_dir)
        form.addRow("默认源目录", _browse_row(self.ed_musicxml_dir, self, directory=True))

        self.ed_suites_dir = QLineEdit(self.config.suites_dir)
        form.addRow("默认套件目录", _browse_row(self.ed_suites_dir, self, directory=True))
        return w

    # ------------------------------------------------------------------ 音频
    def _build_audio(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.cmb_soundfont = QComboBox()
        for f in sorted(SOUND_DIR.glob("*.sf2")) + sorted(SOUND_DIR.glob("*.sf3")):
            rel = f.relative_to(PROJECT_ROOT).as_posix()
            self.cmb_soundfont.addItem(f"{f.name} ({f.stat().st_size/1e6:.0f} MB)", rel)
        for i in range(self.cmb_soundfont.count()):
            if self.cmb_soundfont.itemData(i) == self.config.audio.default_soundfont:
                self.cmb_soundfont.setCurrentIndex(i)
                break
        form.addRow("默认音色库", self.cmb_soundfont)

        self.spn_rate = QSpinBox()
        self.spn_rate.setRange(8000, 192000)
        self.spn_rate.setSingleStep(1000)
        self.spn_rate.setValue(self.config.audio.sample_rate)
        self.spn_rate.setSuffix(" Hz")
        form.addRow("采样率", self.spn_rate)

        self.spn_bitrate = QSpinBox()
        self.spn_bitrate.setRange(64, 320)
        self.spn_bitrate.setSingleStep(32)
        self.spn_bitrate.setValue(self.config.audio.mp3_bitrate_kbps)
        self.spn_bitrate.setSuffix(" kbps")
        form.addRow("MP3 码率", self.spn_bitrate)

        self.spn_gain = QDoubleSpinBox()
        self.spn_gain.setRange(0.05, 2.0)
        self.spn_gain.setSingleStep(0.05)
        self.spn_gain.setValue(self.config.audio.gain)
        form.addRow("主增益", self.spn_gain)

        self.chk_reverb = QCheckBox("启用混响")
        self.chk_reverb.setChecked(self.config.audio.reverb)
        form.addRow("", self.chk_reverb)

        self.chk_chorus = QCheckBox("启用合唱")
        self.chk_chorus.setChecked(self.config.audio.chorus)
        form.addRow("", self.chk_chorus)

        self.chk_keep_wav = QCheckBox("保留中间 WAV 文件（默认生成后删除）")
        self.chk_keep_wav.setChecked(self.config.audio.keep_wav)
        form.addRow("", self.chk_keep_wav)
        return w

    # ------------------------------------------------------------------ 渲染
    def _build_render(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.spn_scale = QSpinBox()
        self.spn_scale.setRange(10, 200)
        self.spn_scale.setValue(self.config.render.scale)
        self.spn_scale.setSuffix(" %")
        form.addRow("缩放", self.spn_scale)

        self.spn_pagewidth = QSpinBox()
        self.spn_pagewidth.setRange(400, 20000)
        self.spn_pagewidth.setSingleStep(100)
        self.spn_pagewidth.setValue(self.config.render.page_width)
        form.addRow("页面宽度", self.spn_pagewidth)

        self.cmb_breaks = QComboBox()
        self.cmb_breaks.addItem("每行一个 SVG（推荐，便于懒加载）", "smart")
        self.cmb_breaks.addItem("按纸张自动分页", "auto")
        self.cmb_breaks.addItem("完全不自动换行", "none")
        for i in range(self.cmb_breaks.count()):
            if self.cmb_breaks.itemData(i) == self.config.render.breaks:
                self.cmb_breaks.setCurrentIndex(i)
                break
        form.addRow("换行方式", self.cmb_breaks)

        self.spn_pageheight = QSpinBox()
        self.spn_pageheight.setRange(100, 60000)
        self.spn_pageheight.setSingleStep(50)
        self.spn_pageheight.setValue(self.config.render.page_height)
        form.addRow("每页高度提示值", self.spn_pageheight)

        self.chk_single_page = QCheckBox(
            "单页连续纵向（小曲目 1 页；大曲目会产生十几万像素高的超大 SVG，慎用）"
        )
        self.chk_single_page.setChecked(self.config.render.single_page)
        form.addRow("", self.chk_single_page)

        self.chk_rests = QCheckBox("把休止符也写进 sync.json")
        self.chk_rests.setChecked(self.config.render.include_rests_in_sync)
        form.addRow("", self.chk_rests)
        return w

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.cmb_tabpos = QComboBox()
        for label, val in (("左侧", "west"), ("上方", "north"), ("右侧", "east"), ("下方", "south")):
            self.cmb_tabpos.addItem(label, val)
        for i in range(self.cmb_tabpos.count()):
            if self.cmb_tabpos.itemData(i) == self.config.ui.tab_position:
                self.cmb_tabpos.setCurrentIndex(i)
                break
        form.addRow("标签位置", self.cmb_tabpos)

        self.ed_color = QLineEdit(self.config.ui.highlight_color)
        btn = QPushButton("选择颜色…")
        btn.clicked.connect(self._pick_color)
        row = QHBoxLayout()
        row.addWidget(self.ed_color, 1)
        row.addWidget(btn)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("高亮颜色", holder)

        self.spn_opacity = QDoubleSpinBox()
        self.spn_opacity.setRange(0.05, 1.0)
        self.spn_opacity.setSingleStep(0.05)
        self.spn_opacity.setValue(self.config.ui.highlight_opacity)
        form.addRow("高亮透明度", self.spn_opacity)

        self.chk_follow = QCheckBox("播放时自动滚动跟随当前小节")
        self.chk_follow.setChecked(self.config.ui.follow_playback)
        form.addRow("", self.chk_follow)

        self.cmb_backend = QComboBox()
        self.cmb_backend.addItem("自动（推荐：优先浏览器引擎，失败回退原生）", "auto")
        self.cmb_backend.addItem("原生 QtSvg（绕开浏览器引擎，最保守）", "native")
        self.cmb_backend.addItem("强制浏览器引擎 QWebEngine（失败则报错）", "web")
        for i in range(self.cmb_backend.count()):
            if self.cmb_backend.itemData(i) == getattr(self.config.ui, "score_backend", "auto"):
                self.cmb_backend.setCurrentIndex(i)
                break
        else:
            self.cmb_backend.setCurrentIndex(0)
        form.addRow("曲谱渲染后端", self.cmb_backend)
        form.addRow(
            "",
            _muted(
                "⚠ 需重启程序生效（后端在「同步播放」页初始化时确定）。\n"
                "「自动」会先用默认（GPU 加速）配置在子进程里探测浏览器引擎，"
                "只有确实建不出 GPU 上下文时才降级为软件渲染；"
                "若浏览器引擎因运行环境限制（受限沙箱禁止创建命名管道）不可用，"
                "会自动回退到原生 QtSvg，功能等价，只是高亮改用覆盖框实现。"
            ),
        )
        return w

    def _pick_color(self) -> None:
        from PySide6.QtGui import QColor  # noqa: PLC0415
        from PySide6.QtWidgets import QColorDialog  # noqa: PLC0415

        c = QColorDialog.getColor(QColor(self.ed_color.text()), self, "选择高亮颜色")
        if c.isValid():
            self.ed_color.setText(c.name())

    # ------------------------------------------------------------------ 应用
    def apply_to_config(self) -> None:
        """把对话框内容写回 ``self.config``。"""
        c = self.config
        c.paths.musescore = self.ed_musescore.text().strip() or None
        c.paths.ffmpeg = self.ed_ffmpeg.text().strip() or None
        c.paths.fluidsynth_dir = self.ed_fluidsynth.text().strip() or None
        c.musicxml_dir = self.ed_musicxml_dir.text().strip()
        c.suites_dir = self.ed_suites_dir.text().strip()

        c.audio.default_soundfont = str(self.cmb_soundfont.currentData() or "")
        c.audio.sample_rate = self.spn_rate.value()
        c.audio.mp3_bitrate_kbps = self.spn_bitrate.value()
        c.audio.gain = self.spn_gain.value()
        c.audio.reverb = self.chk_reverb.isChecked()
        c.audio.chorus = self.chk_chorus.isChecked()
        c.audio.keep_wav = self.chk_keep_wav.isChecked()

        c.render.scale = self.spn_scale.value()
        c.render.page_width = self.spn_pagewidth.value()
        c.render.breaks = str(self.cmb_breaks.currentData())
        c.render.page_height = self.spn_pageheight.value()
        c.render.single_page = self.chk_single_page.isChecked()
        c.render.include_rests_in_sync = self.chk_rests.isChecked()

        c.ui.tab_position = str(self.cmb_tabpos.currentData())
        c.ui.highlight_color = self.ed_color.text().strip() or "#FF8C00"
        c.ui.highlight_opacity = self.spn_opacity.value()
        c.ui.follow_playback = self.chk_follow.isChecked()
        c.ui.score_backend = str(self.cmb_backend.currentData() or "auto")


def _muted(text: str) -> QLabel:
    """灰色小字说明。"""
    lbl = QLabel(text)
    lbl.setStyleSheet("color:#888;")
    lbl.setWordWrap(True)
    return lbl


def _path_row(form, label, value, default_fn, pattern) -> QLineEdit:
    ed = QLineEdit(value or "")
    ed.setPlaceholderText("留空 = 自动探测")

    def browse() -> None:
        start = ed.text().strip() or str(Path(default_fn() or PROJECT_ROOT))
        path, _ = QFileDialog.getOpenFileName(None, f"选择 {label}", start, f"可执行文件 ({pattern})")
        if path:
            ed.setText(path)

    def autodetect() -> None:
        found = default_fn()
        ed.setText(str(found) if found else "")

    btn = QPushButton("浏览…")
    btn.clicked.connect(browse)
    btn2 = QPushButton("自动探测")
    btn2.clicked.connect(autodetect)
    row = QHBoxLayout()
    row.addWidget(ed, 1)
    row.addWidget(btn)
    row.addWidget(btn2)
    holder = QWidget()
    holder.setLayout(row)
    form.addRow(label, holder)
    return ed


def _dir_row(form, label, value, default_fn) -> QLineEdit:
    ed = QLineEdit(value or "")
    ed.setPlaceholderText("留空 = 自动探测")

    def browse() -> None:
        start = ed.text().strip() or str(default_fn() or TOOLS_DIR)
        path = QFileDialog.getExistingDirectory(None, f"选择 {label}", start)
        if path:
            ed.setText(path)

    def autodetect() -> None:
        found = default_fn()
        ed.setText(str(found) if found else "")

    btn = QPushButton("浏览…")
    btn.clicked.connect(browse)
    btn2 = QPushButton("自动探测")
    btn2.clicked.connect(autodetect)
    row = QHBoxLayout()
    row.addWidget(ed, 1)
    row.addWidget(btn)
    row.addWidget(btn2)
    holder = QWidget()
    holder.setLayout(row)
    form.addRow(label, holder)
    return ed


def _browse_row(ed: QLineEdit, parent: QWidget, *, directory: bool) -> QWidget:
    def browse() -> None:
        if directory:
            path = QFileDialog.getExistingDirectory(parent, "选择目录", ed.text())
        else:
            path, _ = QFileDialog.getOpenFileName(parent, "选择文件", ed.text())
        if path:
            ed.setText(path)

    btn = QPushButton("…")
    btn.setMaximumWidth(32)
    btn.clicked.connect(browse)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(ed, 1)
    row.addWidget(btn)
    holder = QWidget()
    holder.setLayout(row)
    return holder
