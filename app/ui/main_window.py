"""Main application window."""
from __future__ import annotations

import queue
import time
from typing import Any

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout,
    QSplitter, QToolBar, QAction, QLabel, QSizePolicy,
)
from PyQt5.QtCore import Qt

from app.config import ConfigManager
from app.copier import TradeCopier
from app.server import TradeCopierServer
from app.ui.log_panel import LogPanel
from app.ui.instances_panel import InstancesPanel
from app.ui.copies_panel import CopiesPanel
from app.ui.rules_panel import RulesPanel

_STATUS_STOPPED  = '<span style="color:#ff5555;">●</span> Stopped'
_STATUS_STARTING = '<span style="color:#ffaa00;">●</span> Starting…'


def _status_running(host: str, port: int) -> str:
    return f'<span style="color:#44ee44;">●</span> Running on {host}:{port}'


class MainWindow(QMainWindow):
    """Top-level window: toolbar + splitter (instances|rules|copies) + log."""

    _REFRESH_INTERVAL = 0.2  # seconds between full panel refreshes
    _MAX_EVENTS_PER_TICK = 200

    def __init__(
        self,
        config: ConfigManager,
        server: TradeCopierServer,
        copier: TradeCopier,
        event_queue: "queue.Queue[dict[str, Any]]",
    ) -> None:
        super().__init__()
        self._config = config
        self._server = server
        self._copier = copier
        self._event_queue = event_queue
        self._last_refresh: float = 0.0

        self.setWindowTitle("Trade Copier")
        self.resize(1280, 720)

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # ── Toolbar ───────────────────────────────────────────────────────
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        self._act_start = QAction("▶ Start Server", self)
        self._act_stop  = QAction("⏹ Stop Server",  self)
        self._act_save  = QAction("💾 Save Config",  self)
        self._act_reload = QAction("↺ Reload Config", self)
        self._act_stop.setEnabled(False)

        for act in (self._act_start, self._act_stop, self._act_save, self._act_reload):
            tb.addAction(act)

        # Push the status label to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)

        self._status_label = QLabel(_STATUS_STOPPED)
        self._status_label.setTextFormat(Qt.RichText)
        self._status_label.setContentsMargins(8, 0, 8, 0)
        tb.addWidget(self._status_label)

        # ── Central widget ────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        vbox = QVBoxLayout(central)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(4)

        # Top splitter: instances | rules | copies
        splitter = QSplitter(Qt.Horizontal)

        self._instances_panel = InstancesPanel()
        self._rules_panel     = RulesPanel(self._config)
        self._copies_panel    = CopiesPanel()

        splitter.addWidget(self._instances_panel)
        splitter.addWidget(self._rules_panel)
        splitter.addWidget(self._copies_panel)
        splitter.setSizes([320, 380, 500])

        vbox.addWidget(splitter, stretch=1)

        # Log panel — fixed height at bottom
        self._log_panel = LogPanel()
        self._log_panel.setFixedHeight(180)
        vbox.addWidget(self._log_panel)

        # ── Connect actions ───────────────────────────────────────────────
        self._act_start.triggered.connect(self._on_start_server)
        self._act_stop.triggered.connect(self._on_stop_server)
        self._act_save.triggered.connect(self._on_save_config)
        self._act_reload.triggered.connect(self._on_reload_config)

    # ── Public API ────────────────────────────────────────────────────────

    def append_log(self, level: str, message: str) -> None:
        """Write a line to the event log (call from main thread only)."""
        self._log_panel.append_log(level, message)

    def process_events(self) -> None:
        """Drain the event queue and refresh panels periodically.

        Called by a QTimer every 50 ms.
        """
        # Drain at most _MAX_EVENTS_PER_TICK messages
        for _ in range(self._MAX_EVENTS_PER_TICK):
            try:
                msg = self._event_queue.get_nowait()
            except queue.Empty:
                break
            self._copier.handle_message(msg)

        # Throttle full-panel refresh to _REFRESH_INTERVAL seconds
        now = time.monotonic()
        if now - self._last_refresh >= self._REFRESH_INTERVAL:
            self._refresh_panels()
            self._last_refresh = now

    # ── Toolbar action handlers ───────────────────────────────────────────

    def _on_start_server(self) -> None:
        self._status_label.setText(_STATUS_STARTING)
        self._act_start.setEnabled(False)
        self._act_stop.setEnabled(True)
        try:
            self._server.start()
            self._status_label.setText(
                _status_running(self._config.server.host, self._config.server.port)
            )
            self.append_log(
                "INFO",
                f"Server started on {self._config.server.host}:{self._config.server.port}",
            )
        except Exception as exc:
            self._status_label.setText(_STATUS_STOPPED)
            self._act_start.setEnabled(True)
            self._act_stop.setEnabled(False)
            self.append_log("ERROR", f"Failed to start server: {exc}")

    def _on_stop_server(self) -> None:
        try:
            self._server.stop()
        except Exception as exc:
            self.append_log("WARN", f"Error stopping server: {exc}")
        self._status_label.setText(_STATUS_STOPPED)
        self._act_start.setEnabled(True)
        self._act_stop.setEnabled(False)
        self.append_log("INFO", "Server stopped.")

    def _on_save_config(self) -> None:
        try:
            self._config.save()
            self.append_log("INFO", "Configuration saved.")
        except Exception as exc:
            self.append_log("ERROR", f"Failed to save config: {exc}")

    def _on_reload_config(self) -> None:
        try:
            self._config.reload()
            self._refresh_panels()
            self.append_log("INFO", "Configuration reloaded from disk.")
        except Exception as exc:
            self.append_log("ERROR", f"Failed to reload config: {exc}")

    # ── Panel refresh ─────────────────────────────────────────────────────

    def _refresh_panels(self) -> None:
        self._instances_panel.refresh(
            self._copier.instances,
            self._copier.source_positions,
        )
        self._rules_panel.refresh(
            self._config.get_rules(),
            self._copier.instances,
        )
        self._copies_panel.refresh(
            self._copier.active_copies,
            self._copier.instances,
        )

    # ── Window events ─────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._server.running:
            try:
                self._server.stop()
            except Exception:
                pass
        event.accept()
