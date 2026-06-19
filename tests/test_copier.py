"""Tests for app/copier.py — TradeCopier business logic.

All tests drive the copier through the public handle_message() API to
maximise realism.  Internal helpers (_parse_time, _parse_positions_string,
_calc_lots) are also tested directly since they carry significant logic.
"""
from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.copier import _float, _int, _parse_positions_string, _parse_time
from tests.conftest import reg_msg, simple_rule

# Convenience path constants used throughout
SRC = r"C:\MT4\Source"
DST = r"C:\MT5\Dest"


# ────────────────────────────────────────────────────────────────────────────
# Pure helper tests
# ────────────────────────────────────────────────────────────────────────────

class TestParseTime:
    def test_valid_iso_format(self):
        dt = _parse_time("2024-01-15T13:45:30")
        assert dt == datetime(2024, 1, 15, 13, 45, 30)

    def test_invalid_string_returns_now(self):
        before = datetime.now()
        dt = _parse_time("not-a-date")
        after = datetime.now()
        assert before <= dt <= after

    def test_empty_string_returns_now(self):
        before = datetime.now()
        dt = _parse_time("")
        after = datetime.now()
        assert before <= dt <= after


class TestParsePositionsString:
    def test_empty_returns_empty_list(self):
        assert _parse_positions_string("") == []

    def test_single_position_parsed(self):
        s = "12345|EURUSD|buy|1.00|1.1000|1.0900|1.1100|0|2024-01-15T12:00:00|"
        positions = _parse_positions_string(s)
        assert len(positions) == 1
        pos = positions[0]
        assert pos.ticket == "12345"
        assert pos.symbol == "EURUSD"
        assert pos.direction == "buy"
        assert pos.lots == pytest.approx(1.0)
        assert pos.open_price == pytest.approx(1.1)
        assert pos.magic == 0

    def test_multiple_positions_semicolon_separated(self):
        s = (
            "1|EURUSD|buy|1.0|1.1000|0|0|0|2024-01-15T12:00:00|;"
            "2|GBPUSD|sell|0.5|1.2500|0|0|12345|2024-01-15T12:01:00|mycomment"
        )
        positions = _parse_positions_string(s)
        assert len(positions) == 2
        assert positions[0].ticket == "1"
        assert positions[1].ticket == "2"
        assert positions[1].magic == 12345
        assert positions[1].comment == "mycomment"

    def test_comment_with_pipe_character_preserved(self):
        # The 10th field (comment) may contain "|"; split("|", 9) preserves it
        s = "1|EURUSD|buy|1.0|1.1000|0|0|0|2024-01-15T12:00:00|part1|part2"
        positions = _parse_positions_string(s)
        assert len(positions) == 1
        assert "part1|part2" in positions[0].comment

    def test_incomplete_record_skipped(self):
        # Fewer than 9 pipe-delimited fields → skip
        s = "1|EURUSD|buy"
        assert _parse_positions_string(s) == []

    def test_trailing_semicolon_ignored(self):
        s = "1|EURUSD|buy|1.0|1.1000|0|0|0|2024-01-15T12:00:00|;"
        positions = _parse_positions_string(s)
        # trailing empty record has < 9 fields; only the real one counted
        assert len(positions) == 1


class TestFloatInt:
    def test_float_from_string(self):
        assert _float("1.5") == pytest.approx(1.5)

    def test_float_from_none(self):
        assert _float(None) == pytest.approx(0.0)

    def test_float_from_invalid(self):
        assert _float("abc") == pytest.approx(0.0)

    def test_int_from_string(self):
        assert _int("42") == 42

    def test_int_from_float_string(self):
        assert _int("3.9") == 3

    def test_int_from_none(self):
        assert _int(None) == 0


# ────────────────────────────────────────────────────────────────────────────
# REGISTER handler
# ────────────────────────────────────────────────────────────────────────────

