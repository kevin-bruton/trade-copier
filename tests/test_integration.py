"""Integration tests — real TCP sockets, TradeCopierServer + TradeCopier.

Sections
--------
TestServerNetworking  — low-level TCP framing, event injection, disconnect
TestFullStackIntegration — server + copier wired together; EA protocol flow
"""
from __future__ import annotations

import json
import queue
import socket
import threading
import time
from contextlib import contextmanager
from typing import Iterator

import pytest

from app.config import ConfigManager
from app.copier import TradeCopier
from app.server import TradeCopierServer
from tests.conftest import simple_rule


# ── Helpers ───────────────────────────────────────────────────────────────────

def _free_port() -> int:
    """Return an available TCP port on localhost (small race-condition window)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(condition, timeout: float = 3.0, interval: float = 0.05) -> bool:
    """Poll *condition* until it returns truthy or *timeout* seconds elapse."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


class _EA:
    """Simulated MetaTrader EA — a persistent client socket with a line buffer."""

    def __init__(self, port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect(("127.0.0.1", port))
        self._buf = b""

    def send(self, msg: dict) -> None:
        data = (json.dumps(msg, separators=(",", ":")) + "\r\n").encode("utf-8")
        self._sock.sendall(data)

    def recv(self, timeout: float = 2.0) -> dict | None:
        """Read one `\\r\\n`-terminated JSON message, buffering excess data."""
        deadline = time.monotonic() + timeout
        while b"\r\n" not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(4096)
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\r\n", 1)
        return json.loads(line.decode("utf-8"))

    def recv_until(self, msg_type: str, timeout: float = 3.0) -> dict | None:
        """Drain messages until one matching *msg_type* arrives."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.recv(timeout=deadline - time.monotonic())
            if msg is None:
                break
            if msg.get("type") == msg_type:
                return msg
        return None

    def close(self) -> None:
        self._sock.close()


def _register_msg(terminal_path: str, platform: str = "MT4") -> dict:
    return {
        "type": "REGISTER",
        "terminal_path": terminal_path,
        "platform": platform,
        "broker": "TestBroker",
        "account": "111",
        "account_type": "demo",
        "currency": "USD",
        "leverage": "100",
        "balance": "10000",
        "equity": "10000",
        "margin": "0",
        "free_margin": "10000",
    }


def _trade_opened_msg(ticket: str = "111", symbol: str = "EURUSD",
                      direction: str = "buy", lots: str = "1.0") -> dict:
    return {
        "type": "TRADE_OPENED",
        "ticket": ticket,
        "symbol": symbol,
        "direction": direction,
        "lots": lots,
        "open_price": "1.1000",
        "sl": "0",
        "tp": "0",
        "magic": "0",
        "open_time": "2024-01-15T12:00:00",
        "comment": "",
    }


# ── Server-only networking fixtures ───────────────────────────────────────────

@pytest.fixture
def event_list() -> list[dict]:
    return []


@pytest.fixture
def bare_server(event_list) -> Iterator[tuple[TradeCopierServer, list[dict], int]]:
    """TradeCopierServer without any copier; events accumulate in event_list."""
    port = _free_port()
    srv = TradeCopierServer("127.0.0.1", port, on_event=event_list.append)
    srv.start()
    time.sleep(0.05)  # let the server thread reach select()
    yield srv, event_list, port
    srv.stop()


# ── Full-stack fixture ─────────────────────────────────────────────────────────

@pytest.fixture
def stack(tmp_path) -> Iterator[tuple[TradeCopierServer, TradeCopier, ConfigManager, list, int]]:
    """
    Full stack: TradeCopierServer + TradeCopier on a real TCP port.

    A background drain thread calls copier.handle_message() for every event
    so copier state is kept current throughout each test.
    """
    port = _free_port()
    event_q: queue.Queue[dict] = queue.Queue()
    config = ConfigManager(tmp_path / "config.yaml")
    logs: list[tuple[str, str]] = []

    srv = TradeCopierServer(
        "127.0.0.1", port,
        on_event=lambda m: event_q.put(m),
    )
    cop = TradeCopier(config, srv, on_log=lambda l, m: logs.append((l, m)))

    srv.start()
    time.sleep(0.05)

    stop_flag = threading.Event()

    def drain() -> None:
        while not stop_flag.is_set():
            try:
                msg = event_q.get(timeout=0.05)
                cop.handle_message(msg)
            except queue.Empty:
                pass

    drain_thread = threading.Thread(target=drain, daemon=True)
    drain_thread.start()

    yield srv, cop, config, logs, port

    stop_flag.set()
    srv.stop()
    drain_thread.join(timeout=2)


# ── TestServerNetworking ──────────────────────────────────────────────────────

class TestServerNetworking:
    """Low-level TCP server tests — no copier involved."""

    def test_client_can_connect(self, bare_server):
        srv, events, port = bare_server
        ea = _EA(port)
        assert wait_for(
            lambda: any(
                e.get("type") == "_SERVER_LOG"
                and e.get("level") == "INFO"
                and "Socket connected" in e.get("message", "")
                for e in events
            )
        )
        ea.close()

    def test_message_received_as_event(self, bare_server):
        srv, events, port = bare_server
        ea = _EA(port)
        ea.send({"type": "HEARTBEAT"})
        assert wait_for(lambda: any(e.get("type") == "HEARTBEAT" for e in events))
        ea.close()

    def test_conn_id_injected_into_event(self, bare_server):
        srv, events, port = bare_server
        ea = _EA(port)
        ea.send({"type": "HEARTBEAT"})
        assert wait_for(lambda: any(e.get("type") == "HEARTBEAT" for e in events))
        event = next(e for e in events if e.get("type") == "HEARTBEAT")
        assert "_conn_id" in event
        assert "127.0.0.1:" in event["_conn_id"]
        ea.close()

    def test_multiple_messages_all_received(self, bare_server):
        srv, events, port = bare_server
        ea = _EA(port)
        for i in range(5):
            ea.send({"type": "HEARTBEAT", "seq": i})
        assert wait_for(
            lambda: len([e for e in events if e.get("type") == "HEARTBEAT"]) == 5
        )
        ea.close()

    def test_disconnect_event_emitted(self, bare_server):
        srv, events, port = bare_server
        ea = _EA(port)
        ea.send({"type": "HEARTBEAT"})
        assert wait_for(lambda: len(events) > 0)
        ea.close()
        assert wait_for(lambda: any(e.get("type") == "_DISCONNECT" for e in events))

    def test_malformed_json_logs_warning_but_keeps_connection_alive(self, bare_server):
        srv, events, port = bare_server
        ea = _EA(port)
        ea._sock.sendall(b"not-valid-json\r\n")
        assert wait_for(
            lambda: any(
                e.get("type") == "_SERVER_LOG"
                and e.get("level") == "WARN"
                and "Malformed JSON" in e.get("message", "")
                for e in events
            )
        )
        # Connection remains alive — a subsequent valid message is delivered
        ea.send({"type": "HEARTBEAT"})
        assert wait_for(lambda: any(e.get("type") == "HEARTBEAT" for e in events))
        ea.close()

    def test_multiple_clients_handled_concurrently(self, bare_server):
        srv, events, port = bare_server
        eas = [_EA(port) for _ in range(3)]
        for i, ea in enumerate(eas):
            ea.send({"type": "HEARTBEAT", "client": i})
        assert wait_for(
            lambda: len([e for e in events if e.get("type") == "HEARTBEAT"]) == 3
        )
        for ea in eas:
            ea.close()

    def test_partial_message_buffered(self, bare_server):
        """Send a message in two TCP segments; the server must still parse it."""
        srv, events, port = bare_server
        ea = _EA(port)
        full = json.dumps({"type": "HEARTBEAT"}).encode() + b"\r\n"
        ea._sock.sendall(full[:5])
        time.sleep(0.05)
        ea._sock.sendall(full[5:])
        assert wait_for(lambda: any(e.get("type") == "HEARTBEAT" for e in events))
        ea.close()


# ── TestFullStackIntegration ──────────────────────────────────────────────────

class TestFullStackIntegration:
    """End-to-end Python stack: EA ↔ server ↔ copier ↔ EA."""

    SRC = r"C:\MT4\Source"
    DST = r"C:\MT5\Dest"

    def test_register_returns_ack(self, stack):
        srv, cop, config, logs, port = stack
        ea = _EA(port)
        ea.send(_register_msg(self.SRC))
        ack = ea.recv_until("ACK_REGISTER")
        assert ack is not None
        assert ack["terminal_path"] == self.SRC
        ea.close()

    def test_register_creates_instance_in_copier(self, stack):
        srv, cop, config, logs, port = stack
        ea = _EA(port)
        ea.send(_register_msg(self.SRC))
        assert wait_for(lambda: self.SRC in cop.instances)
        assert cop.instances[self.SRC].platform == "MT4"
        ea.close()

    def test_disconnect_marks_instance_offline(self, stack):
        srv, cop, config, logs, port = stack
        ea = _EA(port)
        ea.send(_register_msg(self.SRC))
        assert wait_for(lambda: self.SRC in cop.instances)
        ea.close()
        assert wait_for(lambda: not cop.instances[self.SRC].connected)

    def test_trade_copy_flow(self, stack):
        """
        Full trade copy: source opens → dest receives COPY_TRADE → dest
        confirms fill → copier marks the record 'open'.
        """
        srv, cop, config, logs, port = stack

        # Both EAs connect and register
        config.add_rule(simple_rule(source=self.SRC, dest=self.DST))
        src_ea = _EA(port)
        dst_ea = _EA(port)
        src_ea.send(_register_msg(self.SRC, platform="MT4"))
        dst_ea.send(_register_msg(self.DST, platform="MT5"))

        # Drain ACKs
        src_ea.recv_until("ACK_REGISTER")
        dst_ea.recv_until("ACK_REGISTER")

        assert wait_for(lambda: self.SRC in cop.instances and self.DST in cop.instances)

        # Source opens a trade
        src_ea.send(_trade_opened_msg())

        # Dest receives COPY_TRADE
        copy_msg = dst_ea.recv_until("COPY_TRADE", timeout=5)
        assert copy_msg is not None, "COPY_TRADE never arrived at destination EA"
        assert copy_msg["symbol"] == "EURUSD"
        assert copy_msg["direction"] == "buy"
        copy_id = copy_msg["copy_id"]

        # Destination EA confirms fill
        dst_ea.send({
            "type": "COPY_RESULT",
            "copy_id": copy_id,
            "success": "true",
            "ticket": "999",
            "open_price": "1.1001",
        })

        assert wait_for(
            lambda: copy_id in cop.active_copies
            and cop.active_copies[copy_id].status == "open"
        ), "Copy record never reached 'open' status"

        src_ea.close()
        dst_ea.close()

    def test_trade_close_flow(self, stack):
        """
        Source closes → dest receives CLOSE_TRADE → dest confirms → record 'closed'.
        """
        srv, cop, config, logs, port = stack
        config.add_rule(simple_rule(source=self.SRC, dest=self.DST))

        src_ea = _EA(port)
        dst_ea = _EA(port)
        src_ea.send(_register_msg(self.SRC))
        dst_ea.send(_register_msg(self.DST))
        src_ea.recv_until("ACK_REGISTER")
        dst_ea.recv_until("ACK_REGISTER")
        assert wait_for(lambda: self.SRC in cop.instances and self.DST in cop.instances)

        src_ea.send(_trade_opened_msg())
        copy_msg = dst_ea.recv_until("COPY_TRADE", timeout=5)
        assert copy_msg is not None
        copy_id = copy_msg["copy_id"]

        # Fill copy
        dst_ea.send({
            "type": "COPY_RESULT",
            "copy_id": copy_id,
            "success": "true",
            "ticket": "999",
            "open_price": "1.1001",
        })
        assert wait_for(
            lambda: copy_id in cop.active_copies
            and cop.active_copies[copy_id].status == "open"
        )

        # Source closes the trade
        src_ea.send({
            "type": "TRADE_CLOSED",
            "ticket": "111",
            "symbol": "EURUSD",
            "direction": "buy",
            "lots": "1.0",
            "close_price": "1.1050",
            "profit": "50",
            "magic": "0",
            "close_time": "2024-01-15T13:00:00",
        })

        # Dest receives CLOSE_TRADE
        close_msg = dst_ea.recv_until("CLOSE_TRADE", timeout=5)
        assert close_msg is not None, "CLOSE_TRADE never arrived at destination EA"
        assert close_msg["ticket"] == "999"

        # Dest confirms close
        dst_ea.send({
            "type": "CLOSE_RESULT",
            "copy_id": copy_id,
            "success": "true",
            "close_price": "1.1050",
        })
        assert wait_for(
            lambda: cop.active_copies[copy_id].status == "closed"
        ), "Copy record never reached 'closed' status"

        src_ea.close()
        dst_ea.close()

    def test_symbol_map_applied_over_network(self, stack):
        """Symbol mapping must appear in the COPY_TRADE sent to the dest EA."""
        srv, cop, config, logs, port = stack
        config.add_rule(simple_rule(
            source=self.SRC, dest=self.DST, symbol_map={"XAUUSD": "GOLD"}
        ))
        src_ea = _EA(port)
        dst_ea = _EA(port)
        src_ea.send(_register_msg(self.SRC))
        dst_ea.send(_register_msg(self.DST))
        src_ea.recv_until("ACK_REGISTER")
        dst_ea.recv_until("ACK_REGISTER")
        assert wait_for(lambda: self.SRC in cop.instances and self.DST in cop.instances)

        src_ea.send(_trade_opened_msg(ticket="200", symbol="XAUUSD"))
        copy_msg = dst_ea.recv_until("COPY_TRADE", timeout=5)
        assert copy_msg is not None
        assert copy_msg["symbol"] == "GOLD"

        src_ea.close()
        dst_ea.close()

    def test_positions_snapshot_reconciliation(self, stack):
        """
        Snapshot with one position triggers a copy when no active CopyRecord
        exists for that ticket.
        """
        srv, cop, config, logs, port = stack
        config.add_rule(simple_rule(source=self.SRC, dest=self.DST))
        src_ea = _EA(port)
        dst_ea = _EA(port)
        src_ea.send(_register_msg(self.SRC))
        dst_ea.send(_register_msg(self.DST))
        src_ea.recv_until("ACK_REGISTER")
        dst_ea.recv_until("ACK_REGISTER")
        assert wait_for(lambda: self.SRC in cop.instances and self.DST in cop.instances)

        src_ea.send({
            "type": "POSITIONS_SNAPSHOT",
            "positions": "333|EURUSD|buy|0.5|1.1000|0|0|0|2024-01-15T12:00:00|",
        })
        copy_msg = dst_ea.recv_until("COPY_TRADE", timeout=5)
        assert copy_msg is not None, "Snapshot did not trigger COPY_TRADE"
        assert copy_msg["symbol"] == "EURUSD"

        src_ea.close()
        dst_ea.close()
