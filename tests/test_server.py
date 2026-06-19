"""Tests for app/server.py — TradeCopierServer state management.

These tests exercise the server's public API and internal state management
without starting actual TCP sockets (no calls to .start()).
"""
from __future__ import annotations

import json
import queue

import pytest

from app.server import TradeCopierServer


def _server() -> TradeCopierServer:
    """Create an un-started server instance for unit testing state only."""
    events: list[dict] = []
    return TradeCopierServer("127.0.0.1", 0, on_event=events.append)


class TestRegisterInstance:
    def test_new_path_registered(self):
        srv = _server()
        srv.register_instance(r"C:\MT4\A", "conn_1")
        assert srv._path_to_conn[r"C:\MT4\A"] == "conn_1"
        assert srv._conn_to_path["conn_1"] == r"C:\MT4\A"

    def test_two_distinct_paths_registered(self):
        srv = _server()
        srv.register_instance(r"C:\MT4\A", "conn_1")
        srv.register_instance(r"C:\MT5\B", "conn_2")
        assert srv._path_to_conn[r"C:\MT4\A"] == "conn_1"
        assert srv._path_to_conn[r"C:\MT5\B"] == "conn_2"

    def test_duplicate_path_queues_old_conn(self):
        srv = _server()
        srv.register_instance(r"C:\MT4\A", "conn_1")
        srv.register_instance(r"C:\MT4\A", "conn_2")  # same path, new conn

        # New conn is active
        assert srv._path_to_conn[r"C:\MT4\A"] == "conn_2"
        assert srv._conn_to_path["conn_2"] == r"C:\MT4\A"

        # Old conn removed from path map
        assert "conn_1" not in srv._conn_to_path

        # Old conn queued for server-thread closure
        old = srv._close_queue.get_nowait()
        assert old == "conn_1"

    def test_same_conn_id_no_close_queued(self):
        srv = _server()
        srv.register_instance(r"C:\MT4\A", "conn_1")
        srv.register_instance(r"C:\MT4\A", "conn_1")  # same conn, re-register

        assert srv._close_queue.empty()
        assert srv._path_to_conn[r"C:\MT4\A"] == "conn_1"


class TestSend:
    def test_send_registered_path_returns_true(self):
        srv = _server()
        srv.register_instance(r"C:\MT4\A", "conn_1")
        result = srv.send(r"C:\MT4\A", {"type": "ACK_REGISTER"})
        assert result is True

    def test_send_unregistered_path_returns_false(self):
        srv = _server()
        result = srv.send(r"C:\MT4\Unknown", {"type": "HEARTBEAT"})
        assert result is False

    def test_send_enqueues_json_crlf(self):
        srv = _server()
        srv.register_instance(r"C:\MT4\A", "conn_1")
        srv.send(r"C:\MT4\A", {"type": "ACK_REGISTER", "terminal_path": r"C:\MT4\A"})

        conn_id, data = srv._send_queue.get_nowait()
        assert conn_id == "conn_1"
        assert data.endswith(b"\r\n")
        parsed = json.loads(data.decode().rstrip("\r\n"))
        assert parsed["type"] == "ACK_REGISTER"

    def test_send_unregistered_path_nothing_enqueued(self):
        srv = _server()
        srv.send(r"C:\MT4\Missing", {"type": "HEARTBEAT"})
        assert srv._send_queue.empty()

    def test_send_multiple_messages_ordered(self):
        srv = _server()
        srv.register_instance(r"C:\MT4\A", "conn_1")
        for i in range(3):
            srv.send(r"C:\MT4\A", {"seq": i})

        for expected_seq in range(3):
            _, data = srv._send_queue.get_nowait()
            msg = json.loads(data.decode().rstrip("\r\n"))
            assert msg["seq"] == expected_seq


class TestConnectedPaths:
    def test_empty_initially(self):
        srv = _server()
        assert srv.connected_paths() == []

    def test_returns_registered_paths(self):
        srv = _server()
        srv.register_instance(r"C:\MT4\A", "c1")
        srv.register_instance(r"C:\MT5\B", "c2")
        paths = srv.connected_paths()
        assert r"C:\MT4\A" in paths
        assert r"C:\MT5\B" in paths
        assert len(paths) == 2

    def test_returns_list_copy(self):
        """Modifying the returned list must not affect server state."""
        srv = _server()
        srv.register_instance(r"C:\MT4\A", "c1")
        paths = srv.connected_paths()
        paths.clear()
        assert len(srv.connected_paths()) == 1