class TestRegisterHandler:
    def test_creates_instance(self, copier):
        copier.handle_message(reg_msg("c1", SRC))
        assert SRC in copier.instances
        inst = copier.instances[SRC]
        assert inst.platform == "MT4"
        assert inst.broker == "TestBroker"
        assert inst.connected is True

    def test_parses_numeric_fields(self, copier):
        copier.handle_message(reg_msg("c1", SRC, balance="12345.67", equity="12300"))
        inst = copier.instances[SRC]
        assert inst.balance == pytest.approx(12345.67)
        assert inst.equity == pytest.approx(12300.0)

    def test_sends_ack_register(self, copier, mock_server):
        copier.handle_message(reg_msg("c1", SRC))
        mock_server.send.assert_called_once_with(SRC, {"type": "ACK_REGISTER", "terminal_path": SRC})

    def test_calls_server_register_instance(self, copier, mock_server):
        copier.handle_message(reg_msg("c1", SRC))
        mock_server.register_instance.assert_called_once_with(SRC, "c1")

    def test_missing_terminal_path_logs_warn(self, copier, logs):
        copier.handle_message({"_conn_id": "c1", "type": "REGISTER", "terminal_path": ""})
        assert SRC not in copier.instances
        assert any(level == "WARN" for level, _ in logs)

    def test_duplicate_register_logs_warn(self, copier, logs):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", SRC))  # same path, different conn
        assert any(level == "WARN" for level, _ in logs)


# ────────────────────────────────────────────────────────────────────────────
# ACCOUNT_UPDATE handler
# ────────────────────────────────────────────────────────────────────────────

class TestAccountUpdateHandler:
    def test_updates_balance_and_equity(self, copier):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message({
            "_conn_id": "c1",
            "type": "ACCOUNT_UPDATE",
            "balance": "11000",
            "equity": "10900",
            "margin": "100",
            "free_margin": "10800",
        })
        inst = copier.instances[SRC]
        assert inst.balance == pytest.approx(11000.0)
        assert inst.equity == pytest.approx(10900.0)
        assert inst.margin == pytest.approx(100.0)
        assert inst.free_margin == pytest.approx(10800.0)

    def test_unknown_connection_ignored(self, copier):
        # Should not raise; silently ignored
        copier.handle_message({"_conn_id": "ghost", "type": "ACCOUNT_UPDATE", "balance": "5000"})


# ────────────────────────────────────────────────────────────────────────────
# HEARTBEAT handler
# ────────────────────────────────────────────────────────────────────────────

class TestHeartbeatHandler:
    def test_updates_last_heartbeat(self, copier):
        copier.handle_message(reg_msg("c1", SRC))
        old_ts = copier.instances[SRC].last_heartbeat
        time.sleep(0.01)
        copier.handle_message({"_conn_id": "c1", "type": "HEARTBEAT"})
        assert copier.instances[SRC].last_heartbeat >= old_ts


# ────────────────────────────────────────────────────────────────────────────
# _DISCONNECT handler
# ────────────────────────────────────────────────────────────────────────────

class TestDisconnectHandler:
    def test_marks_instance_disconnected(self, copier):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message({"_conn_id": "c1", "type": "_DISCONNECT", "terminal_path": SRC})
        assert copier.instances[SRC].connected is False

    def test_empty_terminal_path_does_not_mark_disconnected(self, copier):
        """Empty terminal_path = old duplicate conn being closed; instance must stay connected."""
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message({"_conn_id": "c1", "type": "_DISCONNECT", "terminal_path": ""})
        assert copier.instances[SRC].connected is True

    def test_unknown_connection_does_not_raise(self, copier):
        copier.handle_message({"_conn_id": "unknown", "type": "_DISCONNECT", "terminal_path": ""})


# ────────────────────────────────────────────────────────────────────────────
# TRADE_OPENED handler
# ────────────────────────────────────────────────────────────────────────────

def _trade_opened_msg(conn_id: str, ticket: str = "111", symbol: str = "EURUSD",
                      direction: str = "buy", lots: str = "1.0", magic: str = "0") -> dict:
    return {
        "_conn_id": conn_id,
        "type": "TRADE_OPENED",
        "ticket": ticket,
        "symbol": symbol,
        "direction": direction,
        "lots": lots,
        "open_price": "1.1000",
        "sl": "0",
        "tp": "0",
        "magic": magic,
        "open_time": "2024-01-15T12:00:00",
        "comment": "",
    }


