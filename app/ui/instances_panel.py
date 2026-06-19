"""Instances panel — table of connected (and remembered) MT4/MT5 terminals."""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMenu, QAction, QDialog, QDialogButtonBox,
    QAbstractItemView, QApplication,
)
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtCore import Qt

from app.models import MTInstance, OpenPosition

_GREEN = "#44ee44"
_GREY  = "#666666"

_COLS = ["●", "Directory", "Platform", "Broker", "Account", "Type",
         "Balance", "Equity", "Positions"]
_COL = {name: i for i, name in enumerate(_COLS)}


class InstancesPanel(QWidget):
    """Displays all known MT terminals with live account info."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        lbl = QLabel("Connected Instances")
        lbl.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.horizontalHeader().setSectionResizeMode(
            _COL["Directory"], QHeaderView.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL["●"], QHeaderView.ResizeToContents
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

        # Store terminal_path per row for context menu lookups
        self._row_paths: list[str] = []
        self._instances: dict[str, MTInstance] = {}
        self._source_positions: dict[str, dict[str, OpenPosition]] = {}

    # ── Public API ────────────────────────────────────────────────────────

    def refresh(
        self,
        instances: dict[str, MTInstance],
        source_positions: dict[str, dict[str, OpenPosition]],
    ) -> None:
        self._instances = instances
        self._source_positions = source_positions

        self._table.setRowCount(len(instances))
        self._row_paths = []

        for row, (path, inst) in enumerate(instances.items()):
            self._row_paths.append(path)
            self._set_row(row, inst, source_positions.get(path, {}))

        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setSectionResizeMode(
            _COL["Directory"], QHeaderView.Stretch
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    def _set_row(
        self,
        row: int,
        inst: MTInstance,
        positions: dict[str, OpenPosition],
    ) -> None:
        # Status dot
        dot = QTableWidgetItem("●")
        colour = _GREEN if inst.connected else _GREY
        dot.setForeground(QBrush(QColor(colour)))
        dot.setTextAlignment(Qt.AlignCenter)
        dot.setFlags(Qt.ItemIsEnabled)
        self._table.setItem(row, _COL["●"], dot)

        # Directory (folder name) with full path as tooltip
        dir_item = QTableWidgetItem(Path(inst.terminal_path).name)
        dir_item.setToolTip(inst.terminal_path)
        self._table.setItem(row, _COL["Directory"], dir_item)

        self._table.setItem(row, _COL["Platform"],  _item(inst.platform))
        self._table.setItem(row, _COL["Broker"],    _item(inst.broker))
        self._table.setItem(row, _COL["Account"],   _item(inst.account))
        self._table.setItem(row, _COL["Type"],      _item(inst.account_type.upper()))
        self._table.setItem(row, _COL["Balance"],   _item(f"${inst.balance:,.2f}"))
        self._table.setItem(row, _COL["Equity"],    _item(f"${inst.equity:,.2f}"))
        self._table.setItem(row, _COL["Positions"], _item(str(len(positions))))

    def _show_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._row_paths):
            return
        path = self._row_paths[row]

        menu = QMenu(self)
        act_copy = QAction("Copy terminal path", self)
        act_pos  = QAction("View open positions", self)
        menu.addAction(act_copy)
        menu.addAction(act_pos)

        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText(path)
        )
        act_pos.triggered.connect(
            lambda: self._show_positions(path)
        )
        menu.exec_(self._table.viewport().mapToGlobal(pos))

    def _show_positions(self, path: str) -> None:
        positions = self._source_positions.get(path, {})
        dlg = _PositionsDialog(path, positions, self)
        dlg.exec_()


class _PositionsDialog(QDialog):
    _COLS = ["Ticket", "Symbol", "Dir", "Lots", "Open Price", "SL", "TP",
             "Magic", "Open Time", "Comment"]

    def __init__(
        self,
        terminal_path: str,
        positions: dict[str, OpenPosition],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Positions — {Path(terminal_path).name}")
        self.resize(800, 360)

        layout = QVBoxLayout(self)
        tbl = QTableWidget(len(positions), len(self._COLS))
        tbl.setHorizontalHeaderLabels(self._COLS)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.verticalHeader().setVisible(False)

        for row, pos in enumerate(positions.values()):
            tbl.setItem(row, 0, _item(pos.ticket))
            tbl.setItem(row, 1, _item(pos.symbol))
            tbl.setItem(row, 2, _item(pos.direction.upper()))
            tbl.setItem(row, 3, _item(str(pos.lots)))
            tbl.setItem(row, 4, _item(str(pos.open_price)))
            tbl.setItem(row, 5, _item(str(pos.sl)))
            tbl.setItem(row, 6, _item(str(pos.tp)))
            tbl.setItem(row, 7, _item(str(pos.magic)))
            tbl.setItem(row, 8, _item(pos.open_time.strftime("%Y-%m-%d %H:%M:%S")))
            tbl.setItem(row, 9, _item(pos.comment))

        tbl.resizeColumnsToContents()
        layout.addWidget(tbl)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)


def _item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    return item
