"""F1.8：多乐器音色映射表（需求 §5.2 F1.8 / §7.4）。

职责：把 MusicXML ``part-list`` 里的声部与 MIDI 通道 / GM 音色对应起来，
**让用户确认或修改**，绝不静默猜测。

列：声部 ID | 声部名 | MIDI 通道 | GM 音色（可下拉） | 来源 | 状态
其中"来源"列明确区分：
  * ``文件内``  —— MusicXML 里写了 ``midi-program``（可信）
  * ``按名猜测`` —— 没写，按乐器名模糊匹配出的建议值（**必须人工确认**）
  * ``未指定``  —— 没写也猜不出（回退钢琴）
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..common.log import get_logger
from ..core.gm import PROGRAM_CHOICES, guess_program_from_name
from ..core.musicxml_io import MusicXMLDocument

log = get_logger(__name__)

_SOURCE_FILE = "文件内"
_SOURCE_GUESS = "按名猜测"
_SOURCE_NONE = "未指定"
#: 「已修改」的蓝色。表格是**浅色**底，蓝色必须够深才看得清
#: （原来用的 #9cdcfe 是深色主题的浅蓝，亮度 0.78，在白底上几乎看不见）。
_MODIFIED_BLUE = "#1565c0"


@dataclass
class PartRow:
    """一行声部的可编辑状态。"""

    part_id: str
    name: str
    channel: int
    program: int
    source: str
    instrument: str = ""
    original_source: str = ""
    """源文件给出的"来源"标签，用于在用户改回原值时准确还原。"""

    def __post_init__(self) -> None:
        if not self.original_source:
            self.original_source = self.source

    @property
    def touch_source(self) -> str:
        """用户改过音色后的「来源」标签。"""
        return self.original_source if self.program == self.original_program else "已修改"

    @property
    def original_program(self) -> int:
        """初始音色。guess/source 是加载时定下的，这里由 :meth:`mark_original` 记录。"""
        return getattr(self, "_original_program", self.program)

    def mark_original(self) -> None:
        self._original_program = self.program


class PartsTable(QWidget):
    """声部 → 通道 → GM 音色 的映射表。"""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[PartRow] = []
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        head = QHBoxLayout()
        self.title = QLabel("音色映射（声部 → MIDI 通道 / GM 音色）")
        self.title.setStyleSheet("font-weight:600;")
        head.addWidget(self.title)
        head.addStretch(1)
        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#e6b422;")
        head.addWidget(self.hint)
        layout.addLayout(head)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["声部 ID", "声部名", "MIDI 通道", "GM 音色", "来源", "状态"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        btns = QHBoxLayout()
        self.btn_reset = QPushButton("恢复源文件默认值")
        self.btn_reset.clicked.connect(lambda: self.changed.emit())
        btns.addWidget(self.btn_reset)
        btns.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#888;")
        btns.addWidget(self.count_label)
        layout.addLayout(btns)

        self.btn_reset.setEnabled(False)  # 由 tab 决定是否可用

    # ------------------------------------------------------------------ 填充
    def load_document(self, doc: MusicXMLDocument | None) -> None:
        """按 MusicXML 文档填充表格。``None`` 表示清空。"""
        self._loading = True
        try:
            self._rows.clear()
            self.table.setRowCount(0)
            if doc is None:
                self._update_summary()
                return

            for p in doc.parts:
                channel = p.midi_channel or 1
                if p.program_was_explicit:
                    program = p.midi_program if p.midi_program is not None else 0
                    source = _SOURCE_FILE
                else:
                    guessed = guess_program_from_name(p.name or p.instrument_name)
                    if guessed is None:
                        program, source = 0, _SOURCE_NONE
                    else:
                        program, source = guessed, _SOURCE_GUESS
                self._rows.append(
                    PartRow(
                        part_id=p.id,
                        name=p.name or p.id,
                        channel=channel,
                        program=program,
                        source=source,
                        instrument=p.instrument_name,
                    )
                )
                self._rows[-1].mark_original()
            self._rebuild()
        finally:
            self._loading = False
            self._update_summary()

    def _rebuild(self) -> None:
        self.table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            self.table.setItem(r, 0, _ro(row.part_id))
            self.table.setItem(r, 1, _ro(row.name))

            ch = QComboBox()
            for i in range(1, 17):
                label = f"{i}" + ("（打击乐）" if i == 10 else "")
                ch.addItem(label, i)
            ch.setCurrentIndex(max(0, min(15, row.channel - 1)))
            ch.currentIndexChanged.connect(lambda _i, rr=r: self._on_channel(rr))
            self.table.setCellWidget(r, 2, ch)

            prog = QComboBox()
            # 128 个 GM 音色；用可搜索的方式太长，这里直接列出
            for number, name in PROGRAM_CHOICES:
                prog.addItem(f"{number:03d} {name}", number)
            prog.setCurrentIndex(max(0, min(len(PROGRAM_CHOICES) - 1, row.program)))
            prog.currentIndexChanged.connect(lambda _i, rr=r: self._on_program(rr))
            self.table.setCellWidget(r, 3, prog)

            self.table.setItem(r, 4, _ro(row.source, color=_source_color(row.source)))
            self.table.setItem(r, 5, _ro(self._status_text(row), color=self._status_color(row)))

    # ------------------------------------------------------------------ 交互
    def _on_channel(self, r: int) -> None:
        w = self.table.cellWidget(r, 2)
        if isinstance(w, QComboBox):
            self._rows[r].channel = int(w.currentData())
        self._touch()

    def _on_program(self, r: int) -> None:
        w = self.table.cellWidget(r, 3)
        if isinstance(w, QComboBox):
            self._rows[r].program = int(w.currentData())
        self._refresh_row(r)
        self._touch()

    def _refresh_row(self, r: int) -> None:
        """刷新依赖行的派生列（来源 / 状态）。"""
        row = self._rows[r]
        row.source = row.touch_source
        self.table.setItem(r, 4, _ro(row.source, color=_source_color(row.source)))
        self.table.setItem(r, 5, _ro(self._status_text(row), color=self._status_color(row)))

    def _touch(self) -> None:
        if not self._loading:
            self.changed.emit()
            self._update_summary()

    # ------------------------------------------------------------------ 输出
    def overrides(self) -> dict[str, dict[str, int]]:
        """返回给流水线的覆盖表 ``{part_id: {"midi_channel", "midi_program"}}``。"""
        return {
            row.part_id: {"midi_channel": row.channel, "midi_program": row.program}
            for row in self._rows
        }

    def channel_conflicts(self) -> list[str]:
        """检测通道冲突（同通道不同音色 → 后面会覆盖前面）。"""
        seen: dict[int, set[int]] = {}
        for row in self._rows:
            seen.setdefault(row.channel, set()).add(row.program)
        problems: list[str] = []
        for ch, progs in sorted(seen.items()):
            if len(progs) > 1:
                problems.append(f"通道 {ch} 上指定了 {len(progs)} 种不同音色，只有最后一种会生效")
        return problems

    def set_reset_enabled(self, enabled: bool) -> None:
        self.btn_reset.setEnabled(enabled)

    def _status_text(self, row: PartRow) -> str:
        if row.source == "已修改":
            return "已修改"
        if row.original_source == _SOURCE_FILE:
            return "来自文件"
        if row.original_source == _SOURCE_GUESS:
            return "请确认"
        return "回退钢琴"

    def _status_color(self, row: PartRow) -> str:
        if row.source == "已修改":
            return _MODIFIED_BLUE
        if row.original_source == _SOURCE_FILE:
            return "#4ec94e"
        if row.original_source == _SOURCE_GUESS:
            return "#e6b422"
        return "#e05252"

    def _update_summary(self) -> None:
        if not self._rows:
            self.count_label.setText("（未载入乐谱）")
            self.hint.setText("")
            return
        self.count_label.setText(f"共 {len(self._rows)} 个声部")
        need = [
            r.part_id
            for r in self._rows
            if r.source in (_SOURCE_GUESS, _SOURCE_NONE)
        ]
        if need:
            self.hint.setText(f"⚠ {len(need)} 个声部需要确认音色")
        else:
            conflicts = self.channel_conflicts()
            self.hint.setText(f"⚠ {conflicts[0]}" if conflicts else "")


def _source_color(source: str) -> str:
    """「来源」列的配色：文件内=可信（绿）、猜测=待确认（黄）、已修改=蓝、未指定=红。"""
    if source == _SOURCE_FILE:
        return "#4ec94e"
    if source == _SOURCE_GUESS:
        return "#e6b422"
    if source == "已修改":
        return _MODIFIED_BLUE
    return "#e05252"


def _ro(text: str, *, color: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    if color:
        from PySide6.QtGui import QColor  # noqa: PLC0415

        item.setForeground(QColor(color))
    return item
