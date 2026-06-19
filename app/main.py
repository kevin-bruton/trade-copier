"""Trade Copier — application entry point."""
from __future__ import annotations

import queue
import sys

import qdarkstyle
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

from app.config import ConfigManager
from app.copier import TradeCopier
from app.server import TradeCopierServer
from app.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api="pyqt5"))

    config = ConfigManager("config.yaml")

    # Log lines emitted before the window exists go into this buffer.
    _pre_window_log: list[tuple[str, str]] = []

    def _buffer_log(level: str, message: str) -> None:
        _pre_window_log.append((level, message))

    event_queue: queue.Queue[dict] = queue.Queue()

    server = TradeCopierServer(
        host=config.server.host,
        port=config.server.port,
        on_event=lambda msg: event_queue.put(msg),
    )

    copier = TradeCopier(config, server, on_log=_buffer_log)

    window = MainWindow(config, server, copier, event_queue)

    # Wire the real log function now the window exists
    copier._log = window.append_log  # type: ignore[assignment]

    # Drain pre-window log buffer into the log panel
    for level, message in _pre_window_log:
        window.append_log(level, message)
    _pre_window_log.clear()

    window.show()

    timer = QTimer()
    timer.timeout.connect(window.process_events)
    timer.start(50)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
