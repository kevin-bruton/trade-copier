# Trade Copier — End-to-End Test Plan

## Scope

This document describes end-to-end (E2E) tests for the complete Trade Copier system:
a Python TCP server, one or more **source** MetaTrader terminals running
`TradeCopierEA`, and one or more **destination** terminals.  
It complements the automated Python unit/integration test suite in `tests/`.

---

## 1. Environment Requirements

| Component | Requirement |
|-----------|-------------|
| Python server | `uv run trade-copier` or `uv run python -m app.main` |
| Source terminal | MetaTrader 4 **or** MetaTrader 5 with `TradeCopierEA` compiled and loaded on a chart |
| Destination terminal | MetaTrader 4 **or** MetaTrader 5 with `TradeCopierEA` loaded |
| `config.yaml` | `source_terminal_path` must match `TerminalPath()` reported by the source EA |
| Network | All terminals reachable at the server's `host:port` (default `localhost:9000`) |

### Minimal setup (single machine)

```
[MT4 source]──TCP──[Python server:9000]──TCP──[MT5 destination]
```

Both terminals and the Python process run on the same Windows machine.  
The server's `host` can stay as `localhost`.

---

## 2. Test Cases

### TC-001 · Source EA Connection

**Goal:** Verify the source terminal registers and appears in the UI.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Start the Python server (click **▶ Start Server** in the UI) | Status shows green ● Running on localhost:9000 |
| 2 | Open MT4/MT5 chart and attach `TradeCopierEA` | EA shows "Connecting…" then "Connected" on chart label |
| 3 | Check **Connected Instances** panel | One row appears with green dot, correct Directory / Broker / Account / Balance |
| 4 | Check Event Log | `[INFO] Registered: <name>  MT4 \| <broker> \| <account> \| DEMO` |

---

### TC-002 · Destination EA Connection

**Goal:** Verify a second terminal registers independently.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Attach `TradeCopierEA` to a chart in the destination terminal | EA connects |
| 2 | Check **Connected Instances** panel | Two rows, both green |
| 3 | Check Event Log | Second `[INFO] Registered:` line appears |

---

### TC-003 · Copy Rule Creation

**Goal:** Verify a rule is added and saved correctly.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Click **+ Add Rule** in the Copy Rules panel | Edit Rule dialog opens |
| 2 | Fill in: Name = "E2E Test Rule", Source = source terminal, Destination = dest terminal, Size mode = Fixed 0.1 lots, Enabled = ✓ | — |
| 3 | Click **Save** | Rule appears in the Rules panel |
| 4 | Click **💾 Save Config** | `config.yaml` updated on disk; re-opening the app shows the rule |

---

### TC-004 · Trade Open — Basic Copy

**Goal:** Opening a trade on the source triggers a copy on the destination.

**Preconditions:** TC-001, TC-002, TC-003 passed; rule enabled.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Open a trade manually on the **source** terminal (e.g. BUY 0.1 EURUSD) | — |
| 2 | Check Event Log within 1–2 s | `[TRADE] TRADE_OPENED  EURUSD buy  lots=0.1  ticket=<N>` |
| 3 | Check Event Log | `[COPY] COPY_TRADE sent  EURUSD→EURUSD  buy  lots=0.1` |
| 4 | Check **destination terminal** | A new BUY EURUSD position appears (comment starts with `COPY_`) |
| 5 | Check Event Log | `[COPY] COPY filled  EURUSD  buy  lots=0.1  ticket=<M>` |
| 6 | Check **Active Copies** panel | One row: status = **open** (green background) |

---

### TC-005 · Trade Close — Basic Close Copy

**Goal:** Closing the source trade closes the destination copy.

**Preconditions:** TC-004 complete; copy is open.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Close the source trade | — |
| 2 | Check Event Log | `[TRADE] TRADE_CLOSED  ticket=<N>` then `[COPY] CLOSE_TRADE sent` |
| 3 | Check destination terminal | The copied position is closed |
| 4 | Check Event Log | `[COPY] CLOSE filled  EURUSD  ticket=<M>  price=…` |
| 5 | Check Active Copies panel (Show closed ON) | Status = **closed** (grey background) |

---

### TC-006 · Magic Number Filter

**Goal:** Trades from filtered-out magic numbers are not copied.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Edit the rule; set Magic numbers = `[12345]` | Rule saved |
| 2 | Open a trade with magic = 0 on the source | Trade appears in source terminal |
| 3 | Check Active Copies panel | No new copy record created |
| 4 | Check destination terminal | No new position |
| 5 | Open a trade with magic = 12345 on the source | — |
| 6 | Check Active Copies panel | Copy record created, status = open |

---

### TC-007 · Symbol Map

**Goal:** Source symbol is remapped to destination symbol before the copy is sent.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Edit the rule; add symbol map `XAUUSD → GOLD` | Rule saved |
| 2 | Open a XAUUSD trade on source | — |
| 3 | Check Event Log | `COPY_TRADE sent  XAUUSD→GOLD` |
| 4 | Check destination | Position opened on `GOLD`, not `XAUUSD` |
| 5 | Check Active Copies panel | Symbol\_dest column shows `GOLD` |

