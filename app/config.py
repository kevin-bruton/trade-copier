"""YAML configuration manager.

Loads config.yaml on startup (creating a default file if absent).
Provides typed accessors and atomic save/reload.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from app.models import (
    CopyRule,
    DestinationConfig,
    ServerConfig,
    SizeConfig,
)

_DEFAULT_YAML = """\
server:
  host: "127.0.0.1"
  port: 9000
  heartbeat_interval: 30
  account_update_interval: 15

copy_rules: []
# Example rule (uncomment and fill in):
# copy_rules:
#   - id: "rule_001"
#     name: "IC Markets MT4 -> Pepperstone MT5"
#     enabled: true
#     # terminal_path as reported by the EA (TerminalPath() / TERMINAL_PATH)
#     source_terminal_path: "C:\\\\Trading\\\\MetaTrader4_ICMarkets"
#     destinations:
#       - terminal_path: "C:\\\\Trading\\\\MetaTrader5_Pepperstone"
#         size_mode: "proportional"   # fixed | proportional | account_percent | fixed_dollar
#         size_value: 100             # 100 % of source size
#         size_by_magic:              # optional per-magic overrides
#           12345:
#             size_mode: "fixed"
#             size_value: 0.5
#     magic_numbers: []              # empty = copy ALL magic numbers
#     symbol_map:
#       "EURUSD": "EURUSD"
#       "XAUUSD": "GOLD"
"""


class ConfigManager:
    """Load, save, and reload the application configuration."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._raw: dict[str, Any] = {}
        self.server: ServerConfig = ServerConfig()
        self.rules: list[CopyRule] = []
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_server_config(self) -> ServerConfig:
        return self.server

    def get_rules(self) -> list[CopyRule]:
        return self.rules

    def save(self) -> None:
        """Rebuild _raw from current state and write atomically."""
        self._raw["server"] = {
            "host": self.server.host,
            "port": self.server.port,
            "heartbeat_interval": self.server.heartbeat_interval,
            "account_update_interval": self.server.account_update_interval,
        }

        copy_rules: list[dict] = []
        for rule in self.rules:
            destinations: list[dict] = []
            for dest in rule.destinations:
                sbm: dict[int, dict] = {}
                for magic, sc in dest.size_by_magic.items():
                    sbm[magic] = {"size_mode": sc.mode, "size_value": sc.value}
                destinations.append(
                    {
                        "terminal_path": dest.terminal_path,
                        "size_mode": dest.size.mode,
                        "size_value": dest.size.value,
                        "size_by_magic": sbm,
                    }
                )
            copy_rules.append(
                {
                    "id": rule.rule_id,
                    "name": rule.name,
                    "enabled": rule.enabled,
                    "source_terminal_path": rule.source_terminal_path,
                    "destinations": destinations,
                    "magic_numbers": rule.magic_numbers,
                    "symbol_map": rule.symbol_map,
                }
            )
        self._raw["copy_rules"] = copy_rules

        # Atomic write: temp file → rename
        tmp = self._path.with_suffix(".yaml.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            yaml.dump(
                self._raw,
                fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        tmp.replace(self._path)

    def reload(self) -> None:
        """Re-read config from disk."""
        self._load()

    def add_rule(self, rule: CopyRule) -> None:
        """Append a new rule and persist."""
        self.rules.append(rule)
        self.save()

    def update_rule(self, rule: CopyRule) -> None:
        """Replace an existing rule (matched by rule_id) and persist."""
        for i, r in enumerate(self.rules):
            if r.rule_id == rule.rule_id:
                self.rules[i] = rule
                self.save()
                return
        # If not found, treat as add
        self.add_rule(rule)

    def delete_rule(self, rule_id: str) -> None:
        """Remove a rule by ID and persist."""
        self.rules = [r for r in self.rules if r.rule_id != rule_id]
        self.save()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            self._create_default()

        with open(self._path, "r", encoding="utf-8") as fh:
            self._raw = yaml.safe_load(fh) or {}

        self._parse()

    def _create_default(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            fh.write(_DEFAULT_YAML)

    def _parse(self) -> None:
        # --- Server config ---
        srv = self._raw.get("server", {})
        self.server = ServerConfig(
            host=srv.get("host", "127.0.0.1"),
            port=int(srv.get("port", 9000)),
            heartbeat_interval=int(srv.get("heartbeat_interval", 30)),
            account_update_interval=int(srv.get("account_update_interval", 15)),
        )

        # --- Copy rules ---
        self.rules = []
        for rd in self._raw.get("copy_rules", []) or []:
            destinations: list[DestinationConfig] = []
            for dd in rd.get("destinations", []) or []:
                sbm: dict[int, SizeConfig] = {}
                for k, v in (dd.get("size_by_magic") or {}).items():
                    sbm[int(k)] = SizeConfig(
                        mode=v.get("size_mode", "fixed"),
                        value=float(v.get("size_value", 0.1)),
                    )
                destinations.append(
                    DestinationConfig(
                        terminal_path=dd.get("terminal_path", ""),
                        size=SizeConfig(
                            mode=dd.get("size_mode", "fixed"),
                            value=float(dd.get("size_value", 0.1)),
                        ),
                        size_by_magic=sbm,
                    )
                )

            self.rules.append(
                CopyRule(
                    rule_id=rd.get("id", ""),
                    name=rd.get("name", ""),
                    enabled=bool(rd.get("enabled", True)),
                    source_terminal_path=rd.get("source_terminal_path", ""),
                    destinations=destinations,
                    magic_numbers=[int(m) for m in (rd.get("magic_numbers") or [])],
                    symbol_map=dict(rd.get("symbol_map") or {}),
                )
            )