class TestTradeOpenedHandler:
    @pytest.fixture(autouse=True)
    def setup(self, copier, tmp_config):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", DST))
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST))

    def test_creates_copy_record(self, copier):
        copier.handle_message(_trade_opened_msg("c1"))
        assert len(copier.active_copies) == 1
        rec = next(iter(copier.active_copies.values()))
        assert rec.symbol_source == "EURUSD"
        assert rec.direction == "buy"
        assert rec.source_lots == pytest.approx(1.0)
        assert rec.status == "pending"
        assert rec.dest_ticket is None

    def test_sends_copy_trade_to_destination(self, copier, mock_server):
        copier.handle_message(_trade_opened_msg("c1"))
        copy_calls = [c for c in mock_server.send.call_args_list
                      if c[0][1].get("type") == "COPY_TRADE"]
        assert len(copy_calls) == 1
        payload = copy_calls[0][0][1]
        assert payload["symbol"] == "EURUSD"
        assert payload["direction"] == "buy"
        assert "copy_id" in payload

    def test_records_position_in_source_positions(self, copier):
        copier.handle_message(_trade_opened_msg("c1", ticket="111"))
        assert "111" in copier.source_positions.get(SRC, {})

    def test_disabled_rule_ignored(self, copier, tmp_config):
        tmp_config.rules.clear()
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST, enabled=False))
        copier.handle_message(_trade_opened_msg("c1", ticket="200"))
        assert len(copier.active_copies) == 0

    def test_magic_filter_blocks_non_matching(self, copier, tmp_config):
        tmp_config.rules.clear()
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST, magic_numbers=[99999]))
        copier.handle_message(_trade_opened_msg("c1", ticket="201", magic="12345"))
        assert len(copier.active_copies) == 0

    def test_magic_filter_allows_matching(self, copier, tmp_config):
        tmp_config.rules.clear()
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST, magic_numbers=[12345]))
        copier.handle_message(_trade_opened_msg("c1", ticket="202", magic="12345"))
        assert len(copier.active_copies) == 1

    def test_magic_empty_allows_all(self, copier, tmp_config):
        tmp_config.rules.clear()
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST, magic_numbers=[]))
        copier.handle_message(_trade_opened_msg("c1", ticket="203", magic="99999"))
        assert len(copier.active_copies) == 1

    def test_symbol_map_applied(self, copier, tmp_config, mock_server):
        tmp_config.rules.clear()
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST, symbol_map={"XAUUSD": "GOLD"}))
        copier.handle_message(_trade_opened_msg("c1", ticket="204", symbol="XAUUSD"))
        copy_calls = [c for c in mock_server.send.call_args_list
                      if c[0][1].get("type") == "COPY_TRADE"]
        assert copy_calls[0][0][1]["symbol"] == "GOLD"
        rec = next(iter(copier.active_copies.values()))
        assert rec.symbol_dest == "GOLD"

    def test_self_copy_prevented(self, copier, tmp_config):
        """Rule whose dest == source must not trigger a copy."""
        tmp_config.rules.clear()
        tmp_config.add_rule(simple_rule(source=SRC, dest=SRC))  # dest = source
        copier.handle_message(_trade_opened_msg("c1", ticket="205"))
        assert len(copier.active_copies) == 0

    def test_no_matching_rule_no_copy(self, copier, tmp_config):
        tmp_config.rules.clear()
        tmp_config.add_rule(simple_rule(source=r"C:\Other", dest=DST))
        copier.handle_message(_trade_opened_msg("c1", ticket="206"))
        assert len(copier.active_copies) == 0


# ────────────────────────────────────────────────────────────────────────────
# COPY_RESULT handler
# ────────────────────────────────────────────────────────────────────────────

