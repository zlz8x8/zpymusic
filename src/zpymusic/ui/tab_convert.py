"""界面三：格式转换（需求 §5.4 F3.1–F3.8）—— **M5 实现**。

页面自上而下是"选文件 → 配参数 → 看依赖 → 跑批量 → 拿产物"这条链路：

| 区域 | 需求 | 说明 |
| :--- | :--- | :--- |
| 源文件行 | F3.1 / F3.8 | 输入框 + 「预览」（在「选择…」左侧，见 §12.9）+ 「选择…」；选中的文件同时进入批量列表 |
| 批量列表 | F3.4 | 每个文件一行：状态（等待/进行/成功/跳过/失败/取消）+ 产物绝对路径 + 备注；单项失败不影响其它项 |
| 转换设置 | F3.1 / F3.7 | 目标格式（按源格式过滤）、目标目录、命名模板（`{base}/{format}/{ext}/{date}`）、重名策略 |
| 依赖自检 | F3.2 | 逐个列出该转换需要的引擎（verovio / FluidSynth / ffmpeg / MuseScore / music21 / pypdf / 音色库），缺任一就禁止开始并给安装提示 + 「打开设置」 |
| 保真度提示 | F3.3 | 对 `musicxml → midi/mp3`、`midi → musicxml`、`svg → musicxml` 弹一次性提示（可勾选"不再提示"，写进配置） |
| 结果区 | F3.6 | 产物绝对路径 + 「打开所在文件夹」+「载入播放界面」（产物是带音频的套件时才可用） |
| 能力矩阵 | §5.4.1 | 只读表格，说明每条转换的优先级与实现方式 |

转换本身由 :mod:`zpymusic.core.convert` 完成，**与 CLI 共用同一份实现**；
本页只负责界面状态与批量调度（:class:`~zpymusic.ui.worker.BatchRunner`，QThread + 协作式取消）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..common.config import AppConfig
from ..common.log import get_logger
from ..core.convert import (
    CONVERSIONS,
    NAME_TEMPLATE_TOKENS,
    ConvertError,
    ConvertOptions,
    check_environment,
    convert_one,
    detect_format,
    fidelity_note,
    is_lossy,
    iter_convertible,
    missing_requirements,
    plan_output,
    requirements_for,
)
from ..core.suite import OverwritePolicy
from .open_utils import open_path, reveal_in_folder
from .preview import AudioPreviewWindow, ScorePreviewWindow, preview_kind
from .worker import BatchRunner, TaskOutcome

log = get_logger(__name__)

_SOURCE_LABEL = {
    "musicxml": "MusicXML (.musicxml/.xml/.mxl)",
    "midi": "MIDI (.mid/.midi)",
    "svg": "SVG (.svg)",
    "pdf": "PDF (.pdf)",
    "mp3": "MP3 (.mp3)",
    "wav": "WAV (.wav)",
}
_TARGETS = ["svg", "midi", "mp3", "wav", "pdf", "musicxml"]

_STATUS_TEXT = {
    "pending": "等待",
    "running": "进行中…",
    "ok": "成功",
    "skipped": "跳过",
    "failed": "失败",
    "cancelled": "已取消",
}
_STATUS_COLOR = {
    "pending": "#888",
    "running": "#1565c0",
    "ok": "#2e7d32",
    "skipped": "#e6b422",
    "failed": "#e05252",
    "cancelled": "#888",
}

_OVERWRITE_LABEL = [
    ("跳过已存在（skip）", OverwritePolicy.SKIP),
    ("覆盖（overwrite）", OverwritePolicy.OVERWRITE),
    ("加序号（newdir）", OverwritePolicy.NEWDIR),
]


@dataclass
class _Job:
    """批量里的一个转换项（源文件 + 该次转换的参数）。"""

    source: Path
    options: ConvertOptions
    row: int


class ConvertTab(QWidget):
    """「格式转换」标签页（M5）。"""

    #: 转换产出了可播放的套件（主窗口据此切到「同步播放」页并载入）
    suite_ready = Signal(object)
    #: 用户点了「打开设置」（主窗口弹设置对话框 —— 依赖缺失时的安装/配置入口）
    settings_requested = Signal()

    def __init__(self, config: AppConfig, log_dock, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.log_dock = log_dock
        # 预览窗口（非模态）按类型各复用一个实例，延迟创建
        self._score_preview: ScorePreviewWindow | None = None
        self._audio_preview: AudioPreviewWindow | None = None
        # 批量状态
        self._runner: BatchRunner | None = None
        self._env: dict | None = None
        self._results: dict[int, object] = {}  # row → ConvertResult
        self._jobs: list[_Job] = []
        self._skipped_pairs: set[tuple[str, str]] = {
            tuple(k.split("→", 1)) for k in getattr(config.ui, "skip_fidelity_prompts", []) if "→" in k
        }

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addWidget(self._build_source_group())
        root.addWidget(self._build_batch_group(), 1)
        root.addWidget(self._build_settings_group())
        root.addWidget(self._build_matrix_group())

        self._refresh_targets()

    # ================================================================== 构建界面
    def _build_source_group(self) -> QGroupBox:
        gb = QGroupBox("源文件（支持批量：选择后自动加入下方列表）")
        gv = QVBoxLayout(gb)

        row = QHBoxLayout()
        self.ed_source = QLineEdit()
        self.ed_source.setPlaceholderText("源文件…")
        self.ed_source.textChanged.connect(self._refresh_targets)
        row.addWidget(QLabel("源文件"))
        row.addWidget(self.ed_source, 1)
        # 「预览」必须在「选择…」左边（F3.8 的界面要求）
        self.btn_preview = QPushButton("预览")
        self.btn_preview.clicked.connect(self._preview_source)
        row.addWidget(self.btn_preview)
        btn = QPushButton("选择…")
        btn.clicked.connect(self._pick_source)
        row.addWidget(btn)
        gv.addLayout(row)
        return gb

    def _build_batch_group(self) -> QGroupBox:
        gb = QGroupBox("批量转换")
        gv = QVBoxLayout(gb)

        row = QHBoxLayout()
        btn_add_dir = QPushButton("添加目录…")
        btn_add_dir.setToolTip("把目录下所有受支持的源文件加入列表")
        btn_add_dir.clicked.connect(self._add_directory)
        row.addWidget(btn_add_dir)
        btn_remove = QPushButton("移除选中")
        btn_remove.clicked.connect(self._remove_selected)
        row.addWidget(btn_remove)
        btn_clear = QPushButton("清空列表")
        btn_clear.clicked.connect(self.clear_list)
        row.addWidget(btn_clear)
        row.addStretch(1)

        self.btn_convert = QPushButton("开始转换")
        self.btn_convert.setMinimumWidth(110)
        self.btn_convert.clicked.connect(self._start)
        row.addWidget(self.btn_convert)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        row.addWidget(self.btn_cancel)
        gv.addLayout(row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["源文件", "状态", "产物", "备注"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self._refresh_result_buttons)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        gv.addWidget(self.table, 1)

        # --- 结果区（F3.6）---
        row = QHBoxLayout()
        self.btn_reveal = QPushButton("打开所在文件夹")
        self.btn_reveal.clicked.connect(self._reveal_selected)
        row.addWidget(self.btn_reveal)
        self.btn_open = QPushButton("打开产物")
        self.btn_open.clicked.connect(self._open_selected)
        row.addWidget(self.btn_open)
        self.btn_load_play = QPushButton("载入播放界面")
        self.btn_load_play.setToolTip("把产物里的套件载入「同步播放」页（需要有音频产物）")
        self.btn_load_play.clicked.connect(self._load_selected_into_player)
        row.addWidget(self.btn_load_play)
        row.addStretch(1)
        row.addWidget(QLabel("已选格式："))
        self.lbl_selected_format = QLabel("—")
        row.addWidget(self.lbl_selected_format)
        gv.addLayout(row)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        gv.addWidget(self.bar)

        self.lbl_summary = QLabel("把源文件加入列表后点「开始转换」。")
        self.lbl_summary.setWordWrap(True)
        gv.addWidget(self.lbl_summary)
        return gb

    def _build_settings_group(self) -> QGroupBox:
        gb = QGroupBox("转换设置")
        gv = QVBoxLayout(gb)

        row = QHBoxLayout()
        row.addWidget(QLabel("目标格式"))
        self.cmb_target = QComboBox()
        self.cmb_target.addItems(_TARGETS)
        self.cmb_target.currentIndexChanged.connect(self._on_settings_changed)
        row.addWidget(self.cmb_target)
        row.addWidget(QLabel("重名时"))
        self.cmb_overwrite = QComboBox()
        for label, value in _OVERWRITE_LABEL:
            self.cmb_overwrite.addItem(label, value)
        self.cmb_overwrite.currentIndexChanged.connect(self._on_settings_changed)
        row.addWidget(self.cmb_overwrite)
        row.addStretch(1)
        gv.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("目标目录"))
        self.ed_out = QLineEdit(str(self.config.suites_path()))
        self.ed_out.textChanged.connect(self._on_settings_changed)
        row.addWidget(self.ed_out, 1)
        btn_out = QPushButton("选择…")
        btn_out.clicked.connect(self._pick_out)
        row.addWidget(btn_out)
        gv.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("命名模板"))
        self.ed_template = QLineEdit(str(getattr(self.config.ui, "convert_name_template", "{base}")))
        self.ed_template.setToolTip(
            "可用占位符：" + "、".join(NAME_TEMPLATE_TOKENS)
            + "\n例如 {base}_{format} 会得到 canon_mp3.mp3"
        )
        self.ed_template.textChanged.connect(self._on_settings_changed)
        row.addWidget(self.ed_template, 1)
        self.chk_quantize = QCheckBox("MIDI → MusicXML 量化（对齐记谱网格）")
        self.chk_quantize.setChecked(True)
        self.chk_quantize.setToolTip(
            "关闭后保留 MIDI 的原始演奏时值（更接近演奏，但记谱会很难看）"
        )
        row.addWidget(self.chk_quantize)
        gv.addLayout(row)

        self.lbl_out_preview = QLabel("")
        self.lbl_out_preview.setStyleSheet("color:#666;")
        self.lbl_out_preview.setWordWrap(True)
        gv.addWidget(self.lbl_out_preview)

        # --- 依赖自检（F3.2）---
        row = QHBoxLayout()
        row.addWidget(QLabel("依赖自检"))
        btn_recheck = QPushButton("重新检测")
        btn_recheck.setToolTip("设置里改过外部工具路径后点它重新探测")
        btn_recheck.clicked.connect(self._on_recheck)
        row.addWidget(btn_recheck)
        btn_settings = QPushButton("打开设置")
        btn_settings.setToolTip("配置 ffmpeg / FluidSynth / MuseScore / 音色库路径")
        btn_settings.clicked.connect(self.settings_requested.emit)
        row.addWidget(btn_settings)
        row.addStretch(1)
        gv.addLayout(row)

        self.lbl_deps = QLabel("")
        self.lbl_deps.setWordWrap(True)
        gv.addWidget(self.lbl_deps)

        self.lbl_hint = QLabel("")
        self.lbl_hint.setWordWrap(True)
        gv.addWidget(self.lbl_hint)
        return gb

    def _build_matrix_group(self) -> QGroupBox:
        gb = QGroupBox("格式转换能力矩阵（需求 §5.4.1）")
        gv = QVBoxLayout(gb)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["转换", "优先级", "实现方式", "依赖 / 备注"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        _fill_matrix(table)
        gv.addWidget(table)
        gv.addWidget(
            _muted(
                "优先级：P0/P1 本期实现；P2 视情况（缺少依赖时会明确拒绝并给出安装提示）；"
                "P3 本期不做（界面上置灰并说明原因）。"
            )
        )
        return gb

    # ================================================================== 列表维护
    @property
    def sources(self) -> list[Path]:
        """当前批量列表里的源文件（按行序）。"""
        out: list[Path] = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None:
                out.append(Path(item.data(Qt.ItemDataRole.UserRole) or item.text()))
        return out

    def add_source(self, path: Path | str, *, select: bool = True) -> bool:
        """把一个文件加入列表（去重）；返回是否新增。"""
        p = Path(path)
        if not p.is_file() or not detect_format(p):
            self._note("warning", f"跳过不支持的文件：{p}")
            return False
        key = str(p.resolve())
        if any(str(s.resolve()) == key for s in self.sources):
            return False
        row = self.table.rowCount()
        self.table.insertRow(row)
        item = QTableWidgetItem(p.name)
        item.setData(Qt.ItemDataRole.UserRole, str(p))
        item.setToolTip(str(p))
        self.table.setItem(row, 0, item)
        self._set_status(row, "pending")
        self.table.setItem(row, 2, QTableWidgetItem(""))
        self.table.setItem(row, 3, QTableWidgetItem(""))
        if select:
            self.table.selectRow(row)
        self._on_list_changed()
        return True

    def clear_list(self) -> None:
        self.table.setRowCount(0)
        self._results.clear()
        self._on_list_changed()

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
            self._results.pop(r, None)
        self._renumber_results()
        self._on_list_changed()

    def _renumber_results(self) -> None:
        """删行之后把"行号 → 结果"的映射重建（行号会变）。"""
        if not self._results:
            return
        keep: dict[int, object] = {}
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is None:
                continue
            src = item.data(Qt.ItemDataRole.UserRole) or item.text()
            for old_row, res in self._results.items():
                if str(getattr(res, "source", "")) == str(src):
                    keep[r] = res
                    break
        self._results = keep

    def _set_status(self, row: int, status: str) -> None:
        item = QTableWidgetItem(_STATUS_TEXT.get(status, status))
        item.setForeground(_color(_STATUS_COLOR.get(status, "#888")))
        self.table.setItem(row, 1, item)

    def _on_list_changed(self) -> None:
        self._refresh_targets()
        self._refresh_result_buttons()
        n = self.table.rowCount()
        self.lbl_summary.setText(
            f"列表里有 {n} 个文件。" if n else "把源文件加入列表后点「开始转换」。"
        )

    # ================================================================== 参数 / 依赖
    def _target_format(self) -> str:
        return self.cmb_target.currentText()

    def _overwrite(self) -> str:
        return str(self.cmb_overwrite.currentData() or OverwritePolicy.SKIP)

    def _source_format(self) -> str:
        text = self.ed_source.text().strip()
        return detect_format(Path(text)) if text else ""

    def _options(self, source: Path) -> ConvertOptions:
        text = self.ed_out.text().strip()
        return ConvertOptions(
            target=self._target_format(),
            out_dir=Path(text) if text else None,
            name_template=self.ed_template.text().strip() or "{base}",
            overwrite=self._overwrite(),
            quantize=self.chk_quantize.isChecked(),
        )

    def _supported_targets(self, sfmt: str) -> set[str]:
        """该源格式可用的目标（P3 与未实现的组合排除）。"""
        return {
            t
            for (s, t), (prio, _how) in CONVERSIONS.items()
            if s == sfmt and prio != "P3"
        }

    def _allowed_targets(self) -> set[str]:
        """当前列表（或当前源文件）允许的目标格式集合：多格式时取**交集**。

        交集为空（例如同时选了 musicxml / midi / mp3 / svg 四种）时**放开全部目标** ——
        否则用户会卡在"一个目标都点不动"的死角。此时依赖自检面板会逐个列出
        "哪个文件不支持当前目标"，用户删掉那几个文件或分批转换即可。
        """
        fmts = {detect_format(p) for p in self.sources}
        if not fmts:
            single = self._source_format()
            fmts = {single} if single else set()
        if not fmts:
            return set(_TARGETS)
        allowed = set(_TARGETS)
        for f in fmts:
            allowed &= self._supported_targets(f)
        return allowed or set(_TARGETS)

    def _common_targets_empty(self) -> bool:
        """列表里是否"没有共同的目标格式"（用于在依赖面板里给出提示）。"""
        fmts = {detect_format(p) for p in self.sources}
        if len(fmts) < 2:
            return False
        allowed = set(_TARGETS)
        for f in fmts:
            allowed &= self._supported_targets(f)
        return not allowed

    def _ensure_target_allowed(self) -> None:
        """当前选中的目标对该列表不适用时，自动切到第一个可用目标。"""
        allowed = self._allowed_targets()
        if not allowed or self.cmb_target.currentText() in allowed:
            return
        for i, t in enumerate(_TARGETS):
            if t in allowed:
                self.cmb_target.setCurrentIndex(i)  # 触发 currentIndexChanged
                return

    def _refresh_targets(self) -> None:
        """按源格式（或整个列表的交集）过滤可选目标（F3.1：置灰 + 原因）。"""
        self._refresh_preview_button()

        allowed = self._allowed_targets()
        model = self.cmb_target.model()
        first_ok = -1
        for i, t in enumerate(_TARGETS):
            ok = t in allowed
            item = model.item(i) if hasattr(model, "item") else None
            if item is not None:
                item.setEnabled(ok)
            if ok and first_ok < 0:
                first_ok = i
        if first_ok >= 0 and self.cmb_target.currentText() not in allowed:
            self.cmb_target.setCurrentIndex(first_ok)
        self._refresh_dependencies()
        self._refresh_output_preview()
        self._refresh_hint()

    def _env_now(self) -> dict:
        if self._env is None:
            self._env = check_environment(self.config)
        return self._env

    def refresh_environment(self) -> None:
        """设置对话框改过路径后调用：重新探测外部工具。"""
        self._env = None
        self._refresh_dependencies()

    def _pending_jobs(self) -> list[_Job]:
        if not self._target_format():
            return []
        return [
            _Job(source=p, options=self._options(p), row=r)
            for r, p in enumerate(self.sources)
        ]

    def _refresh_dependencies(self) -> None:
        """依赖自检（F3.2）：列出生效的引擎，缺任一就禁止开始。"""
        jobs = self._pending_jobs()
        self.btn_convert.setEnabled(bool(jobs) and self._runner is None)
        if not jobs:
            self.lbl_deps.setText(
                "<span style='color:#888'>依赖自检：先选择源文件与目标格式。</span>"
            )
            return

        env = self._env_now()
        missing_cache: dict[tuple[str, str], list] = {}
        blocked: list[tuple[Path, str]] = []
        ok_pairs: list[tuple[str, str]] = []
        for job in jobs:
            key = (detect_format(job.source), job.options.target)
            entry = CONVERSIONS.get(key)
            if entry is None:
                blocked.append((job.source, f"不支持 {key[0] or '?'} → {key[1]}"))
                continue
            if entry[0] == "P3":
                blocked.append((job.source, f"{key[0]} → {key[1]} 本期不做（P3）：{entry[1]}"))
                continue
            if key not in missing_cache:
                missing_cache[key] = missing_requirements(key[0], key[1], self.config, env)
                if not missing_cache[key]:
                    ok_pairs.append(key)
            if missing_cache[key]:
                blocked.append(
                    (
                        job.source,
                        "；".join(f"{r.name}（{r.hint}）" for r in missing_cache[key]),
                    )
                )

        lines: list[str] = []
        if ok_pairs:
            names: list[str] = []
            for key in ok_pairs:
                for req in requirements_for(key[0], key[1], env, self.config):
                    if req.ok and req.name not in names:
                        names.append(req.name)
            pairs_text = "、".join(
                f"{_SOURCE_LABEL.get(s, s)} → {t}" for s, t in dict.fromkeys(ok_pairs)
            )
            lines.append(
                f"<span style='color:{_STATUS_COLOR['ok']}'>✓ 依赖齐备</span>"
                f"（{pairs_text}；需要：{'、'.join(names) or '无外部依赖'}）"
            )
        for src, reason in blocked[:4]:
            lines.append(
                f"<span style='color:{_STATUS_COLOR['failed']}'>✗ {src.name}：</span>{reason}"
            )
        if len(blocked) > 4:
            lines.append(f"…还有 {len(blocked) - 4} 个文件不能转换")
        if blocked and self._common_targets_empty():
            lines.append(
                "提示：列表里的源格式没有共同的目标格式，请分批转换，或移除上面标 ✗ 的文件。"
            )
        if blocked:
            lines.append("安装好之后点「重新检测」，或在「打开设置」里手工指定可执行文件路径。")
        self.lbl_deps.setText("<br>".join(lines))
        self.btn_convert.setEnabled(bool(jobs) and not blocked and self._runner is None)

    def _refresh_output_preview(self) -> None:
        """用当前命名模板 + 目标格式，展示第一个列表项的目标路径（F3.7 的即时反馈）。"""
        srcs = self.sources
        if not srcs:
            self.lbl_out_preview.setText("目标路径示例：（把源文件加入列表后显示）")
            return
        try:
            path, skip = plan_output(srcs[0], self._options(srcs[0]))
        except ConvertError as e:
            self.lbl_out_preview.setText(
                f"<span style='color:{_STATUS_COLOR['failed']}'>命名模板有误：{e}</span>"
            )
            return
        hint = "（套件目录）" if path.suffix == "" else ""
        exists = "；该路径已存在 → 按「重名时」策略处理" if skip else ""
        self.lbl_out_preview.setText(f"目标路径示例：{path}{hint}{exists}")

    def _refresh_hint(self) -> None:
        sfmt = self._source_format()
        tfmt = self._target_format()
        if not sfmt:
            self.lbl_hint.setText("请先选择源文件。")
            return
        entry = CONVERSIONS.get((sfmt, tfmt))
        if entry is None:
            self.lbl_hint.setText(f"<span style='color:{_STATUS_COLOR['failed']}'>不支持：{sfmt} → {tfmt}</span>")
            return
        prio, how = entry
        if prio == "P3":
            self.lbl_hint.setText(
                f"<span style='color:{_STATUS_COLOR['failed']}'>优先级 P3，本期不做。</span> {how}"
            )
            return
        color = {"P0": "#2e7d32", "P1": "#b58900", "P2": "#1565c0"}.get(prio, "#888")
        note = fidelity_note(sfmt, tfmt)
        extra = f" {note}" if note else ""
        self.lbl_hint.setText(f"<span style='color:{color}'>优先级 {prio}</span> —— {how}{extra}")

    def _on_settings_changed(self) -> None:
        self._ensure_target_allowed()
        self._refresh_dependencies()
        self._refresh_output_preview()
        self._refresh_hint()

    def _on_recheck(self) -> None:
        """重新探测外部工具（设置里改过路径之后）。"""
        self._env = None
        self._refresh_dependencies()
        env = self._env_now()
        bad = [r for r in env.values() if not r.ok]
        self._note(
            "info" if not bad else "warning",
            "依赖重新检测：" + ("全部可用" if not bad else "；".join(f"{r.name} 不可用" for r in bad)),
        )

    def _pick_source(self) -> None:
        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

        path, _ = QFileDialog.getOpenFileName(
            self, "选择源文件", self.config.musicxml_dir,
            "所有支持的格式 (*.musicxml *.xml *.mxl *.mid *.midi *.svg *.pdf *.mp3 *.wav);;所有文件 (*)",
        )
        if path:
            self.ed_source.setText(path)
            self.add_source(path)

    def _add_directory(self) -> None:
        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

        folder = QFileDialog.getExistingDirectory(self, "选择源目录", self.config.musicxml_dir)
        if not folder:
            return
        added = 0
        for p in iter_convertible(Path(folder)):
            added += 1 if self.add_source(p, select=False) else 0
        if not added:
            self._note("info", f"{folder} 里没有新的可转换文件")
        else:
            self._note("info", f"已加入 {added} 个文件")

    def _pick_out(self) -> None:
        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

        path = QFileDialog.getExistingDirectory(self, "选择目标目录", self.ed_out.text())
        if path:
            self.ed_out.setText(path)

    # ================================================================== 预览（F3.8）
    def _preview_source(self) -> None:
        """打开（或复用）非模态预览窗口显示 / 播放源文件。"""
        text = self.ed_source.text().strip()
        kind = preview_kind(text) if text else ""
        if not kind:
            return  # 按钮本就该是灰的，这里双保险

        path = Path(text)
        if kind == "score":
            if self._score_preview is None:
                self._score_preview = ScorePreviewWindow(self.config, self, self.log_dock)
            self._score_preview.open_file(path)
        else:
            if self._audio_preview is None:
                self._audio_preview = AudioPreviewWindow(self.config, self, self.log_dock)
            self._audio_preview.open_file(path)

    def _refresh_preview_button(self) -> None:
        """「预览」按钮的可用性 + 原因 tooltip（F3.8 / F3.1 的置灰 + 说明）。"""
        text = self.ed_source.text().strip()
        if not text:
            self.btn_preview.setEnabled(False)
            self.btn_preview.setToolTip("请先选择源文件")
            return
        kind = preview_kind(text)
        if not kind:
            suffix = Path(text).suffix.lower() or "该格式"
            self.btn_preview.setEnabled(False)
            self.btn_preview.setToolTip(
                f"{suffix} 暂不支持预览（仅 MusicXML / SVG 显示乐谱，MIDI / MP3 试听）"
            )
            return
        self.btn_preview.setEnabled(True)
        self.btn_preview.setToolTip(
            "显示乐谱（MusicXML / SVG）" if kind == "score" else "试听（MIDI / MP3）"
        )

    def shutdown_previews(self) -> None:
        """退出程序时收尾预览窗口：停播、等工作线程结束（避免 QThread 仍在运行时被销毁）。"""
        for win in (self._score_preview, self._audio_preview):
            if win is None:
                continue
            try:
                win.shutdown()
                win.close()
            except Exception as e:  # noqa: BLE001 - 退出路径上不该再抛
                log.warning("关闭预览窗口失败：%s", e)

    # ================================================================== 执行（F3.4 / F3.5）
    def _start(self) -> None:
        if self._runner is not None and self._runner.is_running:
            return
        jobs = self._pending_jobs()
        if not jobs:
            self._note("warning", "请先把源文件加入列表")
            return
        if not self._confirm_fidelity(jobs):
            return

        self.config.ui.convert_name_template = self.ed_template.text().strip() or "{base}"
        self.config.ui.skip_fidelity_prompts = sorted(f"{s}→{t}" for s, t in self._skipped_pairs)

        self._results.clear()
        self._env = self._env_now()  # 批量期间复用同一份探测结果
        self._jobs = jobs
        for job in jobs:
            self._set_status(job.row, "pending")
            self._set_cell(job.row, 2, "")
            self._set_cell(job.row, 3, "")

        self.bar.setRange(0, len(jobs))
        self.bar.setValue(0)
        self._set_running(True)
        self.lbl_summary.setText(f"开始转换 {len(jobs)} 个文件…")

        def work(index, job: _Job, report_progress, is_cancelled) -> TaskOutcome:  # noqa: ANN001
            result = convert_one(
                job.source,
                job.options,
                self.config,
                progress=lambda stage, done, total: report_progress(stage, done, total),
                cancelled=is_cancelled,
                env=self._env,
            )
            return TaskOutcome(
                key=str(job.source),
                ok=bool(result.ok),
                payload=result,
                error="" if result.ok else result.message,
                cancelled=result.status == "cancelled",
            )

        self._runner = BatchRunner(jobs, work, parent=self)
        self._runner.progress.connect(self._on_progress)
        self._runner.message.connect(lambda level, text: self._note(level, text))
        self._runner.item_done.connect(self._on_item_done)
        self._runner.finished.connect(self._on_finished)
        self._runner.start()

    def _cancel(self) -> None:
        if self._runner is not None and self._runner.is_running:
            self._runner.cancel()
            self.lbl_summary.setText("已请求取消，等待当前文件结束…")

    def _set_running(self, running: bool) -> None:
        self.btn_convert.setEnabled(not running)
        self.btn_cancel.setEnabled(running)
        for w in (self.cmb_target, self.cmb_overwrite, self.ed_out, self.ed_template):
            w.setEnabled(not running)

    def _on_progress(self, index: int, _inner: int, stage: str) -> None:
        """``BatchRunner.progress`` 的第 1 个参数是**当前项下标**（不是已完成数）。"""
        total = max(1, len(self._jobs))
        self.bar.setRange(0, total)
        self.bar.setValue(max(0, min(index, total)))
        if 0 <= index < len(self._jobs):
            self._set_status(self._jobs[index].row, "running")
        if stage:
            self.lbl_summary.setText(stage)

    def _on_item_done(self, outcome: TaskOutcome) -> None:
        row = self._row_of(outcome.key)
        if row is None:
            return
        result = outcome.payload
        status = getattr(result, "status", "failed" if not outcome.ok else "ok")
        self._results[row] = result
        self._set_status(row, status)
        outputs = [str(p) for p in getattr(result, "outputs", [])]
        self._set_cell(row, 2, "; ".join(outputs), tooltip="\n".join(outputs))
        notes = [getattr(result, "message", "") or ""] + list(getattr(result, "warnings", []))
        self._set_cell(row, 3, " / ".join(n for n in notes if n), tooltip="\n".join(n for n in notes if n))
        self._refresh_result_buttons()

    def _on_finished(self, outcomes: list) -> None:
        self._runner = None
        self._set_running(False)
        self.bar.setValue(self.bar.maximum())
        counts = {"ok": 0, "skipped": 0, "failed": 0, "cancelled": 0}
        for res in self._results.values():
            counts[getattr(res, "status", "failed")] = counts.get(getattr(res, "status", "failed"), 0) + 1
        self.lbl_summary.setText(
            f"完成：成功 {counts['ok']}，跳过 {counts['skipped']}，"
            f"失败 {counts['failed']}，取消 {counts['cancelled']}，共 {len(outcomes)}"
        )
        self._note(
            "info" if counts["failed"] == 0 else "warning",
            f"格式转换完成：成功 {counts['ok']}，跳过 {counts['skipped']}，"
            f"失败 {counts['failed']}，取消 {counts['cancelled']}",
        )
        self._refresh_dependencies()

    def _row_of(self, key: str) -> int | None:
        for r, src in enumerate(self.sources):
            if str(src) == key:
                return r
        return None

    def _set_cell(self, row: int, col: int, text: str, *, tooltip: str = "") -> None:
        item = QTableWidgetItem(text)
        if tooltip:
            item.setToolTip(tooltip)
        self.table.setItem(row, col, item)

    # ================================================================== 保真度提示（F3.3）
    def _confirm_fidelity(self, jobs: list[_Job]) -> bool:
        """对需要提示的组合弹一次对话框；勾了"不再提示"就写进配置。"""
        pairs = [
            (detect_format(j.source), j.options.target)
            for j in jobs
            if is_lossy(detect_format(j.source), j.options.target)
            and (detect_format(j.source), j.options.target) not in self._skipped_pairs
        ]
        if not pairs:
            return True
        sfmt, tfmt = pairs[0]
        box = QMessageBox(self)
        box.setWindowTitle("转换前提示（F3.3）")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"即将把 {sfmt} 转成 {tfmt}")
        box.setInformativeText(fidelity_note(sfmt, tfmt) + "\n\n确定要继续吗？")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        chk = QCheckBox("不再提示该类转换")
        box.setCheckBox(chk)
        if box.exec() != QMessageBox.StandardButton.Yes:
            self.lbl_summary.setText("已取消：用户在保真度提示里选择了「否」。")
            return False
        if chk.isChecked():
            for pair in pairs:
                self._skipped_pairs.add(pair)
        return True

    # ================================================================== 结果区（F3.6）
    def _selected_results(self) -> list[object]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self._results[r] for r in rows if r in self._results]

    def _refresh_result_buttons(self) -> None:
        results = self._selected_results()
        has_outputs = any(getattr(r, "outputs", None) for r in results)
        playable = any(getattr(r, "playable", False) for r in results)
        self.btn_reveal.setEnabled(bool(results))
        self.btn_open.setEnabled(has_outputs)
        self.btn_load_play.setEnabled(playable)
        self.btn_load_play.setToolTip(
            "把产物里的套件载入「同步播放」页"
            if playable
            else "该转换没有产出带音频的套件；需要可播放套件请用「生成套件」页或转成 mp3/wav"
        )
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            self.lbl_selected_format.setText("—")
        else:
            fmt = detect_format(self.sources[rows[0]]) if rows[0] < len(self.sources) else ""
            self.lbl_selected_format.setText(
                f"{_SOURCE_LABEL.get(fmt, fmt or '?')} → {self._target_format()}（{len(rows)} 项）"
            )

    def _reveal_selected(self) -> None:
        for res in self._selected_results():
            outputs = getattr(res, "outputs", [])
            target = Path(outputs[0]) if outputs else getattr(res, "source", None)
            if target is not None:
                reveal_in_folder(Path(target), self.log_dock)

    def _open_selected(self) -> None:
        for res in self._selected_results():
            for out in getattr(res, "outputs", [])[:3]:
                open_path(Path(out), self.log_dock)

    def _load_selected_into_player(self) -> None:
        for res in self._selected_results():
            suite_dir = getattr(res, "suite_dir", None)
            if suite_dir and getattr(res, "playable", False):
                self.suite_ready.emit(Path(suite_dir))
                return
        self._note("info", "选中的结果里没有可载入播放界面的套件")

    # ================================================================== 小工具
    def _note(self, level: str, text: str) -> None:
        add_note = getattr(self.log_dock, "add_note", None)
        if callable(add_note):
            add_note(level, text)
        if level in {"warning", "error"}:
            log.warning("%s", text)
        else:
            log.info("%s", text)


def _fill_matrix(table: QTableWidget) -> None:
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    rows = sorted(CONVERSIONS.items(), key=lambda kv: (order.get(kv[1][0], 9), kv[0]))
    table.setRowCount(len(rows))
    # 注意：这些颜色画在**浅色**表格底上，蓝色必须够深（原来的 #9cdcfe 几乎看不清）
    colors = {"P0": "#2e7d32", "P1": "#b58900", "P2": "#1565c0", "P3": "#888"}

    for r, ((s, t), (prio, how)) in enumerate(rows):
        table.setItem(r, 0, QTableWidgetItem(f"{s} → {t}"))
        p = QTableWidgetItem(prio)
        p.setForeground(_color(colors.get(prio, "#888")))
        table.setItem(r, 1, p)
        table.setItem(r, 2, QTableWidgetItem(how))
        note = "本期不做" if prio == "P3" else ("核心" if prio == "P0" else "")
        table.setItem(r, 3, QTableWidgetItem(note))


def _color(spec: str):  # noqa: ANN201
    from PySide6.QtGui import QColor  # noqa: PLC0415

    return QColor(spec)


def _muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color:#888;")
    lbl.setWordWrap(True)
    return lbl
