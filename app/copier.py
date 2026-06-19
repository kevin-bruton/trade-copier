"""Trade copy logic.

Receives parsed JSON events from the TCP server (via the event queue) and
orchestrates copying trades between MetaTrader instances according to the
configured copy rules.

All public methods must be called from a single thread (the UI/main thread).
Thread-safety with the server is provided by queue.Queue in server.py.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Callable
from uuid import uuid4

from app.config import ConfigManager
from app.models import (
    CopyRecord,
    CopyRule,
    DestinationConfig,
    MTInstance,
    OpenPosition,
)
from app.server import TradeCopierServer


class TradeCopier:
    """Manages instances, positions, and copy records."""

    def __init__(
        self,
        config: ConfigManager,
        server: TradeCopierServer,
        on_log: Callable[[str, str], None],
    ) -> None:
        self._config = config
        self._server = server
        self._log = on_log  # on_log(level, message) — level is INFO/WARN/ERROR/TRADE/COPY

        # keyed by terminal_path
        self.instances: dict[str, MTInstance] = {}
        # terminal_path → {ticket: OpenPosition}
        self.source_positions: dict[str, dict[str, OpenPosition]] = {}
        # copy_id → CopyRecord  (never deleted; UI reads this for display)
        self.active_copies: dict[str, CopyRecord] = {}
        # (source_terminal_path, source_ticket) → [copy_id, …]
        # Only non-terminal copy_ids are kept here; pruned on "closed"/"error".
        self._source_key_to_copy_ids: dict[tuple[str, str], list[str]] = {}
        # conn_id → terminal_path (populated on REGISTER)
        self._conn_to_path: dict[str, str] = {}

        # Built once so handle_message() doesn't allocate a dict on every call.
        self._handlers = {
            "ACCOUNT_UPDATE":     self._on_account_update,
            "POSITIONS_SNAPSHOT": self._on_positions_snapshot,
            "TRADE_OPENED":       self._on_trade_opened,
            "TRADE_CLOSED":       self._on_trade_closed,
            "COPY_RESULT":        self._on_copy_result,
            "CLOSE_RESULT":       self._on_close_result,
            "HEARTBEAT":          self._on_heartbeat,
        }

    # ------------------------------------------------------------------
    # Main entry point — called from UI thread via event queue
    # ------------------------------------------------------------------

    def handle_message(self, msg: dict) -> None:
        """Dispatch one event dict (as produced by TradeCopierServer)."""
        msg_type = msg.get("type", "")
        conn_id = msg.get("_conn_id", "")

        if msg_type == "REGISTER":
            self._on_register(conn_id, msg)
            return

        if msg_type == "_DISCONNECT":
            # The server always includes terminal_path: "" for old duplicate
            # connections being closed, or the real path for genuine disconnects.
            self._on_disconnect(conn_id, msg.get("terminal_path", ""))
            return

        # All other message types require a registered (known) terminal_path
        terminal_path = self._conn_to_path.get(conn_id)
        if not terminal_path:
            return

        handler = self._handlers.get(msg_type)
        if handler:
            handler(terminal_path, msg)
        else:
            self._log("WARN", f"Unknown message type: {msg_type!r}")

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _on_register(self, conn_id: str, msg: dict) -> None:
        terminal_path: str = msg.get("terminal_path", "")
        if not terminal_path:
            self._log("WARN", f"REGISTER missing terminal_path from conn {conn_id}")
            return

        # Warn on duplicate registration (same path, possibly different connection)
        if terminal_path in self.instances and self.instances[terminal_path].connected:
            self._log("WARN", f"Duplicate REGISTER for {terminal_path}; replacing old connection")

        # Map conn_id ↔ terminal_path and notify server
        self._conn_to_path[conn_id] = terminal_path
        self._server.register_instance(terminal_path, conn_id)

        instance = MTInstance(
            terminal_path=terminal_path,
            platform=msg.get("platform", ""),
            broker=msg.get("broker", ""),
            account=msg.get("account", ""),
            account_type=msg.get("account_type", "demo"),
            currency=msg.get("currency", ""),
            leverage=_int(msg.get("leverage", 0)),
            balance=_float(msg.get("balance", 0.0)),
            equity=_float(msg.get("equity", 0.0)),
            margin=_float(msg.get("margin", 0.0)),
            free_margin=_float(msg.get("free_margin", 0.0)),
            connected=True,
            connected_at=datetime.now(),
            last_heartbeat=datetime.now(),
        )
        self.instances[terminal_path] = instance

        self._server.send(terminal_path, {"type": "ACK_REGISTER", "terminal_path": terminal_path})
        self._log(
            "INFO",
            f"Registered: {instance.display_name}  "
            f"{instance.platform} | {instance.broker} | {instance.account} | "
            f"{instance.account_type.upper()}",
        )

    def _on_account_update(self, terminal_path: str, msg: dict) -> None:
        inst = self.instances.get(terminal_path)
        if not inst:
            return
        inst.balance    = _float(msg.get("balance",     inst.balance))
        inst.equity     = _float(msg.get("equity",      inst.equity))
        inst.margin     = _float(msg.get("margin",      inst.margin))
        inst.free_margin = _float(msg.get("free_margin", inst.free_margin))

    def _on_positions_snapshot(self, terminal_path: str, msg: dict) -> None:
        """Reconcile server state against the EA's full position list.

        * Positions in the snapshot with no active CopyRecord → trigger copies.
        * Tickets absent from the snapshot but with active CopyRecords → trigger closes.
        """
        positions = _parse_positions_string(msg.get("positions", ""))
        snapshot: dict[str, OpenPosition] = {p.ticket: p for p in positions}

        # _source_key_to_copy_ids only holds non-terminal copy_ids (pruned on
        # "closed"/"error"), so an absent or empty entry means no active copy exists.
        for ticket, pos in snapshot.items():
            if not self._source_key_to_copy_ids.get((terminal_path, ticket)):
                self._log("INFO", f"Snapshot: new position {ticket} on {terminal_path!r}, treating as TRADE_OPENED")
                self._on_trade_opened(terminal_path, _position_to_msg(pos))

        # Disappeared positions → close all active copies for that ticket
        for (src_path, src_ticket), copy_ids in list(self._source_key_to_copy_ids.items()):
            if src_path != terminal_path or src_ticket in snapshot:
                continue
            for copy_id in copy_ids:
                record = self.active_copies.get(copy_id)
                if record:
                    self._log("INFO", f"Snapshot: {src_ticket} gone from {terminal_path!r}, treating as TRADE_CLOSED")
                    self._on_trade_closed(
                        terminal_path,
                        {
                            "ticket":      src_ticket,
                            "symbol":      record.symbol_source,
                            "direction":   record.direction,
                            "lots":        str(record.source_lots),
                            "close_price": "0",
                            "profit":      "0",
                            "magic":       str(record.magic),
                            "close_time":  "",
                        },
                    )

        self.source_positions[terminal_path] = snapshot

    def _on_trade_opened(self, terminal_path: str, msg: dict) -> None:
        ticket    = msg.get("ticket", "")
        symbol    = msg.get("symbol", "")
        direction = msg.get("direction", "buy")
        magic     = _int(msg.get("magic", 0))

        pos = OpenPosition(
            ticket=ticket,
            symbol=symbol,
            direction=direction,
            lots=_float(msg.get("lots", 0.0)),
            open_price=_float(msg.get("open_price", 0.0)),
            sl=_float(msg.get("sl", 0.0)),
            tp=_float(msg.get("tp", 0.0)),
            magic=magic,
            open_time=_parse_time(msg.get("open_time", "")),
            comment=msg.get("comment", ""),
        )

        self.source_positions.setdefault(terminal_path, {})[ticket] = pos
        self._log("TRADE", f"TRADE_OPENED  {symbol} {direction}  lots={pos.lots}  ticket={ticket}  src={terminal_path!r}")

        for rule in self._config.get_rules():
            if not rule.enabled:
                continue
            if rule.source_terminal_path != terminal_path:
                continue
            if not self._is_magic_allowed(rule, magic):
                continue
            for dest_cfg in rule.destinations:
                if dest_cfg.terminal_path == terminal_path:
                    continue   # prevent self-copy
                self._trigger_copy(rule, dest_cfg, terminal_path, pos)

    def _on_trade_closed(self, terminal_path: str, msg: dict) -> None:
        ticket = msg.get("ticket", "")
        self._log(
            "TRADE",
            f"TRADE_CLOSED  ticket={ticket}  "
            f"close_price={msg.get('close_price', '?')}  "
            f"profit={msg.get('profit', '?')}  src={terminal_path!r}",
        )

        key = (terminal_path, ticket)
        for copy_id in list(self._source_key_to_copy_ids.get(key, [])):
            record = self.active_copies.get(copy_id)
            if not record or record.status in ("closed", "error"):
                continue
            if record.dest_ticket is not None:
                self._trigger_close(record)
            else:
                record.pending_close = True
                self._log("COPY", f"Source closed before copy filled; copy_id={copy_id} will close when filled")

        # Remove from local position tracking
        if terminal_path in self.source_positions:
            self.source_positions[terminal_path].pop(ticket, None)

    def _on_copy_result(self, terminal_path: str, msg: dict) -> None:
        copy_id = msg.get("copy_id", "")
        success = msg.get("success", "false").lower() == "true"
        ticket  = msg.get("ticket", "")
        price   = msg.get("open_price", "?")
        error   = msg.get("error", "")

        record = self.active_copies.get(copy_id)
        if not record:
            self._log("WARN", f"COPY_RESULT for unknown copy_id={copy_id}")
            return

        if success:
            record.dest_ticket = ticket
            record.status = "open"
            self._log(
                "COPY",
                f"COPY filled  {record.symbol_dest}  {record.direction}  "
                f"lots={record.dest_lots}  ticket={ticket}  price={price}  dest={terminal_path!r}",
            )
            if record.pending_close:
                self._trigger_close(record)
        else:
            record.status = "error"
            record.error  = error
            self._log("ERROR", f"COPY_RESULT failed  copy_id={copy_id}  error={error!r}")
            self._prune_key_map(copy_id, record.source_terminal_path, record.source_ticket)

    def _on_close_result(self, terminal_path: str, msg: dict) -> None:
        copy_id     = msg.get("copy_id", "")
        success     = msg.get("success", "false").lower() == "true"
        close_price = msg.get("close_price", "?")
        error       = msg.get("error", "")

        record = self.active_copies.get(copy_id)
        if not record:
            self._log("WARN", f"CLOSE_RESULT for unknown copy_id={copy_id}")
            return

        if success:
            record.status    = "closed"
            record.closed_at = datetime.now()
            self._log(
                "COPY",
                f"CLOSE filled  {record.symbol_dest}  ticket={record.dest_ticket}  "
                f"price={close_price}  dest={terminal_path!r}",
            )
        else:
            record.status = "error"
            record.error  = error
            self._log("ERROR", f"CLOSE_RESULT failed  copy_id={copy_id}  error={error!r}")

        # Record has reached a terminal state; remove it from the lookup map so
        # _on_positions_snapshot and _on_trade_closed don't revisit it.
        self._prune_key_map(copy_id, record.source_terminal_path, record.source_ticket)

    def _on_heartbeat(self, terminal_path: str, msg: dict) -> None:
        inst = self.instances.get(terminal_path)
        if inst:
            inst.last_heartbeat = datetime.now()

    def _on_disconnect(self, conn_id: str, terminal_path: str) -> None:
        self._conn_to_path.pop(conn_id, None)
        if terminal_path and terminal_path in self.instances:
            self.instances[terminal_path].connected = False
            self._log("INFO", f"Disconnected: {self.instances[terminal_path].display_name!r}")

    # ------------------------------------------------------------------
    # Copy / close helpers
    # ------------------------------------------------------------------

    def _trigger_copy(
        self,
        rule: CopyRule,
        dest_cfg: DestinationConfig,
        source_terminal_path: str,
        position: OpenPosition,
    ) -> None:
        dest_terminal = dest_cfg.terminal_path
        dest_symbol   = self._apply_symbol_map(rule, position.symbol)
        dest_lots     = self._calc_lots(rule, dest_cfg, position, source_terminal_path)
        copy_id       = uuid4().hex

        record = CopyRecord(
            copy_id=copy_id,
            source_terminal_path=source_terminal_path,
            source_ticket=position.ticket,
            dest_terminal_path=dest_terminal,
            dest_ticket=None,
            symbol_source=position.symbol,
            symbol_dest=dest_symbol,
            direction=position.direction,
            source_lots=position.lots,
            dest_lots=dest_lots,
            magic=position.magic,
            sl=position.sl,
            tp=position.tp,
            status="pending",
            error="",
            opened_at=datetime.now(),
            closed_at=None,
        )
        self.active_copies[copy_id] = record

        key = (source_terminal_path, position.ticket)
        self._source_key_to_copy_ids.setdefault(key, []).append(copy_id)

        sent = self._server.send(
            dest_terminal,
            {
                "type":      "COPY_TRADE",
                "copy_id":   copy_id,
                "symbol":    dest_symbol,
                "direction": position.direction,
                "lots":      str(dest_lots),
                "sl":        str(position.sl),
                "tp":        str(position.tp),
                "magic":     str(position.magic),
            },
        )

        if sent:
            self._log(
                "COPY",
                f"COPY_TRADE sent  {position.symbol}→{dest_symbol}  "
                f"{position.direction}  lots={dest_lots}  "
                f"rule={rule.name!r}  dest={dest_terminal!r}",
            )
        else:
            self._log("WARN", f"Dest {dest_terminal!r} not connected; copy_id={copy_id} pending")

    def _trigger_close(self, record: CopyRecord) -> None:
        self._server.send(
            record.dest_terminal_path,
            {
                "type":    "CLOSE_TRADE",
                "copy_id": record.copy_id,
                "ticket":  str(record.dest_ticket),
            },
        )
        record.status = "pending_close"
        self._log(
            "COPY",
            f"CLOSE_TRADE sent  copy_id={record.copy_id}  "
            f"ticket={record.dest_ticket}  dest={record.dest_terminal_path!r}",
        )

    # ------------------------------------------------------------------
    # Calculation helpers
    # ------------------------------------------------------------------

    def _prune_key_map(self, copy_id: str, source_path: str, source_ticket: str) -> None:
        """Remove *copy_id* from the source-key lookup map.

        Called when a CopyRecord reaches a terminal state ("closed" or "error")
        so that _on_positions_snapshot only iterates active entries.
        """
        key = (source_path, source_ticket)
        ids = self._source_key_to_copy_ids.get(key)
        if ids is None:
            return
        try:
            ids.remove(copy_id)
        except ValueError:
            pass
        if not ids:
            del self._source_key_to_copy_ids[key]

    def _calc_lots(
        self,
        rule: CopyRule,
        dest_cfg: DestinationConfig,
        position: OpenPosition,
        source_terminal_path: str,
    ) -> float:
        # Per-magic override takes precedence over the default size config
        size = dest_cfg.size_by_magic.get(position.magic, dest_cfg.size)
        mode  = size.mode
        value = size.value

        source_inst = self.instances.get(source_terminal_path)
        dest_inst   = self.instances.get(dest_cfg.terminal_path)

        if mode == "fixed":
            lots = value

        elif mode == "proportional":
            lots = position.lots * (value / 100.0)

        elif mode == "account_percent":
            if (
                dest_inst is not None
                and source_inst is not None
                and source_inst.balance > 0
                and position.lots > 0
            ):
                # (dest_balance * pct/100) / (source_balance / source_lots)
                lots = (dest_inst.balance * value / 100.0) / (
                    source_inst.balance / position.lots
                )
            else:
                lots = value   # fall back to treating value as fixed

        elif mode == "fixed_dollar":
            # Approximate: treat size_value as a fixed lot size until pip-value
            # data is available (EA does its own broker-level clamping anyway).
            lots = value

        else:
            lots = value

        # Safety clamp; EA performs its own broker-level clamping on top
        lots = max(0.01, min(500.0, lots))
        return round(lots, 2)

    def _apply_symbol_map(self, rule: CopyRule, symbol: str) -> str:
        return rule.symbol_map.get(symbol, symbol)

    def _is_magic_allowed(self, rule: CopyRule, magic: int) -> bool:
        """Return True if *magic* passes the rule's magic_numbers filter."""
        return not rule.magic_numbers or magic in rule.magic_numbers


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _float(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _int(v: object) -> int:
    try:
        return int(float(v))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _parse_time(s: str) -> datetime:
    """Parse "YYYY-MM-DDTHH:MM:SS" produced by the EA, fall back to now()."""
    if s:
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass
    return datetime.now()


def _parse_positions_string(positions_str: str) -> list[OpenPosition]:
    """Deserialise the POSITIONS_SNAPSHOT positions field.

    Format: ``ticket|symbol|direction|lots|open_price|sl|tp|magic|open_time|comment``
    separated by ``;``.
    """
    if not positions_str:
        return []
    result: list[OpenPosition] = []
    for record in positions_str.split(";"):
        # Split into at most 10 parts so a comment containing "|" is preserved
        fields = record.split("|", 9)
        if len(fields) < 9:
            continue
        ticket, symbol, direction, lots, open_price, sl, tp, magic, *rest = fields
        open_time_str = rest[0] if rest else ""
        comment       = rest[1] if len(rest) > 1 else ""
        result.append(
            OpenPosition(
                ticket=ticket.strip(),
                symbol=symbol.strip(),
                direction=direction.strip(),
                lots=_float(lots),
                open_price=_float(open_price),
                sl=_float(sl),
                tp=_float(tp),
                magic=_int(magic),
                open_time=_parse_time(open_time_str.strip()),
                comment=comment.strip(),
            )
        )
    return result


def _position_to_msg(pos: OpenPosition) -> dict:
    """Convert an OpenPosition back to a message dict (for snapshot reconciliation)."""
    return {
        "ticket":     pos.ticket,
        "symbol":     pos.symbol,
        "direction":  pos.direction,
        "lots":       str(pos.lots),
        "open_price": str(pos.open_price),
        "sl":         str(pos.sl),
        "tp":         str(pos.tp),
        "magic":      str(pos.magic),
        "open_time":  pos.open_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "comment":    pos.comment,
    }