class TestCopyResultHandler:
    @pytest.fixture(autouse=True)
    def setup(self, copier, tmp_config):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", DST))
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST))
        copier.handle_message(_trade_opened_msg("c1"))
        self.copy_id = next(iter(copier.active_copies))

    def test_success_marks_open_and_sets_ticket(self, copier):
        copier.handle_message({
            "_conn_id": "c2", "type": "COPY_RESULT",
            "copy_id": self.copy_id, "success": "true",
            "ticket": "999", "open_price": "1.1001",
        })
        rec = copier.active_copies[self.copy_id]
        assert rec.status == "open"
        assert rec.dest_ticket == "999"

    def test_failure_marks_error(self, copier):
        copier.handle_message({
            "_conn_id": "c2", "type": "COPY_RESULT",
            "copy_id": self.copy_id, "success": "false",
            "error": "Broker rejected",
        })
        rec = copier.active_copies[self.copy_id]
        assert rec.status == "error"
        assert rec.error == "Broker rejected"

    def test_success_with_pending_close_triggers_close(self, copier, mock_server):
        """If source closed before copy filled, filling it should immediately send CLOSE_TRADE."""
        copier.active_copies[self.copy_id].pending_close = True
        copier.handle_message({
            "_conn_id": "c2", "type": "COPY_RESULT",
            "copy_id": self.copy_id, "success": "true",
            "ticket": "999", "open_price": "1.1001",
        })
        close_calls = [c for c in mock_server.send.call_args_list
                       if c[0][1].get("type") == "CLOSE_TRADE"]
        assert len(close_calls) == 1

    def test_unknown_copy_id_logs_warn(self, copier, logs):
        copier.handle_message({
            "_conn_id": "c2", "type": "COPY_RESULT",
            "copy_id": "nonexistent", "success": "true", "ticket": "1",
        })
        assert any(level == "WARN" for level, _ in logs)


# ────────────────────────────────────────────────────────────────────────────
# TRADE_CLOSED handler
# ────────────────────────────────────────────────────────────────────────────

def _fill_copy(copier, copy_id: str, conn_id: str = "c2") -> None:
    """Helper: simulate a successful COPY_RESULT for an open copy."""
    copier.handle_message({
        "_conn_id": conn_id, "type": "COPY_RESULT",
        "copy_id": copy_id, "success": "true",
        "ticket": "999", "open_price": "1.1001",
    })


class TestTradeClosedHandler:
    @pytest.fixture(autouse=True)
    def setup(self, copier, tmp_config):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", DST))
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST))
        copier.handle_message(_trade_opened_msg("c1", ticket="111"))
        self.copy_id = next(iter(copier.active_copies))
        _fill_copy(copier, self.copy_id)

    def test_closed_filled_copy_triggers_close_trade(self, copier, mock_server):
        copier.handle_message({
            "_conn_id": "c1", "type": "TRADE_CLOSED",
            "ticket": "111", "symbol": "EURUSD", "direction": "buy",
            "lots": "1.0", "close_price": "1.1050", "profit": "50",
            "magic": "0", "close_time": "2024-01-15T13:00:00",
        })
        close_calls = [c for c in mock_server.send.call_args_list
                       if c[0][1].get("type") == "CLOSE_TRADE"]
        assert len(close_calls) == 1
        assert close_calls[0][0][1]["ticket"] == "999"

    def test_closed_filled_copy_status_pending_close(self, copier):
        copier.handle_message({
            "_conn_id": "c1", "type": "TRADE_CLOSED",
            "ticket": "111", "symbol": "EURUSD", "direction": "buy",
            "lots": "1.0", "close_price": "1.1050", "profit": "50",
            "magic": "0", "close_time": "",
        })
        assert copier.active_copies[self.copy_id].status == "pending_close"

    def test_closed_before_fill_sets_pending_close(self, copier, tmp_config):
        """Source closes before the copy is filled → pending_close=True, status stays pending."""
        # Add a second trade that has NOT been filled yet
        copier.handle_message(_trade_opened_msg("c1", ticket="222", symbol="GBPUSD"))
        unfilled_copy_id = next(
            cid for cid, rec in copier.active_copies.items() if rec.symbol_source == "GBPUSD"
        )
        # Close the source before fill
        copier.handle_message({
            "_conn_id": "c1", "type": "TRADE_CLOSED",
            "ticket": "222", "symbol": "GBPUSD", "direction": "buy",
            "lots": "1.0", "close_price": "1.25", "profit": "10",
            "magic": "0", "close_time": "",
        })
        rec = copier.active_copies[unfilled_copy_id]
        assert rec.pending_close is True
        assert rec.status == "pending"

    def test_removes_position_from_source_positions(self, copier):
        copier.handle_message({
            "_conn_id": "c1", "type": "TRADE_CLOSED",
            "ticket": "111", "symbol": "EURUSD", "direction": "buy",
            "lots": "1.0", "close_price": "1.1050", "profit": "50",
            "magic": "0", "close_time": "",
        })
        assert "111" not in copier.source_positions.get(SRC, {})


