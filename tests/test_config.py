"""Tests for app/config.py — ConfigManager load/save/CRUD."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import ConfigManager
from app.models import CopyRule, DestinationConfig, SizeConfig


def _rule(
    rule_id: str = "r1",
    name: str = "Test Rule",
    source: str = r"C:\MT4\Src",
    dest: str = r"C:\MT5\Dst",
    size_mode: str = "fixed",
    size_value: float = 0.5,
    enabled: bool = True,
    magic_numbers: list[int] | None = None,
    symbol_map: dict[str, str] | None = None,
) -> CopyRule:
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


class TestConfigDefaults:
    def test_creates_yaml_if_absent(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        assert not cfg_path.exists()
        ConfigManager(cfg_path)
        assert cfg_path.exists()

    def test_default_host(self, tmp_path):
        cm = ConfigManager(tmp_path / "config.yaml")
        assert cm.server.host == "127.0.0.1"

    def test_default_port(self, tmp_path):
        cm = ConfigManager(tmp_path / "config.yaml")
        assert cm.server.port == 9000

    def test_default_heartbeat(self, tmp_path):
        cm = ConfigManager(tmp_path / "config.yaml")
        assert cm.server.heartbeat_interval == 30

    def test_default_empty_rules(self, tmp_path):
        cm = ConfigManager(tmp_path / "config.yaml")
        assert cm.rules == []


class TestConfigLoad:
    def test_loads_server_section(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "server:\n  host: '0.0.0.0'\n  port: 8888\n"
            "  heartbeat_interval: 60\n  account_update_interval: 30\n"
            "copy_rules: []\n",
            encoding="utf-8",
        )
        cm = ConfigManager(cfg_path)
        assert cm.server.host == "0.0.0.0"
        assert cm.server.port == 8888
        assert cm.server.heartbeat_interval == 60
        assert cm.server.account_update_interval == 30

    def test_loads_rule_with_symbol_map(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "server: {host: localhost, port: 9000, heartbeat_interval: 30, account_update_interval: 15}\n"
            "copy_rules:\n"
            "  - id: r1\n    name: Rule\n    enabled: true\n"
            "    source_terminal_path: 'C:\\\\MT4\\\\Src'\n"
            "    destinations:\n"
            "      - terminal_path: 'C:\\\\MT5\\\\Dst'\n"
            "        size_mode: proportional\n        size_value: 100\n"
            "    magic_numbers: [12345, 67890]\n"
            "    symbol_map:\n      EURUSD: EURUSDm\n      XAUUSD: GOLD\n",
            encoding="utf-8",
        )
        cm = ConfigManager(cfg_path)
        assert len(cm.rules) == 1
        rule = cm.rules[0]
        assert rule.magic_numbers == [12345, 67890]
        assert rule.symbol_map == {"EURUSD": "EURUSDm", "XAUUSD": "GOLD"}

    def test_loads_rule_with_size_by_magic(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "server: {host: localhost, port: 9000, heartbeat_interval: 30, account_update_interval: 15}\n"
            "copy_rules:\n"
            "  - id: r1\n    name: Rule\n    enabled: true\n"
            "    source_terminal_path: 'C:\\\\MT4\\\\Src'\n"
            "    destinations:\n"
            "      - terminal_path: 'C:\\\\MT5\\\\Dst'\n"
            "        size_mode: proportional\n        size_value: 100\n"
            "        size_by_magic:\n"
            "          12345:\n            size_mode: fixed\n            size_value: 0.5\n"
            "    magic_numbers: []\n    symbol_map: {}\n",
            encoding="utf-8",
        )
        cm = ConfigManager(cfg_path)
        dest = cm.rules[0].destinations[0]
        assert 12345 in dest.size_by_magic
        assert dest.size_by_magic[12345].mode == "fixed"
        assert dest.size_by_magic[12345].value == pytest.approx(0.5)


class TestConfigSave:
    def test_save_persists_server_changes(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cm = ConfigManager(cfg_path)
        cm.server.port = 9999
        cm.server.host = "0.0.0.0"
        cm.save()

        cm2 = ConfigManager(cfg_path)
        assert cm2.server.port == 9999
        assert cm2.server.host == "0.0.0.0"

    def test_save_preserves_rules(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cm = ConfigManager(cfg_path)
        cm.add_rule(_rule())
        cm.save()

        cm2 = ConfigManager(cfg_path)
        assert len(cm2.rules) == 1
        assert cm2.rules[0].destinations[0].size.mode == "fixed"
        assert cm2.rules[0].destinations[0].size.value == pytest.approx(0.5)

    def test_save_atomic_no_tmp_file_left(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cm = ConfigManager(cfg_path)
        cm.save()
        assert not (tmp_path / "config.yaml.tmp").exists()


class TestConfigReload:
    def test_reload_picks_up_disk_changes(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cm = ConfigManager(cfg_path)
        assert cm.server.port == 9000

        cfg_path.write_text(
            "server:\n  host: localhost\n  port: 7777\n"
            "  heartbeat_interval: 30\n  account_update_interval: 15\n"
            "copy_rules: []\n",
            encoding="utf-8",
        )
        cm.reload()
        assert cm.server.port == 7777


class TestAddRule:
    def test_add_rule_in_memory(self, tmp_path):
        cm = ConfigManager(tmp_path / "config.yaml")
        cm.add_rule(_rule())
        assert len(cm.rules) == 1
        assert cm.rules[0].name == "Test Rule"

    def test_add_rule_persists(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cm = ConfigManager(cfg_path)
        cm.add_rule(_rule(name="Persisted"))

        cm2 = ConfigManager(cfg_path)
        assert len(cm2.rules) == 1
        assert cm2.rules[0].name == "Persisted"

    def test_add_multiple_rules(self, tmp_path):
        cm = ConfigManager(tmp_path / "config.yaml")
        cm.add_rule(_rule(rule_id="a", name="A"))
        cm.add_rule(_rule(rule_id="b", name="B"))
        assert len(cm.rules) == 2


class TestUpdateRule:
    def test_update_rule_by_id(self, tmp_path):
        cm = ConfigManager(tmp_path / "config.yaml")
        cm.add_rule(_rule(name="Old Name"))

        updated = _rule(name="New Name", enabled=False, magic_numbers=[12345])
        cm.update_rule(updated)

        assert len(cm.rules) == 1
        assert cm.rules[0].name == "New Name"
        assert not cm.rules[0].enabled
        assert cm.rules[0].magic_numbers == [12345]

    def test_update_rule_persists(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cm = ConfigManager(cfg_path)
        cm.add_rule(_rule(name="Old"))
        cm.update_rule(_rule(name="New"))

        cm2 = ConfigManager(cfg_path)
        assert cm2.rules[0].name == "New"

    def test_update_nonexistent_rule_appends(self, tmp_path):
        """update_rule on an unknown ID should act as add."""
        cm = ConfigManager(tmp_path / "config.yaml")
        cm.update_rule(_rule(rule_id="new_id", name="Appended"))
        assert len(cm.rules) == 1
        assert cm.rules[0].rule_id == "new_id"


class TestDeleteRule:
    def test_delete_removes_rule(self, tmp_path):
        cm = ConfigManager(tmp_path / "config.yaml")
        cm.add_rule(_rule(rule_id="r1"))
        cm.delete_rule("r1")
        assert len(cm.rules) == 0

    def test_delete_persists(self, tmp_path):
        cfg_path = tmp_path / "config.yaml"
        cm = ConfigManager(cfg_path)
        cm.add_rule(_rule(rule_id="r1"))
        cm.delete_rule("r1")

        cm2 = ConfigManager(cfg_path)
        assert len(cm2.rules) == 0

    def test_delete_nonexistent_id_is_noop(self, tmp_path):
        cm = ConfigManager(tmp_path / "config.yaml")
        cm.add_rule(_rule(rule_id="r1"))
        cm.delete_rule("does-not-exist")
        assert len(cm.rules) == 1

    def test_delete_only_removes_matching_id(self, tmp_path):
        cm = ConfigManager(tmp_path / "config.yaml")
        cm.add_rule(_rule(rule_id="r1", name="Keep"))
        cm.add_rule(_rule(rule_id="r2", name="Delete"))
        cm.delete_rule("r2")
        assert len(cm.rules) == 1
        assert cm.rules[0].rule_id == "r1"
