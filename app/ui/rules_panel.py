"""Rules panel — view and edit copy rules."""
from __future__ import annotations

import uuid
from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QAbstractItemView,
    QDialog, QDialogButtonBox, QFormLayout,
    QLineEdit, QCheckBox, QComboBox,
    QMessageBox,
)
from PyQt5.QtCore import Qt

from app.config import ConfigManager
from app.models import CopyRule, DestinationConfig, MTInstance, SizeConfig

_RULE_COLS = ["Name", "Source", "→", "Destination(s)", "Magic Filter", "Size Mode", "Enabled"]
_RCOL = {name: i for i, name in enumerate(_RULE_COLS)}


class RulesPanel(QWidget):
    """Displays and edits the configured copy rules."""

    def __init__(self, config: ConfigManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._rules: list[CopyRule] = []
        self._instances: dict[str, MTInstance] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Toolbar ───────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        lbl = QLabel("Copy Rules")
        lbl.setStyleSheet("font-weight: bold;")
        toolbar.addWidget(lbl)
        toolbar.addStretch()

        btn_add = QPushButton("+ Add Rule")
        btn_edit = QPushButton("✎ Edit Rule")
        btn_del  = QPushButton("✕ Delete Rule")
        for btn in (btn_add, btn_edit, btn_del):
            btn.setFixedWidth(100)
            toolbar.addWidget(btn)

        btn_add.clicked.connect(self._on_add)
        btn_edit.clicked.connect(self._on_edit)
        btn_del.clicked.connect(self._on_delete)

        layout.addLayout(toolbar)

        # ── Table ─────────────────────────────────────────────────────────
        self._table = QTableWidget(0, len(_RULE_COLS))
        self._table.setHorizontalHeaderLabels(_RULE_COLS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            _RCOL["Name"], QHeaderView.Stretch
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(self._on_edit)
        layout.addWidget(self._table)

    # ── Public API ────────────────────────────────────────────────────────

    def refresh(self, rules: list[CopyRule], instances: dict[str, MTInstance]) -> None:
        self._rules = list(rules)
        self._instances = instances
        self._repopulate()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _repopulate(self) -> None:
        self._table.setRowCount(len(self._rules))
        for row, rule in enumerate(self._rules):
            self._set_row(row, rule)

    def _set_row(self, row: int, rule: CopyRule) -> None:
        src_name = self._display(rule.source_terminal_path)
        dest_names = ", ".join(self._display(d.terminal_path) for d in rule.destinations)
        magic_str = ", ".join(str(m) for m in rule.magic_numbers) if rule.magic_numbers else "all"
        size_modes = ", ".join({d.size.mode for d in rule.destinations}) if rule.destinations else "—"
        enabled_str = "✓" if rule.enabled else "✗"

        for col, val in zip(
            range(len(_RULE_COLS)),
            [rule.name, src_name, "→", dest_names, magic_str, size_modes, enabled_str],
        ):
            item = QTableWidgetItem(val)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if col == _RCOL["Enabled"]:
                item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, col, item)

    def _display(self, path: str) -> str:
        inst = self._instances.get(path)
        if inst:
            return inst.display_name
        return Path(path).name if path else "—"

    def _selected_row(self) -> int | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        return rows[0].row()

    def _on_add(self) -> None:
        dlg = EditRuleDialog(self._config, self._instances, None, self)
        if dlg.exec_() == QDialog.Accepted:
            self._config.add_rule(dlg.result_rule)
            self.refresh(self._config.get_rules(), self._instances)

    def _on_edit(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        rule = self._rules[row]
        dlg = EditRuleDialog(self._config, self._instances, rule, self)
        if dlg.exec_() == QDialog.Accepted:
            self._config.update_rule(dlg.result_rule)
            self.refresh(self._config.get_rules(), self._instances)

    def _on_delete(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        rule = self._rules[row]
        answer = QMessageBox.question(
            self, "Delete rule",
            f'Delete rule "{rule.name}"?',
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self._config.delete_rule(rule.rule_id)
            self.refresh(self._config.get_rules(), self._instances)


# ── Edit Rule Dialog ───────────────────────────────────────────────────────────

class EditRuleDialog(QDialog):
    """Modal dialog for creating or editing a copy rule."""

    def __init__(
        self,
        config: ConfigManager,
        instances: dict[str, MTInstance],
        rule: CopyRule | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._instances = instances
        self._rule = rule
        self.result_rule: CopyRule | None = None
        self._build_ui()
        if rule:
            self._populate(rule)

    def _build_ui(self) -> None:
        self.setWindowTitle("Edit Rule" if self._rule else "Add Rule")
        self.resize(560, 440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        # Name
        self._name_edit = QLineEdit()
        form.addRow("Name:", self._name_edit)

        # Source
        self._source_combo = QComboBox()
        self._source_combo.setEditable(True)
        for path, inst in self._instances.items():
            self._source_combo.addItem(inst.display_name, path)
            idx = self._source_combo.count() - 1
            self._source_combo.setItemData(idx, path, Qt.ToolTipRole)
        form.addRow("Source:", self._source_combo)

        # Enabled
        self._enabled_cb = QCheckBox("Enabled")
        self._enabled_cb.setChecked(True)
        form.addRow("", self._enabled_cb)

        # Magic numbers
        self._magic_edit = QLineEdit()
        self._magic_edit.setPlaceholderText("all  (or comma-separated numbers)")
        form.addRow("Magic numbers:", self._magic_edit)

        layout.addLayout(form)

        # Destinations table
        layout.addWidget(QLabel("Destinations:"))
        self._dest_table = QTableWidget(0, 3)
        self._dest_table.setHorizontalHeaderLabels(["Terminal path", "Size mode", "Size value"])
        self._dest_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._dest_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._dest_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._dest_table.setMinimumHeight(100)
        layout.addWidget(self._dest_table)

        dest_btns = QHBoxLayout()
        btn_add_dest = QPushButton("+ Add")
        btn_rem_dest = QPushButton("- Remove")
        dest_btns.addStretch()
        dest_btns.addWidget(btn_add_dest)
        dest_btns.addWidget(btn_rem_dest)
        layout.addLayout(dest_btns)
        btn_add_dest.clicked.connect(self._add_dest_row)
        btn_rem_dest.clicked.connect(self._remove_dest_row)

        # Symbol map table
        layout.addWidget(QLabel("Symbol map (source → destination):"))
        self._sym_table = QTableWidget(0, 2)
        self._sym_table.setHorizontalHeaderLabels(["Source symbol", "Dest symbol"])
        self._sym_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._sym_table.setMinimumHeight(80)
        layout.addWidget(self._sym_table)

        sym_btns = QHBoxLayout()
        btn_add_sym = QPushButton("+ Add row")
        btn_rem_sym = QPushButton("- Remove row")
        sym_btns.addStretch()
        sym_btns.addWidget(btn_add_sym)
        sym_btns.addWidget(btn_rem_sym)
        layout.addLayout(sym_btns)
        btn_add_sym.clicked.connect(self._add_sym_row)
        btn_rem_sym.clicked.connect(self._remove_sym_row)

        # Dialog buttons
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _populate(self, rule: CopyRule) -> None:
        self._name_edit.setText(rule.name)
        self._enabled_cb.setChecked(rule.enabled)

        # Set source combo
        idx = self._source_combo.findData(rule.source_terminal_path)
        if idx >= 0:
            self._source_combo.setCurrentIndex(idx)
        else:
            self._source_combo.setEditText(rule.source_terminal_path)

        # Magic numbers
        if rule.magic_numbers:
            self._magic_edit.setText(", ".join(str(m) for m in rule.magic_numbers))

        # Destinations
        for dest in rule.destinations:
            self._add_dest_row(dest.terminal_path, dest.size.mode, str(dest.size.value))

        # Symbol map
        for src_sym, dst_sym in rule.symbol_map.items():
            self._add_sym_row(src_sym, dst_sym)

    def _add_dest_row(
        self,
        path: str = "",
        mode: str = "proportional",
        value: str = "100",
    ) -> None:
        row = self._dest_table.rowCount()
        self._dest_table.insertRow(row)
        self._dest_table.setItem(row, 0, QTableWidgetItem(path))
        mode_combo = QComboBox()
        mode_combo.addItems(["fixed", "proportional", "account_percent", "fixed_dollar"])
        idx = mode_combo.findText(mode)
        if idx >= 0:
            mode_combo.setCurrentIndex(idx)
        self._dest_table.setCellWidget(row, 1, mode_combo)
        self._dest_table.setItem(row, 2, QTableWidgetItem(value))

    def _remove_dest_row(self) -> None:
        rows = self._dest_table.selectionModel().selectedRows()
        for r in sorted(rows, key=lambda x: x.row(), reverse=True):
            self._dest_table.removeRow(r.row())

    def _add_sym_row(self, src: str = "", dst: str = "") -> None:
        row = self._sym_table.rowCount()
        self._sym_table.insertRow(row)
        self._sym_table.setItem(row, 0, QTableWidgetItem(src))
        self._sym_table.setItem(row, 1, QTableWidgetItem(dst))

    def _remove_sym_row(self) -> None:
        rows = self._sym_table.selectionModel().selectedRows()
        for r in sorted(rows, key=lambda x: x.row(), reverse=True):
            self._sym_table.removeRow(r.row())

    def _on_save(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Rule name cannot be empty.")
            return

        # Source terminal path
        idx = self._source_combo.currentIndex()
        if idx >= 0:
            source_path = self._source_combo.itemData(idx) or self._source_combo.currentText()
        else:
            source_path = self._source_combo.currentText().strip()

        # Magic numbers
        magic_text = self._magic_edit.text().strip()
        magic_numbers: list[int] = []
        if magic_text and magic_text.lower() != "all":
            try:
                magic_numbers = [int(x.strip()) for x in magic_text.split(",") if x.strip()]
            except ValueError:
                QMessageBox.warning(self, "Validation", "Magic numbers must be integers.")
                return

        # Destinations
        destinations: list[DestinationConfig] = []
        for r in range(self._dest_table.rowCount()):
            path_item = self._dest_table.item(r, 0)
            val_item  = self._dest_table.item(r, 2)
            combo     = self._dest_table.cellWidget(r, 1)
            d_path = path_item.text().strip() if path_item else ""
            d_mode = combo.currentText() if combo else "proportional"
            try:
                d_val = float(val_item.text()) if val_item else 100.0
            except ValueError:
                d_val = 100.0
            if d_path:
                destinations.append(
                    DestinationConfig(terminal_path=d_path, size=SizeConfig(mode=d_mode, value=d_val))
                )

        # Symbol map
        symbol_map: dict[str, str] = {}
        for r in range(self._sym_table.rowCount()):
            src_item = self._sym_table.item(r, 0)
            dst_item = self._sym_table.item(r, 1)
            if src_item and dst_item:
                s, d = src_item.text().strip(), dst_item.text().strip()
                if s:
                    symbol_map[s] = d

        rule_id = self._rule.rule_id if self._rule else str(uuid.uuid4())[:8]

        self.result_rule = CopyRule(
            rule_id=rule_id,
            name=name,
            enabled=self._enabled_cb.isChecked(),
            source_terminal_path=source_path,
            destinations=destinations,
            magic_numbers=magic_numbers,
            symbol_map=symbol_map,
        )
        self.accept()