# ────────────────────────────────────────────────────────────────────────────
# CLOSE_RESULT handler
# ────────────────────────────────────────────────────────────────────────────

class TestCloseResultHandler:
    @pytest.fixture(autouse=True)
    def setup(self, copier, tmp_config):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", DST))
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST))
        copier.handle_message(_trade_opened_msg("c1"))
        self.copy_id = next(iter(copier.active_copies))
        _fill_copy(copier, self.copy_id)
        # Trigger close
        copier.handle_message({
            "_conn_id": "c1", "type": "TRADE_CLOSED",
            "ticket": "111", "symbol": "EURUSD", "direction": "buy",
            "lots": "1.0", "close_price": "1.105", "profit": "50",
            "magic": "0", "close_time": "",
        })

    def test_success_marks_closed_with_timestamp(self, copier):
        copier.handle_message({
            "_conn_id": "c2", "type": "CLOSE_RESULT",
            "copy_id": self.copy_id, "success": "true", "close_price": "1.105",
        })
        rec = copier.active_copies[self.copy_id]
        assert rec.status == "closed"
        assert rec.closed_at is not None

    def test_failure_marks_error(self, copier):
        copier.handle_message({
            "_conn_id": "c2", "type": "CLOSE_RESULT",
            "copy_id": self.copy_id, "success": "false", "error": "Close failed",
        })
        rec = copier.active_copies[self.copy_id]
        assert rec.status == "error"
        assert rec.error == "Close failed"

    def test_unknown_copy_id_logs_warn(self, copier, logs):
        copier.handle_message({
            "_conn_id": "c2", "type": "CLOSE_RESULT",
            "copy_id": "bad_id", "success": "true", "close_price": "1.105",
        })
        assert any(level == "WARN" for level, _ in logs)


# ────────────────────────────────────────────────────────────────────────────
# POSITIONS_SNAPSHOT handler
# ────────────────────────────────────────────────────────────────────────────

class TestPositionsSnapshotHandler:
    @pytest.fixture(autouse=True)
    def setup(self, copier, tmp_config):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", DST))
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST))

    def test_new_position_in_snapshot_copied(self, copier):
        copier.handle_message({
            "_conn_id": "c1",
            "type": "POSITIONS_SNAPSHOT",
            "positions": "111|EURUSD|buy|1.0|1.1000|0|0|0|2024-01-15T12:00:00|",
        })
        assert len(copier.active_copies) == 1

    def test_existing_position_not_double_copied(self, copier):
        # Open via TRADE_OPENED first
        copier.handle_message(_trade_opened_msg("c1", ticket="111"))
        assert len(copier.active_copies) == 1

        # Snapshot includes same ticket — must NOT create another copy
        copier.handle_message({
            "_conn_id": "c1",
            "type": "POSITIONS_SNAPSHOT",
            "positions": "111|EURUSD|buy|1.0|1.1000|0|0|0|2024-01-15T12:00:00|",
        })
        assert len(copier.active_copies) == 1

    def test_disappeared_position_triggers_close(self, copier, mock_server):
        copier.handle_message(_trade_opened_msg("c1", ticket="111"))
        copy_id = next(iter(copier.active_copies))
        _fill_copy(copier, copy_id)

        # Empty snapshot → ticket 111 has gone
        copier.handle_message({
            "_conn_id": "c1",
            "type": "POSITIONS_SNAPSHOT",
            "positions": "",
        })
        close_calls = [c for c in mock_server.send.call_args_list
                       if c[0][1].get("type") == "CLOSE_TRADE"]
        assert len(close_calls) >= 1

    def test_snapshot_updates_source_positions(self, copier):
        snapshot_str = "111|EURUSD|buy|1.0|1.1000|0|0|0|2024-01-15T12:00:00|"
        copier.handle_message({
            "_conn_id": "c1",
            "type": "POSITIONS_SNAPSHOT",
            "positions": snapshot_str,
        })
        assert "111" in copier.source_positions.get(SRC, {})


