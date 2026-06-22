"""Tests for app/models.py dataclasses."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.models import (
    CopyRecord,
    MTInstance,
    ServerConfig,
    SizeConfig,
)


def _make_instance(terminal_path: str) -> MTInstance:
    return MTInstance(
        terminal_path=terminal_path,
        platform="MT4",
        broker="IC Markets",
        account="123456",
        account_type="demo",
        currency="USD",
        leverage=100,
        balance=10_000.0,
        equity=10_000.0,
        margin=0.0,
        free_margin=10_000.0,
        connected=True,
        connected_at=datetime.now(),
        last_heartbeat=datetime.now(),
    )


def _make_copy_record(**kwargs) -> CopyRecord:
    defaults = dict(
        copy_id="abc123",
        source_terminal_path="C:\\MT4\\Src",
        source_ticket="1",
        dest_terminal_path="C:\\MT5\\Dst",
        dest_ticket=None,
        symbol_source="EURUSD",
        symbol_dest="EURUSD",
        direction="buy",
        source_lots=1.0,
        dest_lots=1.0,
        magic=0,
        sl=0.0,
        tp=0.0,
        status="pending",
        error="",
        opened_at=datetime.now(),
        closed_at=None,
    )
    defaults.update(kwargs)
    return CopyRecord(**defaults)


class TestMTInstanceDisplayName:
    def test_windows_path(self):
        inst = _make_instance(r"C:\Trading\MetaTrader4_ICMarkets")
        assert inst.display_name == "MetaTrader4_ICMarkets"

    def test_nested_windows_path(self):
        inst = _make_instance(r"C:\Program Files\MT4\Brokers\IcMarkets")
        assert inst.display_name == "IcMarkets"

    def test_unix_style_path(self):
        inst = _make_instance("/home/user/.mt4/terminals/ICMarkets")
        assert inst.display_name == "ICMarkets"

    def test_single_component(self):
        inst = _make_instance("MT4Root")
        assert inst.display_name == "MT4Root"


class TestCopyRecordDefaults:
    def test_pending_close_defaults_false(self):
        rec = _make_copy_record()
        assert rec.pending_close is False

    def test_dest_ticket_can_be_none(self):
        rec = _make_copy_record(dest_ticket=None)
        assert rec.dest_ticket is None

    def test_closed_at_can_be_none(self):
        rec = _make_copy_record(closed_at=None)
        assert rec.closed_at is None


class TestServerConfigDefaults:
    def test_default_host(self):
        assert ServerConfig().host == "127.0.0.1"

    def test_default_port(self):
        assert ServerConfig().port == 9000

    def test_default_heartbeat_interval(self):
        assert ServerConfig().heartbeat_interval == 30

    def test_default_account_update_interval(self):
        assert ServerConfig().account_update_interval == 15


class TestSizeConfig:
    def test_fixed_mode_stored(self):
        sc = SizeConfig(mode="fixed", value=0.5)
        assert sc.mode == "fixed"
        assert sc.value == pytest.approx(0.5)

    def test_proportional_mode_stored(self):
        sc = SizeConfig(mode="proportional", value=100.0)
        assert sc.mode == "proportional"
