"""Data classes shared across the application."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class MTInstance:
    """Represents a connected (or previously-connected) MetaTrader terminal."""

    terminal_path: str        # Installation directory — primary identifier
    platform: str             # "MT4" | "MT5"
    broker: str
    account: str
    account_type: str         # "demo" | "real"
    currency: str
    leverage: int
    balance: float
    equity: float
    margin: float
    free_margin: float
    connected: bool
    connected_at: datetime | None
    last_heartbeat: datetime | None

    @property
    def display_name(self) -> str:
        """Last path component used as the human-readable label in the UI."""
        return Path(self.terminal_path).name


@dataclass
class OpenPosition:
    """A single open position on a source terminal."""

    ticket: str
    symbol: str
    direction: str    # "buy" | "sell"
    lots: float
    open_price: float
    sl: float
    tp: float
    magic: int
    open_time: datetime
    comment: str


@dataclass
class CopyRecord:
    """Tracks a single copy operation from source to one destination."""

    copy_id: str
    source_terminal_path: str
    source_ticket: str
    dest_terminal_path: str
    dest_ticket: str | None
    symbol_source: str
    symbol_dest: str
    direction: str
    source_lots: float
    dest_lots: float
    magic: int
    sl: float
    tp: float
    status: str               # "pending" | "open" | "pending_close" | "closed" | "error"
    error: str
    opened_at: datetime
    closed_at: datetime | None
    # Set when the source closes before the destination copy is filled
    pending_close: bool = False


@dataclass
class SizeConfig:
    """Lot-size calculation rule for a destination."""

    mode: str     # "fixed" | "proportional" | "account_percent" | "fixed_dollar"
    value: float


@dataclass
class DestinationConfig:
    """Configuration for one destination terminal within a copy rule."""

    terminal_path: str
    size: SizeConfig
    size_by_magic: dict[int, SizeConfig] = field(default_factory=dict)


@dataclass
class CopyRule:
    """A complete copy rule: one source → one or more destinations."""

    rule_id: str
    name: str
    enabled: bool
    source_terminal_path: str
    destinations: list[DestinationConfig] = field(default_factory=list)
    magic_numbers: list[int] = field(default_factory=list)   # empty = copy all
    symbol_map: dict[str, str] = field(default_factory=dict)


@dataclass
class ServerConfig:
    """TCP server bind settings loaded from config.yaml."""

    host: str = "127.0.0.1"
    port: int = 9000
    heartbeat_interval: int = 30
    account_update_interval: int = 15
