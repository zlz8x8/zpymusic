"""主窗口（需求 §5.5 F4.1–F4.9）。

结构::

    QMainWindow
      ├── 菜单栏（文件 / 工具 / 帮助）
      ├── 中心 QTabWidget（标签位置可配置，默认左侧）
      │     ├── 生成套件   tab_generate.GenerateTab
      │     ├── 同步播放   tab_play.PlayTab          （M4 完整实现）
      │     └── 格式转换   tab_convert.ConvertTab    （M5 完整实现）
      ├── 底部 QDockWidget：日志 / 信息 / 警告（log_dock.LogDock）
      └── 状态栏：当前套件 / 音色库 / 引擎版本 / 进度
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QTabWidget,
    QWidget,
)

from .. import __version__
from ..common.config import AppConfig
from ..common.deps import DependencyReport, probe_all
from ..common.log import get_logger, setup_logging
from ..common.paths import PROJECT_ROOT
from .job import Job
from .log_dock import LogDock, install_qt_log_handler
from .settings_dialog import SettingsDialog
from .tab_convert import ConvertTab
from .tab_generate import GenerateTab
from .tab_play import PlayTab

log = get_logger(__name__)

_TAB_POSITIONS = {
    "west": QTabWidget.TabPosition.West,
    "north": QTabWidget.TabPosition.North,
    "east": QTabWidget.TabPosition.East,
    "south": QTabWidget.TabPosition.South,
}


def app_icon() -> QIcon:
    """用代码画一个简单的八分音符图标，避免额外的资源文件。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(Qt.GlobalColor.white)
    p.setPen(Qt.GlobalColor.white)
    p.drawEllipse(14, 42, 18, 14)
    p.drawEllipse(38, 36, 18, 14)
    p.setPen(Qt.GlobalColor.white)
    p.drawLine(30, 48, 30, 14)
    p.drawLine(54, 42, 54, 8)
    p.drawLine(30, 14, 54, 8)
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    """zpyMusic 主窗口。"""

    def __init__(self, config: AppConfig | None = None) -> None:
        super().__init__()
        self.config = config or AppConfig.load()

        self.setWindowTitle(f"zpyMusic {__version__} —— MusicXML 曲谱转换与同步播放")
        self.setWindowIcon(app_icon())
        self.resize(1280, 860)

        # --- 日志区（要在其它部件之前建好，便于接日志） ---------------------
        self.log_dock = LogDock(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.log_dock.setMinimumHeight(160)
        self.log_handler = install_qt_log_handler(self.log_dock)

        # --- 三个标签页 -----------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(_TAB_POSITIONS.get(self.config.ui.tab_position, QTabWidget.TabPosition.West))
        self.tabs.setDocumentMode(True)

        self.tab_generate = GenerateTab(self.config, self.log_dock, self)
        self.tab_play = PlayTab(self.config, self.log_dock, self)
        self.tab_convert = ConvertTab(self.config, self.log_dock, self)

        self.tabs.addTab(self.tab_generate, "生成套件")
        self.tabs.addTab(self.tab_play, "同步播放")
        self.tabs.addTab(self.tab_convert, "格式转换")
        self.setCentralWidget(self.tabs)

        self.tab_generate.suite_created.connect(self._on_suite_created)
        # 格式转换页（M5）：产物可载入播放界面；依赖缺失时可从这里弹设置
        self.tab_convert.suite_ready.connect(self._on_convert_suite_ready)
        self.tab_convert.settings_requested.connect(self.open_settings)

        self._build_menu()
        self._build_statusbar()
        # 状态栏的"当前套件 / 音色库"由播放页的选择驱动：
        # 原先只有"生成套件"成功时才写一次，切套件从不更新（一直显示"未选择套件"）。
        self.tab_play.suite_selected.connect(self._on_suite_selected)
        self._on_suite_selected(self.tab_play.current_suite)

        self.log_dock.add_note(
            "info",
            f"zpyMusic {__version__} 已启动；项目根目录 {PROJECT_ROOT}",
        )
        self._restore_state()
        self._probe_environment()

    # ------------------------------------------------------------------ 菜单
    def _build_menu(self) -> None:
        bar = self.menuBar()

        m_file = bar.addMenu("文件(&F)")
        act = QAction("生成套件…", self)
        act.setShortcut(QKeySequence("Ctrl+G"))
        act.triggered.connect(lambda: self.tabs.setCurrentWidget(self.tab_generate))
        m_file.addAction(act)

        act = QAction("同步播放…", self)
        act.triggered.connect(lambda: self.tabs.setCurrentWidget(self.tab_play))
        m_file.addAction(act)

        act = QAction("格式转换…", self)
        act.triggered.connect(lambda: self.tabs.setCurrentWidget(self.tab_convert))
        m_file.addAction(act)

        m_file.addSeparator()
        act = QAction("打开项目目录", self)
        act.triggered.connect(lambda: _open_path(PROJECT_ROOT, self.log_dock))
        m_file.addAction(act)

        act = QAction("退出", self)
        act.setShortcut(QKeySequence.StandardKey.Quit)
        act.triggered.connect(self.close)
        m_file.addAction(act)

        m_tools = bar.addMenu("工具(&T)")
        act = QAction("设置…", self)
        act.setShortcut(QKeySequence("Ctrl+,"))
        act.triggered.connect(self.open_settings)
        m_tools.addAction(act)

        act = QAction("重新探测环境", self)
        act.setShortcut(QKeySequence("F5"))
        act.triggered.connect(self._probe_environment)
        m_tools.addAction(act)

        m_tools.addSeparator()
        self.m_pos = m_tools.addMenu("标签位置")
        for label, key in (("左侧", "west"), ("上方", "north"), ("右侧", "east"), ("下方", "south")):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(self.config.ui.tab_position == key)
            a.triggered.connect(lambda _c=False, k=key: self._set_tab_position(k))
            self.m_pos.addAction(a)

        m_view = bar.addMenu("视图(&V)")
        act = self.log_dock.toggleViewAction()
        act.setText("显示日志区")
        m_view.addAction(act)

        m_help = bar.addMenu("帮助(&H)")
        act = QAction("格式转换能力矩阵…", self)
        act.triggered.connect(self._show_conversions)
        m_help.addAction(act)

        act = QAction("关于 zpyMusic…", self)
        act.triggered.connect(self._about)
        m_help.addAction(act)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.lbl_current = QLabel("未选择套件")
        self.lbl_soundfont = QLabel("")
        self.lbl_engine = QLabel("")
        self.lbl_deps = QLabel("环境探测中…")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(180)
        self.progress.setVisible(False)

        sb.addWidget(self.lbl_current, 1)
        sb.addPermanentWidget(self.lbl_soundfont)
        sb.addPermanentWidget(self.lbl_engine)
        sb.addPermanentWidget(self.lbl_deps)
        sb.addPermanentWidget(self.progress)

    # ------------------------------------------------------------------ 环境
    def _probe_environment(self) -> None:
        """探测外部依赖（需求 §3.3）。

        **同步执行，且刻意不放在工作线程**：探测过程会启动子进程
        （MuseScore / ffmpeg 的 ``--version``），而实测表明，
        若探测在工作线程跑、同时主线程创建 ``QSvgRenderer``（曲谱视图挂载 SVG），
        会触发 QtSvg 的 access violation（数据竞争）。
        探测本身很轻（约 10 ms，且已去掉子进程调用），同步执行对启动速度无影响。
        """
        self.lbl_deps.setText("环境探测中…")
        self.lbl_deps.setStyleSheet("color:#e6b422;")
        QApplication.processEvents()
        report, error = None, ""
        try:
            report = probe_all()
        except Exception as e:  # noqa: BLE001 - 探测失败不应阻塞启动
            error = f"{type(e).__name__}: {e}"
        self._on_probed(report, error)

    def _on_probed(self, report: object, error: str) -> None:
        if error or not isinstance(report, DependencyReport):
            self.lbl_deps.setText("环境探测失败")
            self.lbl_deps.setStyleSheet("color:#e05252;")
            self.log_dock.add_note("error", f"环境探测失败：{error}")
            return
        for line in report.summary_lines():
            self.log_dock.add_note("info" if "OK" in line else "warning", f"依赖探测：{line}")

        missing_required = report.missing_required
        missing_optional = [d for d in report.warnings if not d.required]
        if missing_required:
            self.lbl_deps.setText(f"缺少 {len(missing_required)} 项核心依赖")
            self.lbl_deps.setStyleSheet("color:#e05252;")
            self.lbl_deps.setToolTip("\n".join(f"{d.name}: {d.hint}" for d in missing_required))
            self.log_dock.add_note(
                "error",
                "核心依赖缺失，生成套件将不可用："
                + "；".join(f"{d.name}（{d.hint}）" for d in missing_required),
            )
        elif missing_optional:
            self.lbl_deps.setText(f"就绪（{len(missing_optional)} 项可选能力不可用）")
            self.lbl_deps.setStyleSheet("color:#e6b422;")
            self.lbl_deps.setToolTip("\n".join(f"{d.name}: {d.hint}" for d in missing_optional))
        else:
            self.lbl_deps.setText("全部依赖可用 ✓")
            self.lbl_deps.setStyleSheet("color:#4ec94e;")

        v = report.get("verovio")
        f = report.get("fluidsynth")
        self.lbl_engine.setText(f"verovio {v.version}" if v and v.available else "verovio 不可用")
        if f and f.available:
            self.lbl_engine.setText(self.lbl_engine.text() + f" | fluidsynth {f.version}")
        # 音色库：只有在还没有选中套件时才用配置值兜底 ——
        # 套件自己的 sync.json 里记着它是用哪个音色库生成的，那个更准确。
        if self.tab_play.current_suite is None:
            sf = Path(self.config.audio.soundfont_path(PROJECT_ROOT)).name
            self.lbl_soundfont.setText(f"音色库 {sf}")

    # ------------------------------------------------------------------ 槽
    def _on_suite_selected(self, suite: object | None) -> None:
        """播放页选中/载入了套件 → 刷新状态栏（F4.1 当前套件 / 音色库）。"""
        if suite is None:
            self.lbl_current.setText("未选择套件")
            self.lbl_current.setToolTip("")
            return
        base = str(getattr(suite, "base", "") or "")
        suite_dir = str(getattr(suite, "dir", "") or "")
        self.lbl_current.setText(f"当前套件：{base}" if base else "当前套件：—")
        self.lbl_current.setToolTip(suite_dir)
        try:
            audio = suite.load_sync().audio  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - 缺 sync.json 时其余状态照旧
            return
        soundfont = str(audio.get("soundfont", "") or "")
        if soundfont:
            self.lbl_soundfont.setText(f"音色库 {Path(soundfont).name}")
            self.lbl_soundfont.setToolTip(soundfont)

    def _on_suite_created(self, suite_dir: Path) -> None:
        # 让播放页真正切到刚生成的套件（load_suite_dir 之前是死代码，从没被调用）
        try:
            self.tab_play.load_suite_dir(Path(suite_dir))
        except OSError:
            pass
        current = self.tab_play.current_suite
        if current is not None and Path(current.dir) == Path(suite_dir):
            # 若该行本来就是当前行，setCurrentRow 不会发信号，这里补刷一次状态栏
            self._on_suite_selected(current)
        else:
            self.lbl_current.setText(f"最新套件：{Path(suite_dir).name}")

    def _on_convert_suite_ready(self, suite_dir: Path) -> None:
        """「格式转换」页产出了可播放的套件：切到播放页并载入（F3.6 的"载入播放界面"）。"""
        self._on_suite_created(Path(suite_dir))
        self.tabs.setCurrentWidget(self.tab_play)
        self.log_dock.add_note("info", f"已把转换产物载入播放界面：{Path(suite_dir).name}")

    def _set_tab_position(self, key: str) -> None:
        self.config.ui.tab_position = key
        self.tabs.setTabPosition(_TAB_POSITIONS.get(key, QTabWidget.TabPosition.West))
        for a in self.m_pos.actions():
            a.setChecked(a.text() == {"west": "左侧", "north": "上方", "east": "右侧", "south": "下方"}[key])
        self.config.save()
        self.log_dock.add_note("info", f"标签位置已切换为 {key}")

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.config, self)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            dlg.apply_to_config()
            self.config.save()
            self.tabs.setTabPosition(
                _TAB_POSITIONS.get(self.config.ui.tab_position, QTabWidget.TabPosition.West)
            )
            self.tab_generate._populate_soundfonts()
            self.tab_convert.refresh_environment()  # 路径改了 → 重新做依赖自检（F3.2）
            self.log_dock.add_note("info", "设置已保存到 config.json；部分依赖变更需重新探测（F5）")
            self._probe_environment()

    def _show_conversions(self) -> None:
        from ..cli import CONVERSIONS  # noqa: PLC0415

        lines = [
            f"{s:10s} → {t:10s} [{prio}] {how}"
            for (s, t), (prio, how) in sorted(CONVERSIONS.items(), key=lambda kv: kv[1][0])
        ]
        box = QMessageBox(self)
        box.setWindowTitle("格式转换能力矩阵")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText("\n".join(lines))
        box.exec()

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "关于 zpyMusic",
            f"<h3>zpyMusic {__version__}</h3>"
            "<p>MusicXML 曲谱转换与 SVG 同步播放工具</p>"
            "<p style='color:#888'>以 MusicXML 为权威数据源，"
            "生成 SVG（每行一个文件，含音符 ID）/ MIDI / MP3 / sync.json 套件；"
            "播放时在 SVG 上同步高亮当前小节。</p>"
            f"<p style='color:#888'>项目根目录：{PROJECT_ROOT}</p>"
            "<p style='color:#888'>需求文档：docs/requirements.md（v2.0）</p>",
        )

    # ------------------------------------------------------------------ 状态持久化
    def _restore_state(self) -> None:
        from PySide6.QtCore import QSettings  # noqa: PLC0415

        s = QSettings("zpyMusic", "zpyMusic")
        geo = s.value("window/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        state = s.value("window/state")
        if isinstance(state, QByteArray):
            self.restoreState(state)
        # 把上次的源目录填进生成页
        last = s.value("generate/last_source")
        if isinstance(last, str) and Path(last).exists():
            self.tab_generate.ed_source.setText(last)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        from PySide6.QtCore import QSettings  # noqa: PLC0415

        runner = self.tab_generate._runner
        if runner is not None and runner.is_running:
            reply = QMessageBox.question(
                self,
                "任务仍在运行",
                "生成任务尚未结束，确定要退出吗？（当前文件会被中断）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            runner.cancel()
            runner.wait(5000)

        # 预览窗口（非模态）可能还有后台渲染 / MIDI 合成在跑：停播并等它收尾
        self.tab_convert.shutdown_previews()

        s = QSettings("zpyMusic", "zpyMusic")
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("window/state", self.saveState())
        s.setValue("generate/last_source", self.tab_generate.ed_source.text())
        self.config.save()
        super().closeEvent(event)


def _open_path(path: Path, log_dock: LogDock) -> None:
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as e:
        log_dock.add_note("error", f"无法打开 {path}：{e}")


def run_gui(argv: list[str] | None = None) -> int:
    """启动 GUI（``python -m zpymusic.gui`` 或控制台脚本）。"""
    import logging  # noqa: PLC0415
    import sys  # noqa: PLC0415

    # 注意：这里**刻意不再**预先注入 WebEngine / Qt 的 GPU 环境变量。
    #
    # 历史做法是在 QApplication 之前无条件调用 prepare_webengine_env()，
    # 而那个函数会注入 --disable-gpu / QT_OPENGL=software / QT_QUICK_BACKEND=software。
    # 实测（见 sync/web_view.py 模块文档）这三样正是
    # "Failed to create GLES3 context" / "ContextResult::kFatalFailure" 两条报错的来源，
    # 而且它们会随 os.environ 泄漏给**所有**子进程（MuseScore 因此崩过，见需求 §12.10）。
    #
    # 现在只在真正要构造 QWebEngineView 时、由 WebScoreView.__init__ 按
    # 子进程探测出的 profile 注入（默认 profile 不关 GPU）。默认后端是 auto，
    # 走原生 QtSvg 时一个 Qt GL 变量都不会被设置。

    setup_logging(logging.INFO)
    app = QApplication(argv if argv is not None else sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("zpyMusic")
    app.setOrganizationName("zpyMusic")
    app.setWindowIcon(app_icon())

    config = AppConfig.load()
    win = MainWindow(config)
    win.show()
    return app.exec()
