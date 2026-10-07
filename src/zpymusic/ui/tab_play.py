"""界面二：同步播放展示曲谱（需求 §5.3 F2.1–F2.9）。

布局::

    ┌──────────────┬──────────────────────────────────────────┐
    │ 套件列表      │  曲谱视图（ScoreView：WebEngine 或原生）    │
    │ + 套件信息    ├──────────────────────────────────────────┤
    │              │  传输控制：播放/暂停 停止 进度条 时间        │
    │              │  调节：速率 高亮颜色/透明度 跟随 校准        │
    └──────────────┴──────────────────────────────────────────┘

播放编排全部委托给 :class:`~zpymusic.sync.controller.PlaybackController`，
本文件只负责界面与用户输入。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..common.config import AppConfig
from ..common.errors import ZpyMusicError
from ..common.log import get_logger
from ..core.suite import Suite, discover_suites, make_layout
from ..sync.controller import RATE_STEPS, PlaybackController, PlaybackStatus
from ..sync.player import MIN_RATE, QtMediaPlayer
from ..sync.score_view import ScoreView, create_score_view
from .score_host import ScoreViewHost  # noqa: F401 - 兼容旧导入路径（预览窗口共用）

log = get_logger(__name__)


class PlayTab(QWidget):
    """「同步播放」标签页（M4）。"""

    #: 选中/载入了一个套件（携带 :class:`~zpymusic.core.suite.Suite`）。
    #: 主窗口用它刷新状态栏的"当前套件 / 音色库"。
    suite_selected = Signal(object)

    def __init__(self, config: AppConfig, log_dock, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.log_dock = log_dock
        self._suite: Suite | None = None
        self._seeking = False
        self._pending_click_id = ""

        # --- 后端 ---------------------------------------------------------
        # 后端偏好来自配置（默认 auto）。若浏览器引擎在本机报 GPU 上下文错误，
        # 用户可在「设置 → 界面」里改成 native 完全绕开 Chromium。
        self.score_view: ScoreView = create_score_view(
            prefer=str(getattr(self.config.ui, "score_backend", "auto") or "auto"),
            log_sink=log,
        )
        log.info("曲谱视图后端：%s", type(self.score_view).__name__)
        self.player = QtMediaPlayer(self)
        self.controller = PlaybackController(self.player, self.score_view, self)
        self.controller.status.connect(self._on_status)
        self.controller.error.connect(self._on_error)
        self.controller.finished.connect(self._on_finished)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_sidebar())

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        self.view_host = ScoreViewHost(self.score_view)
        rv.addWidget(self.view_host, 1)
        rv.addWidget(self._build_controls())
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([330, 950])
        root.addWidget(splitter)

        self._install_shortcuts()
        self._apply_style_from_config()
        self._setup_click_jump()
        self.reload()

    # ================================================================== 左侧栏
    def _build_sidebar(self) -> QWidget:
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)
        lv.setSpacing(6)

        gb = QGroupBox("套件")
        form = QFormLayout(gb)
        row = QHBoxLayout()
        self.ed_root = QLineEdit(str(self.config.suites_path()))
        row.addWidget(self.ed_root, 1)
        btn = QPushButton("…")
        btn.setMaximumWidth(30)
        btn.clicked.connect(self._pick_root)
        row.addWidget(btn)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("根目录", holder)

        row = QHBoxLayout()
        btn_scan = QPushButton("扫描")
        btn_scan.clicked.connect(self.reload)
        row.addWidget(btn_scan)
        btn_open = QPushButton("选择音频…")
        btn_open.clicked.connect(self._pick_audio)
        row.addWidget(btn_open)
        form.addRow("", row)
        lv.addWidget(gb)

        self.lst = QListWidget()
        self.lst.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lst.currentItemChanged.connect(self._on_selected)
        lv.addWidget(self.lst, 1)

        self.info = QTextBrowser()
        self.info.setMinimumHeight(220)
        lv.addWidget(self.info, 2)
        return left

    # ================================================================== 控制条
    def _build_controls(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        # --- 进度条 ---
        row = QHBoxLayout()
        self.lbl_time = QLabel("00:00.0")
        self.lbl_time.setMinimumWidth(70)
        self.lbl_time.setStyleSheet("font-family:Consolas,monospace;")
        row.addWidget(self.lbl_time)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setTracking(True)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        row.addWidget(self.slider, 1)

        self.lbl_total = QLabel("00:00.0")
        self.lbl_total.setMinimumWidth(70)
        self.lbl_total.setStyleSheet("font-family:Consolas,monospace;color:#888;")
        row.addWidget(self.lbl_total)
        v.addLayout(row)

        # --- 传输按钮 ---
        row = QHBoxLayout()
        self.btn_play = QPushButton("▶ 播放")
        self.btn_play.setMinimumWidth(104)
        self.btn_play.clicked.connect(self._on_play_clicked)
        row.addWidget(self.btn_play)

        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.clicked.connect(self.controller.stop)
        row.addWidget(self.btn_stop)

        self.btn_back = QPushButton("⏮ 上一音")
        self.btn_back.setToolTip("回退到上一个音符（←）")
        self.btn_back.clicked.connect(lambda: self._seek_note(-1))
        row.addWidget(self.btn_back)

        self.btn_fwd = QPushButton("⏭ 下一音")
        self.btn_fwd.setToolTip("前进到下一个音符（→）")
        self.btn_fwd.clicked.connect(lambda: self._seek_note(1))
        row.addWidget(self.btn_fwd)

        row.addSpacing(12)
        self.lbl_measure = QLabel("小节 —")
        self.lbl_measure.setMinimumWidth(120)
        row.addWidget(self.lbl_measure)

        self.lbl_active = QLabel("")
        self.lbl_active.setStyleSheet("color:#4ec94e;font-family:Consolas,monospace;")
        row.addWidget(self.lbl_active, 1)
        v.addLayout(row)

        # --- 调节 ---
        row = QHBoxLayout()
        row.addWidget(QLabel("速率"))
        self.cmb_rate = QComboBox()
        for r in RATE_STEPS:
            self.cmb_rate.addItem(f"{r*100:.0f}%", r)
        self.cmb_rate.setCurrentIndex(list(RATE_STEPS).index(1.0))
        self.cmb_rate.currentIndexChanged.connect(self._on_rate_changed)
        row.addWidget(self.cmb_rate)

        self.btn_rate_down = QPushButton("−")
        self.btn_rate_down.setMaximumWidth(28)
        self.btn_rate_down.clicked.connect(lambda: self.controller.step_rate(-1))
        row.addWidget(self.btn_rate_down)
        self.btn_rate_up = QPushButton("+")
        self.btn_rate_up.setMaximumWidth(28)
        self.btn_rate_up.clicked.connect(lambda: self.controller.step_rate(1))
        row.addWidget(self.btn_rate_up)
        self.btn_rate_reset = QPushButton("100%")
        self.btn_rate_reset.clicked.connect(self.controller.reset_rate)
        row.addWidget(self.btn_rate_reset)

        self.lbl_bpm = QLabel("BPM —")
        self.lbl_bpm.setMinimumWidth(110)
        row.addWidget(self.lbl_bpm)
        row.addStretch(1)
        v.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("高亮"))
        self.ed_color = QLineEdit(self.config.ui.highlight_color)
        self.ed_color.setMaximumWidth(90)
        self.ed_color.editingFinished.connect(self._apply_style_from_config)
        row.addWidget(self.ed_color)
        btn_color = QPushButton("取色…")
        btn_color.clicked.connect(self._pick_color)
        row.addWidget(btn_color)

        self.sld_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sld_opacity.setRange(5, 100)
        self.sld_opacity.setValue(int(self.config.ui.highlight_opacity * 100))
        self.sld_opacity.setMaximumWidth(140)
        self.sld_opacity.valueChanged.connect(self._apply_style_from_config)
        row.addWidget(self.sld_opacity)
        self.lbl_opacity = QLabel(f"{int(self.config.ui.highlight_opacity*100)}%")
        self.lbl_opacity.setMinimumWidth(36)
        row.addWidget(self.lbl_opacity)

        self.chk_follow = QPushButton("跟随滚动：开")
        self.chk_follow.setCheckable(True)
        self.chk_follow.setChecked(self.config.ui.follow_playback)
        self.chk_follow.toggled.connect(self._on_follow_toggled)
        row.addWidget(self.chk_follow)

        self.btn_calibrate = QPushButton("按当前音符校准")
        self.btn_calibrate.setToolTip("把当前正在发声的音符当作基准，修正 offset_ms（F2.8）")
        self.btn_calibrate.clicked.connect(self._on_calibrate)
        row.addWidget(self.btn_calibrate)

        row.addWidget(QLabel("offset"))
        self.spn_offset = QSlider(Qt.Orientation.Horizontal)
        self.spn_offset.setRange(-500, 500)
        self.spn_offset.setValue(0)
        self.spn_offset.setMaximumWidth(140)
        self.spn_offset.valueChanged.connect(self._on_offset_changed)
        row.addWidget(self.spn_offset)
        self.lbl_offset = QLabel("0 ms")
        self.lbl_offset.setMinimumWidth(60)
        row.addWidget(self.lbl_offset)
        row.addStretch(1)
        v.addLayout(row)
        return box

    # ================================================================== 快捷键
    def _install_shortcuts(self) -> None:
        def sc(seq: str, fn) -> None:  # noqa: ANN001
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            s.activated.connect(fn)

        sc("Space", self.controller.toggle)
        sc("Ctrl+Right", lambda: self._seek_note(1))
        sc("Ctrl+Left", lambda: self._seek_note(-1))
        sc("Right", lambda: self.controller.nudge(1000))
        sc("Left", lambda: self.controller.nudge(-1000))
        sc("Home", lambda: self.controller.seek_score(0))
        sc(">", lambda: self.controller.step_rate(1))
        sc("<", lambda: self.controller.step_rate(-1))
        sc("Ctrl+0", self.controller.reset_rate)
        # 缩放（"不能手动缩小"的修复）
        sc("Ctrl+=", lambda: self._zoom_step(1.15))
        sc("Ctrl++", lambda: self._zoom_step(1.15))
        sc("Ctrl+-", lambda: self._zoom_step(1 / 1.15))
        sc("Ctrl+W", self._toggle_fit_width)

    def _zoom_step(self, factor: float) -> None:
        if not hasattr(self.score_view, "zoom"):
            return
        self.view_host._apply_zoom(self.score_view.zoom() * factor)

    def _toggle_fit_width(self) -> None:
        bar = self.view_host.zoom_bar
        bar.btn_fit.setChecked(not bar.btn_fit.isChecked())

    # ================================================================== 套件
    def _pick_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择套件根目录", self.ed_root.text())
        if path:
            self.ed_root.setText(path)
            self.reload()

    def reload(self) -> None:
        self.lst.clear()
        root = Path(self.ed_root.text())
        suites = discover_suites(root)
        if not suites:
            self.info.setHtml(
                f"<p style='color:#888'>在 <code>{root}</code> 下没有找到套件。</p>"
                "<p>请先在「生成套件」标签页生成，或把根目录指到正确位置。</p>"
            )
            return
        for s in suites:
            problems = s.validate()
            mark = "✓" if not problems else "⚠"
            item = QListWidgetItem(f"{mark} {s.base}")
            item.setData(Qt.ItemDataRole.UserRole, str(s.dir))
            if problems:
                item.setToolTip("缺少：" + "；".join(problems))
            self.lst.addItem(item)
        self.lst.setCurrentRow(0)

    def _pick_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择套件中的音频", self.ed_root.text(), "音频 (*.mid *.midi *.mp3 *.wav)"
        )
        if not path:
            return
        p = Path(path)
        suites = discover_suites(p.parent.parent)
        target = next((s for s in suites if s.dir == p.parent), None)
        if target is None:
            target = Suite(make_layout(p.parent.parent, p.stem))
        self._select_suite(target)

    def _on_selected(self, current: QListWidgetItem | None, _prev=None) -> None:
        if current is None:
            return
        d = Path(current.data(Qt.ItemDataRole.UserRole))
        suites = discover_suites(d.parent)
        suite = next((s for s in suites if s.dir == d), None)
        if suite is not None:
            self._select_suite(suite)

    def _select_suite(self, suite: Suite) -> None:
        self._suite = suite
        self._render_info(suite)
        ok = self.controller.load_suite(suite)
        if not ok:
            self.log_dock.add_note("error", f"无法载入套件 {suite.base} 的音频")
        # 载入后刷新缩放显示（适应宽度会改变实际缩放值）
        self.view_host.refresh_zoom_display()
        self.log_dock.set_context(
            source=suite.base,
            target_dir=str(suite.dir),
            target_files=", ".join(p.name for p in suite.audio_files),
            soundfont=_soundfont_of(suite),
            engine=f"视图后端 {type(self.score_view).__name__}",
        )
        self.suite_selected.emit(suite)

    @property
    def current_suite(self) -> Suite | None:
        """当前选中的套件（供主窗口启动时同步状态栏）。"""
        return self._suite

    def _render_info(self, suite: Suite) -> None:
        problems = suite.validate()
        rows = [f"<h3>{suite.base}</h3>", f"<p style='color:#888'><code>{suite.dir}</code></p>"]
        if problems:
            rows.append(
                "<p style='color:#e05252'><b>完整性检查未通过：</b></p><ul>"
                + "".join(f"<li>{p}</li>" for p in problems)
                + "</ul>"
            )
        try:
            doc = suite.load_sync()
        except ZpyMusicError as e:
            rows.append(
                f"<p style='color:#e6b422'>无法读取同步数据：{e}<br>"
                "音频仍可播放，但不会有同步高亮。</p>"
            )
        else:
            unique = len({n.get("id") for n in doc.notes})
            parts = "".join(
                f"<li>{p.get('id')} — {p.get('name')} · ch {p.get('midi_channel')} · "
                f"{p.get('program_name')}"
                + ("" if p.get("explicit_program") else "（源文件未指定音色）")
                + "</li>"
                for p in doc.parts
            )
            rows.append(
                "<table cellpadding='2'>"
                f"<tr><td>规模</td><td>{unique} 元素 / {len(doc.notes)} 事件 / "
                f"{len(doc.systems)} 行 / {len(doc.measures)} 小节</td></tr>"
                f"<tr><td>时长</td><td>{doc.duration_ms/1000:.1f} s</td></tr>"
                f"<tr><td>速度</td><td>{doc.tempo.get('initial_bpm') or '?'} BPM</td></tr>"
                f"<tr><td>音色库</td><td>{Path(str(doc.audio.get('soundfont',''))).name}</td></tr>"
                f"<tr><td>音频</td><td>{', '.join(p.name for p in suite.audio_files)}</td></tr>"
                "</table>"
                f"<p><b>声部</b></p><ul>{parts}</ul>"
            )
        rows.append(
            "<p style='color:#888;font-size:9pt'>快捷键：空格 播放/暂停 · "
            "←/→ 快退/快进 1s · Ctrl+←/→ 上/下一个音符 · &lt;/&gt; 变速 · Ctrl+0 回 100%"
            "</p>"
        )
        self.info.setHtml("".join(rows))

    # ================================================================== 传输
    def _on_play_clicked(self) -> None:
        self.controller.toggle()

    def _seek_note(self, direction: int) -> None:
        tl = self.controller.timeline
        if tl is None:
            return
        cur = self.controller.score_time()
        onsets = [o for o, _d, _i in tl.entries]
        if not onsets:
            return
        if direction > 0:
            nxt = next((o for o in onsets if o > cur + 1), onsets[-1])
        else:
            prev = [o for o in onsets if o < cur - 1]
            nxt = prev[-1] if prev else 0
        self.controller.seek_score(nxt)

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_slider_moved(self, value: int) -> None:
        # 拖动时实时预览目标位置的高亮（F2.4）
        self._seeking = True
        if self.controller.duration_ms:
            target = int(value / 1000 * self.controller.duration_ms)
            self.lbl_time.setText(_fmt(target))
            self._preview(target)

    def _on_slider_released(self) -> None:
        self._seeking = False
        if self.controller.duration_ms:
            self.controller.seek_fraction(self.slider.value() / 1000)

    def _preview(self, score_ms: int) -> None:
        """拖动预览：直接按乐谱时间刷新高亮，不改动播放位置。"""
        self.controller.preview_at(float(score_ms))

    def _on_rate_changed(self) -> None:
        rate = self.cmb_rate.currentData()
        if rate:
            self.controller.set_rate(float(rate))

    def _on_follow_toggled(self, checked: bool) -> None:
        self.controller.set_follow(checked)
        self.chk_follow.setText(f"跟随滚动：{'开' if checked else '关'}")
        self.config.ui.follow_playback = checked

    def _on_calibrate(self) -> None:
        value = self.controller.calibrate_to_active()
        self.spn_offset.blockSignals(True)
        self.spn_offset.setValue(int(max(-500, min(500, value))))
        self.spn_offset.blockSignals(False)
        self.lbl_offset.setText(f"{value:.0f} ms")
        self.log_dock.add_note("info", f"已校准 offset = {value:.0f} ms")

    def _on_offset_changed(self, value: int) -> None:
        self.controller.set_offset(float(value))
        self.lbl_offset.setText(f"{value} ms")

    def _apply_style_from_config(self) -> None:
        color = self.ed_color.text().strip() or "#FF8C00"
        opacity = self.sld_opacity.value() / 100.0
        self.lbl_opacity.setText(f"{int(opacity*100)}%")
        self.config.ui.highlight_color = color
        self.config.ui.highlight_opacity = opacity
        self.controller.set_highlight_style(color, opacity)

    def _pick_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog  # noqa: PLC0415

        c = QColorDialog.getColor(QColor(self.ed_color.text()), self, "选择高亮颜色")
        if c.isValid():
            self.ed_color.setText(c.name())
            self._apply_style_from_config()

    # ================================================================== 点击跳转
    def _setup_click_jump(self) -> None:
        if hasattr(self.score_view, "element_clicked"):
            self.score_view.element_clicked.connect(self._on_element_clicked)

    def _on_element_clicked(self, element_id: str) -> None:
        onset = self.controller.timeline.onset_of(element_id) if self.controller.timeline else None
        if onset is None:
            self.log_dock.add_note("warning", f"该位置（{element_id}）没有对应的时间信息")
            return
        self.controller.seek_score(onset)
        where = "正在播放" if self.controller.state.value == "playingState" else "已就位"
        self.log_dock.add_note(
            "info", f"跳到音符 {element_id}（{_fmt(onset)}）—— {where}"
        )

    # ================================================================== 状态
    def _on_status(self, st: PlaybackStatus) -> None:
        if not self._seeking and st.duration_ms:
            self.slider.blockSignals(True)
            self.slider.setValue(int(st.score_ms / st.duration_ms * 1000))
            self.slider.blockSignals(False)
        self.lbl_time.setText(_fmt(st.score_ms))
        self.lbl_total.setText(_fmt(st.duration_ms))
        self.lbl_measure.setText(f"小节 {st.measure_id or '—'}")
        self.lbl_active.setText(
            ("♪ " + ",".join(st.active_ids[:4])) if st.active_ids else ""
        )
        self.btn_play.setText("⏸ 暂停" if st.state == "playingState" else "▶ 播放")
        self.lbl_bpm.setText(
            f"BPM {st.effective_bpm:.0f}" if st.effective_bpm else "BPM —"
        )
        if abs(st.rate - 1.0) > 1e-6:
            self.lbl_bpm.setText(self.lbl_bpm.text() + f"（×{st.rate:.2f}）")
        # 速率下拉与状态同步（用快捷键改速率时）
        idx = min(
            range(self.cmb_rate.count()),
            key=lambda i: abs(float(self.cmb_rate.itemData(i)) - st.rate),
        )
        if abs(float(self.cmb_rate.currentData()) - st.rate) > 1e-6:
            self.cmb_rate.blockSignals(True)
            self.cmb_rate.setCurrentIndex(idx)
            self.cmb_rate.blockSignals(False)

    def _on_error(self, text: str) -> None:
        self.log_dock.add_note("error", text)

    def _on_finished(self) -> None:
        self.log_dock.add_note("info", "播放结束")
        self._emit_status_final()

    def _emit_status_final(self) -> None:
        QTimer.singleShot(0, lambda: self.controller._emit_status(force=True))

    # ================================================================== 其他
    def load_suite_dir(self, suite_dir: Path) -> None:
        """供主窗口调用（生成完成后自动切到播放页时使用）。"""
        suites = discover_suites(Path(suite_dir).parent)
        target = next((s for s in suites if s.dir == Path(suite_dir)), None)
        if target is None:
            return
        root = str(Path(suite_dir).parent)
        if self.ed_root.text() != root:
            self.ed_root.setText(root)
            self.reload()
        for i in range(self.lst.count()):
            item = self.lst.item(i)
            if Path(item.data(Qt.ItemDataRole.UserRole)) == Path(suite_dir):
                self.lst.setCurrentRow(i)
                break

    def save_config(self) -> None:
        self.config.save()


def _fmt(ms: int) -> str:
    ms = max(0, int(ms))
    s, msec = divmod(ms, 1000)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{msec // 100}"


def _soundfont_of(suite: Suite) -> str:
    try:
        doc = suite.load_sync()
    except Exception:  # noqa: BLE001
        return ""
    return Path(str(doc.audio.get("soundfont", ""))).name
