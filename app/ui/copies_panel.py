"""Active copies panel — live table of all copy records."""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QAbstractItemView,
)
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtCore import Qt

from app.models import CopyRecord, MTInstance

_COLS = ["Copy ID", "Source", "Src Ticket", "Symbol", "Dir",
         "Src Lots", "Dest", "Dest Ticket", "Dest Lots", "Status", "Time"]
_COL = {name: i for i, name in enumerate(_COLS)}

# Row background colours (dark-theme friendly)
_BG: dict[str, QColor] = {
    "pending":       QColor(100, 75,  0),
    "open":          QColor(0,   80, 30),
    "pending_close": QColor(100, 65,  0),
    "closed":        QColor(55,  55, 55),
    "error":         QColor(100, 20, 20),
}


class CopiesPanel(QWidget):
    """Displays active and historical copy records."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._show_closed = False
        # copy_ids permanently hidden by the user ("Clear closed" button)
        self._permanently_cleared: set[str] = set()
        self._copies_cache: dict[str, CopyRecord] = {}
        self._instances_cache: dict[str, MTInstance] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Toolbar ───────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        lbl = QLabel("Active Copies")
        lbl.setStyleSheet("font-weight: bold;")
        toolbar.addWidget(lbl)
        toolbar.addStretch()

        self._btn_show_closed = QPushButton("Show closed")
        self._btn_show_closed.setCheckable(True)
        self._btn_show_closed.setFixedWidth(90)
        self._btn_show_closed.toggled.connect(self._on_toggle_closed)
        toolbar.addWidget(self._btn_show_closed)

        btn_clear = QPushButton("Clear closed")
        btn_clear.setFixedWidth(90)
        btn_clear.clicked.connect(self._on_clear_closed)
        toolbar.addWidget(btn_clear)

        layout.addLayout(toolbar)

        # ── Table ─────────────────────────────────────────────────────────
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            _COL["Source"], QHeaderView.Stretch
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

    # ── Public API ────────────────────────────────────────────────────────

    def refresh(
        self,
        active_copies: dict[str, CopyRecord],
        instances: dict[str, MTInstance],
    ) -> None:
        # Exclude permanently-cleared records from the local cache so they
        # don't reappear after the user clicks "Clear closed".
        self._copies_cache = {
            cid: rec for cid, rec in active_copies.items()
            if cid not in self._permanently_cleared
        }
        self._instances_cache = instances
        self._repopulate()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _repopulate(self) -> None:
        records = [
            rec for rec in self._copies_cache.values()
            if self._show_closed or rec.status != "closed"
        ]
        # Active records first, then closed; within each group newest first.
        records.sort(key=lambda r: (r.status == "closed", -r.opened_at.timestamp()))

        self._table.setRowCount(len(records))
        for row, rec in enumerate(records):
            self._set_row(row, rec)

    def _set_row(self, row: int, rec: CopyRecord) -> None:
        src_name = self._display(rec.source_terminal_path)
        dst_name = self._display(rec.dest_terminal_path)
        time_str = (rec.closed_at or rec.opened_at).strftime("%H:%M:%S")

        values = [
            rec.copy_id[:8],
            src_name,
            rec.source_ticket,
            rec.symbol_source,
            rec.direction.upper(),
            str(rec.source_lots),
            dst_name,
            rec.dest_ticket or "—",
            str(rec.dest_lots),
            rec.status,
            time_str,
        ]
        bg = _BG.get(rec.status, QColor(55, 55, 55))
        for col, val in enumerate(values):
            item = QTableWidgetItem(val)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            item.setBackground(QBrush(bg))
            self._table.setItem(row, col, item)

    def _display(self, terminal_path: str) -> str:
        inst = self._instances_cache.get(terminal_path)
        if inst:
            return inst.display_name
        return Path(terminal_path).name if terminal_path else "—"

    def _on_toggle_closed(self, checked: bool) -> None:
        self._show_closed = checked
        self._repopulate()

    def _on_clear_closed(self) -> None:
        newly_cleared = {
            cid for cid, rec in self._copies_cache.items()
            if rec.status == "closed"
        }
        self._permanently_cleared.update(newly_cleared)
        for cid in newly_cleared:
            self._copies_cache.pop(cid, None)
        self._repopulate()

