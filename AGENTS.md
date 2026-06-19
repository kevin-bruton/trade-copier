# AGENTS.md — Trade Copier

Guidelines for AI agents working in this repository.

---

## Project Overview

A Python TCP socket server that copies trades in real time between MetaTrader 4 and MetaTrader 5 terminals. Each terminal runs a lightweight Expert Advisor (EA) that connects as a socket client. The Python app applies configured copy rules and drives a dark-mode PyQt5 UI.

---

## Development Environment

**Language / runtime:** Python 3.13+  
**Dependency manager:** `uv` — do **not** use `pip` or `python -m venv` directly.

```powershell
# Install dependencies
uv sync

# Install PyQt5 (Windows requires a separate step due to wheel availability)
uv pip install PyQt5
```

> Never run `uv add PyQt5`. The `pyproject.toml` already pins `pyqt5-qt5==5.15.2` (the last Qt5 release with Windows wheels) via `[tool.uv] override-dependencies`. Using `uv add` bypasses this pin and breaks resolution on Windows.

Adding a new runtime dependency:
```powershell
uv add <package>
```

Adding a dev-only dependency:
```powershell
uv add --dev <package>
```

---

## Running Tests

```powershell
uv run pytest
```

Always run the full suite before and after making changes. All 121 tests must pass. There is no separate lint command.

```powershell
# Run a specific test file
uv run pytest tests/test_copier.py

# Run with short tracebacks
uv run pytest --tb=short
```

Tests live in `tests/`. pytest config is in `pyproject.toml` (`testpaths = ["tests"]`, `addopts = "-v"`).

---

## Project Structure

```
app/
  main.py              # entry point: wires config, server, copier, Qt event loop
  server.py            # TradeCopierServer — threaded TCP select() loop
  copier.py            # TradeCopier — all copy logic and message dispatch
  config.py            # ConfigManager — YAML load/save/reload + rule CRUD
  models.py            # all dataclasses (MTInstance, CopyRecord, CopyRule, …)
  ui/
    main_window.py     # MainWindow — toolbar, panels, process_events() timer
    instances_panel.py # QTableWidget showing connected terminals
    copies_panel.py    # QTableWidget showing copy records + "Clear closed"
    rules_panel.py     # QTableWidget + EditRuleDialog for copy rule management
    log_panel.py       # QPlainTextEdit event log (colour-coded, max 5 000 lines)

mql/
  TradeCopierEA.mq4    # MT4 Expert Advisor
  TradeCopierEA.mq5    # MT5 Expert Advisor
  Sockets.mqh          # TCP socket client header (existing, do not modify)
  SimpleJson.mqh       # flat JSON serialisation header (existing, do not modify)

tests/
  conftest.py          # fixtures: tmp_config, mock_server, logs, copier
                       # helpers:  reg_msg(), simple_rule()
  test_models.py
  test_config.py
  test_server.py
  test_copier.py
  test_integration.py  # real TCP socket tests
```

---

## Architecture: Thread Model

This is the most important design constraint in the codebase.

```
UI/main thread                    Server thread
──────────────────────────────    ──────────────────────────────
TradeCopier.handle_message()      select() loop
ConfigManager                     owns all socket objects
All Qt widgets                    _conn_sockets, _buffers
                                  drains _send_queue, _close_queue
```

**Rules:**
- `TradeCopier` and all UI code run **only** on the main thread.
- `TradeCopierServer` owns its sockets exclusively; no other thread touches them.
- Cross-thread communication uses two `queue.Queue` objects in `server.py`:
  - `_send_queue: Queue[(conn_id, bytes)]` — main thread enqueues, server drains.
  - `_close_queue: Queue[str]` — enqueued by `register_instance()` when a duplicate terminal_path is detected; server closes the old socket.
- The `on_event` callback (called by the server thread) should only put items onto a `queue.Queue` — never call `TradeCopier` methods or Qt APIs directly from it.
- `MainWindow.process_events()` is called by a `QTimer` every 50 ms; it drains the event queue and calls `copier.handle_message()` on the main thread.

---

## Key Module Details

### `app/copier.py` — TradeCopier

