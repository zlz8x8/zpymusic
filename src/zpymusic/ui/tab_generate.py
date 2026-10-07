"""界面一：生成播放套件（需求 §5.2 F1.1–F1.10）。

交互设计：

* **单文件模式**（默认）：可以精细控制音色映射（F1.8）。
* **整目录模式**（勾选"处理目录下全部"）：批量处理，使用源文件自带的音色信息，
  不提供逐文件编辑（否则 10 个文件的映射表无法在一个表里表达）。
* 所有耗时步骤在 :class:`~zpymusic.ui.worker.BatchRunner` 的线程里执行，
  UI 不冻结，且支持取消（F1.9）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..common.config import AppConfig
from ..common.errors import TaskCancelled
from ..common.log import get_logger
from ..common.paths import PROJECT_ROOT, SOUND_DIR
from ..core.musicxml_io import MUSICXML_SUFFIXES, iter_source_files, read_musicxml
from ..core.pipeline import GenerateOptions, GenerateResult, generate_suite
from ..core.suite import OverwritePolicy
from .parts_table import PartsTable
from .worker import BatchRunner, TaskOutcome

log = get_logger(__name__)

_STATUS_MARK = {"ok": "✓ 成功", "skipped": "— 跳过", "failed": "✗ 失败", "cancelled": "✕ 取消"}


class GenerateTab(QWidget):
    """「生成套件」标签页。"""

    suite_created = Signal(Path)
    """生成成功后发出，便于主窗口通知播放界面（M4 使用）。"""

    def __init__(self, config: AppConfig, log_dock, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.log_dock = log_dock
        self._runner: BatchRunner | None = None
        self._current_doc = None
        self._results: list[GenerateResult] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # ================================================== 上：源与目标
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)

        gb_src = QGroupBox("1. 源 MusicXML")
        src_form = QVBoxLayout(gb_src)
        row = QHBoxLayout()
        self.ed_source = QLineEdit()
        self.ed_source.setPlaceholderText("选择 .musicxml / .xml / .mxl 文件，或选择目录后勾选批量")
        self.ed_source.textChanged.connect(self._on_source_changed)
        row.addWidget(self.ed_source, 1)
        btn_file = QPushButton("选择文件…")
        btn_file.clicked.connect(self._pick_source_file)
        row.addWidget(btn_file)
        btn_dir = QPushButton("选择目录…")
        btn_dir.clicked.connect(self._pick_source_dir)
        row.addWidget(btn_dir)
        src_form.addLayout(row)

        self.chk_batch = QCheckBox("处理目录下全部 MusicXML（批量模式，不支持逐文件编辑音色）")
        self.chk_batch.toggled.connect(self._on_batch_toggled)
        src_form.addWidget(self.chk_batch)

        self.lst_sources = QListWidget()
        self.lst_sources.setMaximumHeight(96)
        self.lst_sources.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        src_form.addWidget(self.lst_sources)
        top_layout.addWidget(gb_src, 3)

        gb_dst = QGroupBox("2. 目标与产物")
        dst_form = QFormLayout(gb_dst)
        row = QHBoxLayout()
        self.ed_target = QLineEdit(str(config.suites_path()))
        row.addWidget(self.ed_target, 1)
        btn_target = QPushButton("选择…")
        btn_target.clicked.connect(self._pick_target_dir)
        row.addWidget(btn_target)
        dst_form.addRow("套件根目录", row)

        self.cmb_soundfont = QComboBox()
        self._populate_soundfonts()
        dst_form.addRow("音色库 (sf2/sf3)", self.cmb_soundfont)

        self.cmb_overwrite = QComboBox()
        for label, value in (
            ("跳过已存在（推荐）", OverwritePolicy.SKIP),
            ("覆盖已有文件", OverwritePolicy.OVERWRITE),
            ("新建带序号目录", OverwritePolicy.NEWDIR),
        ):
            self.cmb_overwrite.addItem(label, value)
        dst_form.addRow("同名冲突", self.cmb_overwrite)

        opts = QHBoxLayout()
        self.chk_svg = _check("SVG 曲谱", True)
        self.chk_midi = _check("MIDI", True)
        self.chk_mp3 = _check("MP3", True)
        self.chk_wav = _check("保留 WAV", config.audio.keep_wav)
        for c in (self.chk_svg, self.chk_midi, self.chk_mp3, self.chk_wav):
            opts.addWidget(c)
        dst_form.addRow("产物", opts)
        top_layout.addWidget(gb_dst, 2)
        splitter.addWidget(top)

        # ================================================== 中：音色映射表
        self.parts = PartsTable()
        self.parts.btn_reset.clicked.connect(self._reload_parts)
        self.parts.btn_reset.setEnabled(True)
        splitter.addWidget(self.parts)

        # ================================================== 下：进度与结果
        bottom = QWidget()
        bot = QVBoxLayout(bottom)
        bot.setContentsMargins(0, 0, 0, 0)

        gb_prog = QGroupBox("3. 生成")
        prog = QVBoxLayout(gb_prog)
        self.progress = QProgressBar()
        self.progress.setFormat("%p%")
        prog.addWidget(self.progress)
        self.lbl_stage = QLabel("就绪")
        self.lbl_stage.setStyleSheet("color:#888;")
        prog.addWidget(self.lbl_stage)

        row = QHBoxLayout()
        self.btn_generate = QPushButton("开始生成")
        self.btn_generate.setDefault(True)
        self.btn_generate.clicked.connect(self._start)
        row.addWidget(self.btn_generate)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        row.addWidget(self.btn_cancel)

        btn_clear = QPushButton("清空结果")
        btn_clear.clicked.connect(self._clear_results)
        row.addWidget(btn_clear)

        row.addStretch(1)
        self.btn_open = QPushButton("打开套件目录")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_suite_dir)
        row.addWidget(self.btn_open)
        prog.addLayout(row)
        bot.addWidget(gb_prog)

        self.tbl_results = QTableWidget(0, 4)
        self.tbl_results.setHorizontalHeaderLabels(["文件", "状态", "规模", "说明"])
        self.tbl_results.verticalHeader().setVisible(False)
        self.tbl_results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.tbl_results.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl_results.doubleClicked.connect(self._on_result_double_clicked)
        bot.addWidget(self.tbl_results, 1)
        splitter.addWidget(bottom)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        root.addWidget(splitter)

        self._on_source_changed("")

    # ------------------------------------------------------------------ 源
    def _populate_soundfonts(self) -> None:
        self.cmb_soundfont.clear()
        fonts: list[Path] = []
        for pat in ("*.sf2", "*.sf3"):
            fonts.extend(sorted(SOUND_DIR.glob(pat)))
        default = self.config.audio.soundfont_path(PROJECT_ROOT)
        for f in fonts:
            try:
                rel = f.relative_to(PROJECT_ROOT).as_posix()
            except ValueError:
                rel = str(f)
            size = f.stat().st_size / 1e6
            self.cmb_soundfont.addItem(f"{f.name}  ({size:.0f} MB)", rel)
        if not fonts:
            self.cmb_soundfont.addItem("（sound/ 目录下没有音色库）", "")
        # 选中配置里的默认值
        target = self.config.audio.default_soundfont
        for i in range(self.cmb_soundfont.count()):
            if self.cmb_soundfont.itemData(i) == target:
                self.cmb_soundfont.setCurrentIndex(i)
                break

    def _pick_source_file(self) -> None:
        patterns = " ".join(f"*{s}" for s in sorted(MUSICXML_SUFFIXES))
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 MusicXML 源文件", str(self.config.musicxml_path()),
            f"MusicXML ({patterns});;所有文件 (*)",
        )
        if path:
            self.chk_batch.setChecked(False)
            self.ed_source.setText(path)

    def _pick_source_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择源目录", str(self.config.musicxml_path())
        )
        if path:
            self.ed_source.setText(path)
            self.chk_batch.setChecked(True)

    def _pick_target_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择套件根目录", self.ed_target.text())
        if path:
            self.ed_target.setText(path)

    def _on_batch_toggled(self, _checked: bool) -> None:
        self._on_source_changed(self.ed_source.text())

    def _on_source_changed(self, _text: str) -> None:
        """刷新源文件列表；单文件模式下顺带刷新音色映射表。"""
        self.lst_sources.clear()
        src = Path(self.ed_source.text().strip()) if self.ed_source.text().strip() else None
        files: list[Path] = []
        if src is not None and src.exists():
            if src.is_dir():
                files = iter_source_files(src) if self.chk_batch.isChecked() else []
            elif src.is_file():
                files = [src]

        for f in files:
            item = QListWidgetItem(f.name)
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            item.setToolTip(str(f))
            self.lst_sources.addItem(item)

        single = files[0] if (len(files) == 1 and not self.chk_batch.isChecked()) else None
        if single is None and src is not None and src.is_file() and not self.chk_batch.isChecked():
            single = src
        self._load_parts(single)

    def _load_parts(self, source: Path | None) -> None:
        self.parts.set_reset_enabled(source is not None)
        if source is None:
            self._current_doc = None
            self.parts.load_document(None)
            return
        try:
            self._current_doc = read_musicxml(source)
        except Exception as e:  # noqa: BLE001 - 读取失败只提示，不崩
            self._current_doc = None
            self.parts.load_document(None)
            self.log_dock.add_note("error", f"无法读取 {source.name}：{e}")
            return
        self.parts.load_document(self._current_doc)
        doc = self._current_doc
        self.log_dock.set_context(
            **{
                "source": f"{doc.source.name}（{doc.part_count} 声部）",
                "target_dir": self.ed_target.text(),
                "soundfont": Path(str(self.cmb_soundfont.currentData())).name,
                "engine": f"verovio {_verovio_version()}",
            }
        )
        for w in doc.warnings:
            self.log_dock.add_note("warning", w)

    def _reload_parts(self) -> None:
        src = Path(self.ed_source.text().strip()) if self.ed_source.text().strip() else None
        self._load_parts(src if src and src.is_file() else None)

    # ------------------------------------------------------------------ 生成
    def _collect_sources(self) -> list[Path]:
        src_text = self.ed_source.text().strip()
        if not src_text:
            return []
        src = Path(src_text)
        if not src.exists():
            return []
        if src.is_dir():
            return iter_source_files(src)
        if src.is_file():
            return [src]
        return []

    def _start(self) -> None:
        if self._runner is not None and self._runner.is_running:
            self.log_dock.add_note("warning", "已有任务在运行")
            return
        sources = self._collect_sources()
        if not sources:
            QMessageBox.warning(self, "没有源文件", "请先选择有效的 MusicXML 文件或目录。")
            return

        if self.chk_batch.isChecked() and len(sources) > 1:
            dup_warn = (
                f"批量模式将处理 {len(sources)} 个文件，"
                "所有文件都使用各自源文件中的音色信息（表格中的修改不会应用）。\n\n继续？"
            )
        else:
            dup_warn = ""
        if dup_warn and QMessageBox.question(
            self, "确认批量生成", dup_warn,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        ) != QMessageBox.StandardButton.Yes:
            return

        target = Path(self.ed_target.text().strip())
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "目标目录不可用", f"{target}\n\n{e}")
            return

        soundfont_rel = self.cmb_soundfont.currentData()
        if not soundfont_rel:
            QMessageBox.warning(self, "缺少音色库", "sound/ 目录下没有可用的 .sf2/.sf3 文件。")
            return
        soundfont = PROJECT_ROOT / str(soundfont_rel)

        self.config.suites_dir = str(target)
        self.config.audio.default_soundfont = str(soundfont_rel)
        self.config.overwrite_policy = str(self.cmb_overwrite.currentData())
        self.config.audio.keep_wav = self.chk_wav.isChecked()

        options = GenerateOptions(
            suites_root=target,
            overwrite=str(self.cmb_overwrite.currentData()),
            keep_wav=self.chk_wav.isChecked(),
            render_svg=self.chk_svg.isChecked(),
            render_midi=self.chk_midi.isChecked(),
            render_audio=self.chk_mp3.isChecked() or self.chk_wav.isChecked(),
            encode_mp3=self.chk_mp3.isChecked(),
            copy_musicxml=True,
            verify=True,
            soundfont=soundfont,
            part_overrides=(
                self.parts.overrides() if not self.chk_batch.isChecked() else None
            ),
        )

        self._clear_results()
        self.log_dock.set_context(
            **{
                "target_dir": str(target),
                "soundfont": soundfont.name,
                "engine": f"verovio {_verovio_version()}",
            }
        )
        conflicts = self.parts.channel_conflicts()
        for c in conflicts:
            self.log_dock.add_note("warning", f"音色映射：{c}")

        self.progress.setRange(0, len(sources))
        self.progress.setValue(0)
        self.lbl_stage.setText(f"开始处理 {len(sources)} 个文件…")
        self._set_running(True)

        def work(_index, item, report_progress, is_cancelled):
            path = Path(item)
            if is_cancelled():
                raise TaskCancelled("已取消")
            res = generate_suite(
                path,
                self.config,
                options,
                progress=report_progress,
                cancelled=is_cancelled,
            )
            return TaskOutcome(
                key=str(path), ok=res.ok, payload=res, error=res.message, warnings=res.warnings
            )

        self._runner = BatchRunner(sources, work, parent=self)
        self._runner.progress.connect(self._on_progress)
        self._runner.message.connect(self._on_message)
        self._runner.item_done.connect(self._on_item_done)
        self._runner.finished.connect(self._on_finished)
        self._runner.start()

    def _cancel(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            self.btn_cancel.setEnabled(False)

    def _set_running(self, running: bool) -> None:
        self.btn_generate.setEnabled(not running)
        self.btn_cancel.setEnabled(running)
        for w in (self.chk_batch, self.ed_source, self.ed_target, self.cmb_soundfont,
                  self.cmb_overwrite, self.chk_svg, self.chk_midi, self.chk_mp3, self.chk_wav):
            w.setEnabled(not running)

    # ------------------------------------------------------------------ 回调
    def _on_progress(self, _index: int, _total: int, stage: str) -> None:
        self.lbl_stage.setText(stage)

    def _on_message(self, level: str, text: str) -> None:
        self.log_dock.add_note(level, text)

    def _on_item_done(self, outcome: TaskOutcome) -> None:
        res = outcome.payload
        if not isinstance(res, GenerateResult):
            return
        self._results.append(res)
        self.progress.setValue(len(self._results))
        self._add_result_row(res)

    def _add_result_row(self, res: GenerateResult) -> None:
        r = self.tbl_results.rowCount()
        self.tbl_results.insertRow(r)

        name = QTableWidgetItem(res.source.name)
        name.setToolTip(str(res.suite_dir or res.source))
        self.tbl_results.setItem(r, 0, name)

        status = QTableWidgetItem(_STATUS_MARK.get(res.status, res.status))
        if res.status == "ok":
            status.setForeground(Qt.GlobalColor.green)
        elif res.status == "failed":
            status.setForeground(Qt.GlobalColor.red)
        self.tbl_results.setItem(r, 1, status)

        scale = QTableWidgetItem(
            f"{res.svg_count} system / {res.note_count} 音符 / {res.duration_ms/1000:.1f}s"
            if res.note_count else "—"
        )
        self.tbl_results.setItem(r, 2, scale)

        detail = res.message
        if res.warnings:
            detail += f"（{len(res.warnings)} 条警告）"
        item = QTableWidgetItem(detail)
        item.setToolTip("\n".join(res.warnings[:20]))
        self.tbl_results.setItem(r, 3, item)
        self.tbl_results.scrollToBottom()

    def _on_finished(self, outcomes: list) -> None:
        self._set_running(False)
        ok = sum(1 for r in self._results if r.status == "ok")
        skipped = sum(1 for r in self._results if r.status == "skipped")
        failed = sum(1 for r in self._results if r.status == "failed")
        cancelled = sum(1 for o in outcomes if getattr(o, "cancelled", False))
        self.lbl_stage.setText(
            f"完成：成功 {ok}，跳过 {skipped}，失败 {failed}"
            + (f"，取消 {cancelled}" if cancelled else "")
        )
        self.progress.setValue(self.progress.maximum())
        self.btn_open.setEnabled(any(r.ok for r in self._results))
        self.config.save()

        for r in self._results:
            if r.ok and r.suite_dir:
                self.suite_created.emit(r.suite_dir)

        if failed:
            msg = "\n".join(
                f"· {r.source.name}: {r.message}" for r in self._results if r.status == "failed"
            )
            self.log_dock.add_note("error", f"{failed} 个文件失败：\n{msg}")
        if ok and not failed:
            self.log_dock.add_note("info", f"全部完成，共生成/更新 {ok} 个套件。")

    # ------------------------------------------------------------------ 结果操作
    def _clear_results(self) -> None:
        self._results.clear()
        self.tbl_results.setRowCount(0)
        self.progress.setValue(0)

    def _selected_result(self) -> GenerateResult | None:
        rows = self.tbl_results.selectionModel().selectedRows()
        if not rows:
            return None
        r = rows[0].row()
        return self._results[r] if 0 <= r < len(self._results) else None

    def _on_result_double_clicked(self, *_args) -> None:
        self._open_suite_dir()

    def _open_suite_dir(self) -> None:
        res = self._selected_result()
        target = res.suite_dir if res and res.suite_dir else Path(self.ed_target.text())
        if not target or not Path(target).is_dir():
            QMessageBox.information(self, "没有可打开的目录", "尚无可打开的套件目录。")
            return
        import os  # noqa: PLC0415
        import subprocess  # noqa: PLC0415
        import sys  # noqa: PLC0415

        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as e:
            self.log_dock.add_note("error", f"无法打开目录：{e}")


def _check(text: str, checked: bool) -> QCheckBox:
    c = QCheckBox(text)
    c.setChecked(checked)
    return c


def _verovio_version() -> str:
    try:
        from ..core import verovio_version  # noqa: PLC0415

        return verovio_version()
    except Exception:  # noqa: BLE001
        return "?"