# ────────────────────────────────────────────────────────────────────────────
# Lot-size calculation (_calc_lots via TRADE_OPENED round-trip)
# ────────────────────────────────────────────────────────────────────────────

class TestCalcLots:
    """Drive _calc_lots through handle_message to verify CopyRecord.dest_lots."""

    def _open_trade(self, copier, lots: str = "1.0", magic: str = "0",
                    ticket: str = "1", symbol: str = "EURUSD") -> str:
        copier.handle_message(_trade_opened_msg("c1", ticket=ticket, lots=lots,
                                                magic=magic, symbol=symbol))
        return next(
            cid for cid, rec in copier.active_copies.items() if rec.source_ticket == ticket
        )

    def test_fixed_mode(self, copier, tmp_config):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", DST))
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST, size_mode="fixed", size_value=0.25))
        cid = self._open_trade(copier, lots="2.0")
        assert copier.active_copies[cid].dest_lots == pytest.approx(0.25)

    def test_proportional_100_pct(self, copier, tmp_config):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", DST))
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST, size_mode="proportional", size_value=100.0))
        cid = self._open_trade(copier, lots="2.0")
        assert copier.active_copies[cid].dest_lots == pytest.approx(2.0)

    def test_proportional_50_pct(self, copier, tmp_config):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", DST))
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST, size_mode="proportional", size_value=50.0))
        cid = self._open_trade(copier, lots="2.0")
        assert copier.active_copies[cid].dest_lots == pytest.approx(1.0)

    def test_proportional_clamped_to_min_001(self, copier, tmp_config):
        # 0.01 lots × 1% = 0.0001 → clamped to 0.01
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", DST))
        tmp_config.add_rule(simple_rule(source=SRC, dest=DST, size_mode="proportional", size_value=1.0))
        cid = self._open_trade(copier, lots="0.01")
        assert copier.active_copies[cid].dest_lots >= 0.01

    def test_account_percent_mode(self, copier, tmp_config):
        # (dest_balance * pct/100) / (src_balance / src_lots)
        # (20000 * 1.0/100) / (10000 / 1.0) = 200 / 10000 = 0.02
        copier.handle_message(reg_msg("c1", SRC, balance="10000"))
        copier.handle_message(reg_msg("c2", DST, balance="20000"))
        tmp_config.add_rule(
            simple_rule(source=SRC, dest=DST, size_mode="account_percent", size_value=1.0)
        )
        cid = self._open_trade(copier, lots="1.0")
        assert copier.active_copies[cid].dest_lots == pytest.approx(0.02)

    def test_fixed_dollar_falls_back_to_value(self, copier, tmp_config):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", DST))
        tmp_config.add_rule(
            simple_rule(source=SRC, dest=DST, size_mode="fixed_dollar", size_value=0.3)
        )
        cid = self._open_trade(copier, lots="2.0")
        assert copier.active_copies[cid].dest_lots == pytest.approx(0.3)

    def test_unknown_mode_falls_back_to_value(self, copier, tmp_config):
        copier.handle_message(reg_msg("c1", SRC))
        copier.handle_message(reg_msg("c2", DST))
        from app.models import CopyRule, DestinationConfig, SizeConfig
        rule = CopyRule(
            rule_id="r1", name="R", enabled=True,
            source_terminal_path=SRC,
            destinations=[DestinationConfig(DST, SizeConfig(mode="bogus", value=0.7))],
            magic_numbers=[], symbol_map={},
        )
        tmp_config.add_rule(rule)
        cid = self._open_trade(copier, lots="2.0")
        assert copier.active_copies[cid].dest_lots == pytest.approx(0.7)