- **`handle_message(msg: dict)`** is the single entry point. Called from the main thread only.
- `msg` always contains `"_conn_id"` (injected by the server) and `"type"`.
- `REGISTER` and `_DISCONNECT` are handled directly before the `_handlers` dict lookup — they do not require a prior registration.
- All other message types look up `terminal_path` via `self._conn_to_path[conn_id]`. Messages from unknown connections are silently dropped.
- **`_handlers`** is built once in `__init__`, not per call.
- **`_prune_key_map(copy_id, source_path, source_ticket)`** removes a copy_id from `_source_key_to_copy_ids` and deletes the key if the list becomes empty. Call this whenever a copy reaches a terminal state (`"closed"` or `"error"`). This keeps snapshot reconciliation O(active positions).
- **`active_copies`** is never pruned — the UI reads it for display. Use the `_permanently_cleared` set in `copies_panel.py` to hide closed records without deleting them.

### `app/server.py` — TradeCopierServer

- **`send(terminal_path, msg_dict)`** — enqueues `(conn_id, json_bytes)` onto `_send_queue`. Returns `True` if the path is registered, `False` otherwise. Thread-safe.
- **`register_instance(terminal_path, conn_id)`** — if a different `conn_id` is already registered for this path, the old `conn_id` is queued on `_close_queue` for safe closure by the server thread. Thread-safe.
- **`connected_paths`** — property, returns a copy of the registered paths list. Thread-safe.
- **`running`** — property, returns `bool`. Use this instead of any stored flag in the UI.
- The `_DISCONNECT` synthetic event always includes `"terminal_path"`. It is `""` when a duplicate connection is closed by `_close_queue`; it is the real path for genuine socket drops.

### `app/config.py` — ConfigManager

- Config is auto-created with defaults if the YAML file does not exist.
- **`save()`** uses an atomic write: write to `.yaml.tmp` then `Path.replace()`.
- **`add_rule(rule)`**, **`update_rule(rule)`**, **`delete_rule(rule_id)`** all call `save()` automatically.
- **`reload()`** re-reads from disk; call this when the user clicks ↺ Reload Config.
- `get_rules()` → `list[CopyRule]`; `get_server_config()` → `ServerConfig`.

### `app/models.py` — Dataclasses

| Class | Key fields |
|---|---|
| `MTInstance` | `terminal_path` (primary key), `connected`, `display_name` (property) |
| `OpenPosition` | `ticket`, `symbol`, `direction` (`"buy"`/`"sell"`), `lots` |
| `CopyRecord` | `copy_id`, `status` (`"pending"/"open"/"pending_close"/"closed"/"error"`), `pending_close` |
| `CopyRule` | `rule_id`, `source_terminal_path`, `destinations: list[DestinationConfig]`, `magic_numbers`, `symbol_map` |
| `SizeConfig` | `mode` (`"fixed"/"proportional"/"account_percent"/"fixed_dollar"`), `value` |
| `ServerConfig` | `host`, `port`, `heartbeat_interval`, `account_update_interval` |

`CopyRecord.pending_close` is set when a source position closes before its destination copy has been filled. On `COPY_RESULT` success, if `pending_close` is true, immediately send `CLOSE_TRADE`.

---

## Message Protocol

All messages are flat JSON objects terminated with `\r\n`. No nested objects.

### EA → Python

| `type` | Key fields |
|---|---|
| `REGISTER` | `terminal_path`, `platform`, `broker`, `account`, `account_type`, `currency`, `leverage`, `balance`, `equity`, `margin`, `free_margin` |
| `ACCOUNT_UPDATE` | `balance`, `equity`, `margin`, `free_margin` |
| `POSITIONS_SNAPSHOT` | `count`, `positions` (semicolon-separated records, `\|`-delimited fields — see below) |
| `TRADE_OPENED` | `ticket`, `symbol`, `direction`, `lots`, `open_price`, `sl`, `tp`, `magic`, `open_time`, `comment` |
| `TRADE_CLOSED` | `ticket`, `symbol`, `direction`, `lots`, `close_price`, `profit`, `magic`, `close_time` |
| `COPY_RESULT` | `copy_id`, `success`, `ticket`, `open_price`, `error` |
| `CLOSE_RESULT` | `copy_id`, `success`, `close_price`, `error` |
| `HEARTBEAT` | `timestamp` |

### Python → EA

| `type` | Key fields |
|---|---|
| `ACK_REGISTER` | `terminal_path` (echoed) |
| `COPY_TRADE` | `copy_id`, `symbol`, `direction`, `lots`, `sl`, `tp`, `magic`, `comment` |
| `CLOSE_TRADE` | `copy_id`, `ticket` |
| `HEARTBEAT` | `timestamp` |

### `POSITIONS_SNAPSHOT` format

