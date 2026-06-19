"""Event log panel — read-only monospace text view with colour-coded levels."""
from __future__ import annotations

import html as _html
from datetime import datetime

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QPlainTextEdit, QFileDialog,
)
from PyQt5.QtGui import QFont

_COLOURS: dict[str, str] = {
    "INFO":  "#e0e0e0",
    "WARN":  "#ffcc00",
    "ERROR": "#ff5555",
    "TRADE": "#44ccff",
    "COPY":  "#88ff88",
}


class LogPanel(QWidget):
    """Scrollable event log with per-level colour coding."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Toolbar ──────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        lbl = QLabel("Event Log")
        lbl.setStyleSheet("font-weight: bold;")
        toolbar.addWidget(lbl)
        toolbar.addStretch()

        btn_clear = QPushButton("Clear")
        btn_clear.setFixedWidth(60)
        btn_clear.clicked.connect(self._on_clear)
        toolbar.addWidget(btn_clear)

        btn_save = QPushButton("Save log…")
        btn_save.setFixedWidth(80)
        btn_save.clicked.connect(self._on_save)
        toolbar.addWidget(btn_save)

        layout.addLayout(toolbar)

        # ── Text area ────────────────────────────────────────────────────
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(5000)
        font = QFont("Consolas", 9)
        font.setStyleHint(QFont.Monospace)
        self._text.setFont(font)
        layout.addWidget(self._text)

    # ── Public API ────────────────────────────────────────────────────────

    def append_log(self, level: str, message: str) -> None:
        """Append a colour-coded log line."""
        colour = _COLOURS.get(level.upper(), "#e0e0e0")
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f'{timestamp} [{level:<5}] {_html.escape(message)}'
        html = f'<span style="color:{colour};">{line}</span>'

        sb = self._text.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4

        self._text.appendHtml(html)

        if at_bottom:
            sb.setValue(sb.maximum())

    # ── Internal slots ────────────────────────────────────────────────────

    def _on_clear(self) -> None:
        self._text.clear()

    def _on_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log", "", "Text files (*.txt);;All files (*)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._text.toPlainText())
