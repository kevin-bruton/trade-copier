"""Shared fixtures and helper factories for all test modules."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from app.config import ConfigManager
from app.copier import TradeCopier
from app.models import CopyRule, DestinationConfig, SizeConfig
from app.server import TradeCopierServer


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_config(tmp_path: Path) -> ConfigManager:
    """ConfigManager backed by a temporary YAML file."""
    return ConfigManager(tmp_path / "config.yaml")


@pytest.fixture
def mock_server() -> MagicMock:
    """MagicMock that satisfies the TradeCopierServer interface."""
    srv = MagicMock(spec=TradeCopierServer)
    srv.send.return_value = True  # destination is always "connected" by default
    return srv


@pytest.fixture
def logs() -> list[tuple[str, str]]:
    """Shared log accumulator wired into the copier fixture."""
    return []


@pytest.fixture
def copier(tmp_config: ConfigManager, mock_server: MagicMock, logs) -> TradeCopier:
    """TradeCopier with a mocked server and an in-memory logger."""
    return TradeCopier(tmp_config, mock_server, on_log=lambda l, m: logs.append((l, m)))


# ── Message builder helpers ───────────────────────────────────────────────────

def reg_msg(
    conn_id: str,
    terminal_path: str,
    platform: str = "MT4",
    broker: str = "TestBroker",
    account: str = "111",
    account_type: str = "demo",
    currency: str = "USD",
    leverage: str = "100",
    balance: str = "10000",
    equity: str = "10000",
    margin: str = "0",
    free_margin: str = "10000",
) -> dict:
    """Build a REGISTER message dict."""
    return {
        "_conn_id":      conn_id,
        "type":          "REGISTER",
        "terminal_path": terminal_path,
        "platform":      platform,
        "broker":        broker,
        "account":       account,
        "account_type":  account_type,
        "currency":      currency,
        "leverage":      leverage,
        "balance":       balance,
        "equity":        equity,
        "margin":        margin,
        "free_margin":   free_margin,
    }


def simple_rule(
    source: str = "C:\\MT4\\Source",
    dest: str = "C:\\MT5\\Dest",
    size_mode: str = "proportional",
    size_value: float = 100.0,
    magic_numbers: list[int] | None = None,
    symbol_map: dict[str, str] | None = None,
    rule_id: str = "rule_1",
    name: str = "Test Rule",
    enabled: bool = True,
) -> CopyRule:
    """Build a minimal CopyRule."""
    return CopyRule(
        rule_id=rule_id,
        name=name,
        enabled=enabled,
        source_terminal_path=source,
        destinations=[
            DestinationConfig(
                terminal_path=dest,
                size=SizeConfig(mode=size_mode, value=size_value),
            )
        ],
        magic_numbers=magic_numbers or [],
        symbol_map=symbol_map or {},
    )