---

### TC-008 · Proportional Lot Size

**Goal:** Lot size is scaled proportionally to source size.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Edit the rule; size mode = Proportional 50% | Rule saved |
| 2 | Open a 1.0 lot trade on source | — |
| 3 | Check Active Copies panel | Dest Lots = 0.50 |
| 4 | Check destination terminal | Position size = 0.50 lots |

---

### TC-009 · Reconnection — Source Reconnects

**Goal:** If the source EA disconnects and reconnects, positions are reconciled
correctly via POSITIONS_SNAPSHOT.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Open 2 trades on source; verify both copied | — |
| 2 | Remove `TradeCopierEA` from source chart (EA disconnects) | Source row shows grey dot in UI |
| 3 | Re-attach `TradeCopierEA` to source chart | EA reconnects; POSITIONS_SNAPSHOT sent |
| 4 | Check Event Log | `Snapshot: new position … treating as TRADE_OPENED` (if not already copied) |
| 5 | No duplicate copies created | Active Copies panel still shows 2 records, not 4 |

---

### TC-010 · Source Closes Before Copy Fills (Pending Close)

**Goal:** If the source closes a trade before the COPY_RESULT is received,
the destination copy is closed immediately upon fill.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Temporarily disconnect destination terminal to delay the fill | — |
| 2 | Open a trade on source | Copy is in **pending** state |
| 3 | Close the trade on source immediately | Event Log: "Source closed before copy filled; copy_id=… will close when filled" |
| 4 | Reconnect destination terminal | COPY_RESULT arrives → CLOSE_TRADE sent immediately |
| 5 | Check Active Copies | Status transitions pending → open → pending_close → closed |

---

### TC-011 · Duplicate Connection (EA Restart)

**Goal:** When the EA restarts with the same `terminal_path`, the old connection
is safely replaced without affecting other terminals.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Remove and re-attach `TradeCopierEA` on source chart in quick succession | — |
| 2 | Check Event Log | `[WARN] Duplicate REGISTER …; replacing old connection` |
| 3 | Check Instances panel | Source still shows one row (green dot) |
| 4 | Destination is unaffected | Destination row remains green |

---

### TC-012 · Multiple Destinations (Fan-Out)

**Goal:** One source rule with two destinations sends independent copies to each.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Connect three terminals: one source, two destinations | All three rows in Instances panel |
| 2 | Edit rule to include both destinations | Rule shows two destination entries |
| 3 | Open a trade on source | — |
| 4 | Check Active Copies | Two copy records created (one per destination) |
| 5 | Check both destination terminals | Each has a new position |

---

### TC-013 · Config Hot-Reload

**Goal:** Clicking **↺ Reload Config** picks up external edits to `config.yaml`
without restarting the server.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Edit `config.yaml` directly in a text editor (e.g. disable the rule) | — |
| 2 | Click **↺ Reload Config** in the UI | Event Log: "Configuration reloaded from disk" |
| 3 | Open a trade on source | No copy created (rule disabled) |

---

### TC-014 · Server Start / Stop

**Goal:** The server can be stopped and restarted without losing config or copy history.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Click **⏹ Stop Server** | Status shows red ● Stopped; EAs disconnect |
| 2 | Click **▶ Start Server** | Status shows green ● Running; EAs reconnect |
| 3 | Open a trade on source | Copy is created normally |

---

## 3. Performance / Stress Checks (Manual)

| Test | Method | Pass criteria |
|------|--------|---------------|
| Latency | Open a trade; measure time from source open to destination fill | < 500 ms on local machine |
| 10 rapid trades | Open 10 trades in succession using a script or EA loop | All 10 copied; no duplicate or missed copies |
| Long-running stability | Leave server running for 1 hour with heartbeats active | No crashes; heartbeat timestamps advance every 30 s |

---

## 4. Known Limitations

- **Lot clamping** is performed by the EA at the broker level on top of the
  Python-level clamp (min 0.01, max 500).  The E2E tests should verify the
  broker accepts the computed lot size without modification.
- **Symbol availability**: the destination broker must offer the mapped symbol
  (TC-007); if not, the EA will log an error and the copy record status becomes
  `error`.
- **MT4 comment length**: MT4 truncates comments to 31 characters.  The copy
  comment format `COPY_<copy_id[:26]>` is designed to fit within this limit.

---

## 5. Automated E2E Harness (Future Work)

A fully automated harness would:

1. Launch headless MT4/MT5 using [wine](https://www.winehq.org/) or a Windows
   CI runner.
2. Use the [MetaTrader Python API](https://pypi.org/project/MetaTrader5/) to
   open/close trades programmatically on the source terminal.
3. Assert the destination terminal's open positions list via the same API.
4. Report pass/fail per test case.

The Python-layer integration tests in `tests/test_integration.py` cover
everything up to the TCP protocol boundary and serve as a fast, always-runnable
subset of this plan.
