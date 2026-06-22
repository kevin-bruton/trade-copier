"""TCP server that manages connections from MetaTrader EA clients.

Design notes
------------
* The server runs in its own daemon thread using a ``select()`` loop — no asyncio,
  which keeps the design simple and avoids event-loop conflicts with Qt.
* The server thread **owns** all socket objects.  The main (UI/copier) thread
  communicates via two thread-safe queues:
    _send_queue  – (conn_id, bytes) items enqueued by send(); drained by server loop.
    _close_queue – conn_id strings enqueued by register_instance() when a duplicate
                   terminal_path is detected; drained by server loop.
* Events (parsed JSON dicts, plus synthetic _DISCONNECT events) are posted to the
  on_event callback, which the caller should route to a queue.Queue for thread-safe
  hand-off to the UI/copier thread.
"""
from __future__ import annotations

import json
import queue
import select
import socket
import threading
import time
from typing import Callable


class TradeCopierServer:
    """TCP server for MetaTrader EA clients."""

    def __init__(
        self,
        host: str,
        port: int,
        on_event: Callable[[dict], None],
    ) -> None:
        self._host = host
        self._port = port
        self._on_event = on_event

        self._server_sock: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None

        # server-thread-only state
        self._conn_sockets: dict[str, socket.socket] = {}
        self._buffers: dict[str, str] = {}

        # shared state (protected by _lock)
        self._lock = threading.Lock()
        self._path_to_conn: dict[str, str] = {}   # terminal_path → conn_id
        self._conn_to_path: dict[str, str] = {}   # conn_id → terminal_path

        # thread-safe queues
        self._send_queue: queue.Queue[tuple[str, bytes]] = queue.Queue()
        self._close_queue: queue.Queue[str] = queue.Queue()

    # ------------------------------------------------------------------
    # Public API (safe to call from any thread)
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Bind, listen, and start the background server thread."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self._host, self._port))
        self._server_sock.listen(10)
        self._server_sock.setblocking(False)
        self._running = True
        self._thread = threading.Thread(
            target=self._server_loop, name="TradeCopierServer", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the server loop to exit and wait for the thread."""
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def send(self, terminal_path: str, message: dict) -> bool:
        """Serialise *message* to JSON and queue it for delivery.

        Returns ``True`` if the terminal is currently registered (connected),
        ``False`` otherwise.  A ``True`` return does **not** guarantee delivery;
        the connection may drop between enqueueing and the server loop draining.
        """
        with self._lock:
            conn_id = self._path_to_conn.get(terminal_path)
        if conn_id is None:
            return False
        data = (json.dumps(message, separators=(",", ":")) + "\r\n").encode("utf-8")
        self._send_queue.put((conn_id, data))
        return True

    def register_instance(self, terminal_path: str, conn_id: str) -> None:
        """Associate *terminal_path* with *conn_id* (called by copier on REGISTER).

        If *terminal_path* was already mapped to a **different** connection the old
        connection is queued for closure so the server loop can clean it up safely.
        """
        with self._lock:
            old_conn = self._path_to_conn.get(terminal_path)
            if old_conn and old_conn != conn_id:
                # Remove old mapping first so _disconnect emits an empty terminal_path
                # and the copier does not mistakenly mark the instance as disconnected.
                self._conn_to_path.pop(old_conn, None)
                self._close_queue.put(old_conn)
            self._path_to_conn[terminal_path] = conn_id
            self._conn_to_path[conn_id] = terminal_path

    def connected_paths(self) -> list[str]:
        """Return the terminal_paths of all currently registered connections."""
        with self._lock:
            return list(self._path_to_conn.keys())

    # ------------------------------------------------------------------
    # Server loop (runs in background thread)
    # ------------------------------------------------------------------

    def _server_loop(self) -> None:
        while self._running:
            # Build the read list fresh each iteration (connections can appear/disappear)
            all_socks = list(self._conn_sockets.values())
            if self._server_sock:
                read_list = [self._server_sock] + all_socks
            else:
                read_list = all_socks

            if not read_list:
                # Nothing to select on; just drain queues and sleep briefly
                self._drain_close_queue()
                self._drain_send_queue()
                time.sleep(0.05)
                continue

            try:
                readable, _, exceptional = select.select(
                    read_list, [], read_list, 0.05
                )
            except (ValueError, OSError):
                # Server socket was closed (during stop())
                break

            # Accept new connections
            if self._server_sock and self._server_sock in readable:
                self._accept()

            # Read from clients
            for conn_id, sock in list(self._conn_sockets.items()):
                if sock in exceptional:
                    self._disconnect(conn_id, sock)
                elif sock in readable:
                    if not self._read_client(conn_id, sock):
                        self._disconnect(conn_id, sock)

            self._drain_close_queue()
            self._drain_send_queue()

    def _accept(self) -> None:
        try:
            conn, addr = self._server_sock.accept()
        except OSError:
            return
        conn.setblocking(False)
        conn_id = f"{addr[0]}:{addr[1]}"
        self._conn_sockets[conn_id] = conn
        self._buffers[conn_id] = ""
        self._on_event(
            {
                "_conn_id": conn_id,
                "type": "_SERVER_LOG",
                "level": "INFO",
                "message": f"Socket connected: {conn_id}",
            }
        )

    def _read_client(self, conn_id: str, sock: socket.socket) -> bool:
        """Read available data from *sock*.  Returns ``False`` when disconnected."""
        try:
            data = sock.recv(65536)
        except OSError:
            return False
        if not data:
            return False

        self._buffers[conn_id] += data.decode("utf-8", errors="replace")
        while "\r\n" in self._buffers[conn_id]:
            line, self._buffers[conn_id] = self._buffers[conn_id].split("\r\n", 1)
            line = line.strip()
            if line:
                self._handle_line(conn_id, line)
        return True

    def _handle_line(self, conn_id: str, line: str) -> None:
        """Parse a complete JSON line and forward it via the on_event callback."""
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            self._on_event(
                {
                    "_conn_id": conn_id,
                    "type": "_SERVER_LOG",
                    "level": "WARN",
                    "message": (
                        f"Malformed JSON from {conn_id}: {exc.msg} "
                        f"at char {exc.pos}; line starts {line[:120]!r}"
                    ),
                }
            )
            return
        if not isinstance(msg, dict):
            self._on_event(
                {
                    "_conn_id": conn_id,
                    "type": "_SERVER_LOG",
                    "level": "WARN",
                    "message": f"Ignored non-object JSON from {conn_id}",
                }
            )
            return
        msg["_conn_id"] = conn_id
        self._on_event(msg)

    def _disconnect(self, conn_id: str, sock: socket.socket) -> None:
        """Close *sock* and clean up all associated state."""
        try:
            sock.close()
        except OSError:
            pass
        self._conn_sockets.pop(conn_id, None)
        self._buffers.pop(conn_id, None)

        with self._lock:
            terminal_path = self._conn_to_path.pop(conn_id, None)
            if terminal_path:
                self._path_to_conn.pop(terminal_path, None)

        # Notify the copier so it can mark the instance as disconnected.
        # terminal_path is empty when an old duplicate connection is closed.
        self._on_event(
            {
                "_conn_id": conn_id,
                "type": "_DISCONNECT",
                "terminal_path": terminal_path or "",
            }
        )

    def _drain_close_queue(self) -> None:
        """Close any connections queued by register_instance() for cleanup."""
        while True:
            try:
                old_conn = self._close_queue.get_nowait()
            except queue.Empty:
                break
            old_sock = self._conn_sockets.get(old_conn)
            if old_sock:
                self._disconnect(old_conn, old_sock)

    def _drain_send_queue(self) -> None:
        """Flush all pending outbound messages."""
        while True:
            try:
                conn_id, data = self._send_queue.get_nowait()
            except queue.Empty:
                break
            sock = self._conn_sockets.get(conn_id)
            if sock:
                try:
                    sock.sendall(data)
                except OSError:
                    self._disconnect(conn_id, sock)