The `positions` field is a string of semicolon-separated records:
```
ticket|symbol|direction|lots|open_price|sl|tp|magic|open_time|comment;ticket|...
```
Parsed with `split("|", 9)` to preserve any `|` characters inside comments.

---

## Copy-Loop Prevention

Positions placed by the copier have a comment prefixed with `"COPY_"`. Both EAs and the Python copier use this prefix to avoid re-reporting these positions as new source trades.

- **MT4:** comment is `"COPY_" + copy_id[:26]` (31 chars total — MT4 hard limit).
- **MT5:** comment is `"COPY_" + copy_id` (37 chars, no truncation).

Do not change the `"COPY_"` prefix without updating both EAs and the Python copier.

---

## Test Infrastructure

### Fixtures (`tests/conftest.py`)

| Fixture | Type | Description |
|---|---|---|
| `tmp_config` | `ConfigManager` | Backed by a temp YAML file |
| `mock_server` | `MagicMock(spec=TradeCopierServer)` | `send()` returns `True` by default |
| `logs` | `list[tuple[str, str]]` | Captures `(level, message)` log calls |
| `copier` | `TradeCopier` | Wired to `tmp_config`, `mock_server`, `logs` |

### Helper factories

- **`reg_msg(conn_id, terminal_path, **kwargs)`** — builds a `REGISTER` message dict including `"_conn_id"`.
- **`simple_rule(source, dest, **kwargs)`** — builds a minimal `CopyRule` with one destination.

### Integration tests (`tests/test_integration.py`)

Real TCP sockets are used. Tests start a `TradeCopierServer` and connect actual `socket.socket` clients. The `_EA` helper class maintains a receive buffer so that multi-message exchanges are parsed correctly without consuming extra messages.

The `wait_for(condition, timeout=3.0, interval=0.05)` helper polls a condition with a timeout — use this for any assertion that depends on the server thread having processed a message.

---

## Coding Conventions

- **Type hints everywhere** — all function signatures and public attributes are fully typed; `from __future__ import annotations` is used in every module.
- **Dataclasses** for all data models; no dicts passed around as domain objects.
- **Handler methods** in `TradeCopier` take `(terminal_path: str, msg: dict)` — not `conn_id`.
- **Log levels:** `INFO`, `WARN`, `ERROR`, `TRADE`, `COPY`. Use `TRADE` for position open/close events on source terminals; use `COPY` for actions taken on destination terminals.
- **`_float(v)` / `_int(v)`** — module-level helpers in `copier.py` that safely convert EA string values; use these instead of bare `float()` / `int()`.
- **Do not store mutable state in UI widgets.** State lives in `TradeCopier.active_copies`, `TradeCopier.instances`, etc. UI panels call `refresh()` which reads from those dicts.
- **`import` statements at module level** — never inside functions or methods.

---

## Common Pitfalls

| Pitfall | Correct approach |
|---|---|
| Calling `TradeCopier` methods from the server's `on_event` callback | Put the event on a `queue.Queue`; drain it from the main thread |
| Mutating `active_copies` from the server thread | Always route through the event queue |
| Using `threading.Event().wait(n)` for sleeps in the server loop | Use `time.sleep(n)` |
| Adding `uv add PyQt5` | Use `uv pip install PyQt5`; the pin in `pyproject.toml` handles the rest |
| Forgetting to call `_prune_key_map()` when a copy reaches `"closed"` or `"error"` | `_source_key_to_copy_ids` grows unboundedly and breaks snapshot reconciliation |
| Deleting records from `active_copies` to "clear" them in the UI | Use `copies_panel._permanently_cleared` set instead |
| Backslashes in `config.yaml` paths | YAML requires `\\` — the ConfigManager writes them correctly; do not manually write single backslashes |

---

## MQL Files

The `mql/` folder contains MT4 and MT5 Expert Advisors. Key points when modifying them:

- `Sockets.mqh` and `SimpleJson.mqh` are shared headers — any change affects all EAs that use them. Do not modify them.
- Position tracking is done in an internal `trackedPositions[]` array in each EA. Positions with `comment` starting with `"COPY_"` are tracked but never emitted as `TRADE_OPENED`.
- MT5 has `OnTrade()` for near-zero latency detection; MT4 uses timer polling only.
- After modifying an EA, it must be recompiled in MetaEditor (F7) before the changes take effect in MetaTrader.
- The EA input parameter `ServerPort` must match `server.port` in `config.yaml`.
