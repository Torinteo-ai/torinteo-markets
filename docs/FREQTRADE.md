# Freqtrade dry-run approval boundary

**Status: implemented and locally tested integration boundary. It is not a standalone Freqtrade strategy or a verified running bot.** The executable demonstration in this repository uses the native paper ledger. The Freqtrade files require a reviewed host strategy and lifecycle work before connecting them to an upstream dry-run process.

`torinteo_paper/adapters/freqtrade.py` uses only the Python standard library. It validates approved entry tickets, reads bounded local files, and atomically records an entry confirmation claim. `integrations/freqtrade/TorinteoPaperStrategy.py` contains `TorinteoPaperConfirmationMixin`; despite the filename, it deliberately does not define a runnable `TorinteoPaperStrategy` or subclass `IStrategy`.

No exchange orders, bot API requests, private keys, optional package installation, or upstream Freqtrade execution were used to build or verify this boundary.

## One executor owns each decision

Select the executor when evaluating an observation. The native engine defaults to `executor="native"`. An approved decision evaluated with `executor="freqtrade_dry_run"` is persisted as an external approval and does not create a native pending trade. Export requires that original persisted executor, and the same decision cannot switch executors within that ledger.

The relevant application calls, after initializing the engine and collecting valid current market evidence, are:

```python
decision = engine.run(snapshot, executor="freqtrade_dry_run")
path = ticket_directory / f"{decision['decision_id']}.json"
engine.export_freqtrade_ticket(decision, path)
```

Export applies only to eligible, still-current, market-sourced crypto spot LONG approvals. An exported ticket is an entry authorization, not an order or a fill. Separate ledgers do not coordinate with each other; use one authoritative ledger and one persistent claims directory for this handoff.

## Ticket contract

The producer creates the file once as `<decision_id>.json`. It must not overwrite or refresh an existing approval. Set `TORINTEO_TICKET_DIR` to its absolute directory in the consuming process. The directory and its files must not be writable by other users. Keep `claims.sqlite3` in that directory across restarts, and keep it on a local filesystem with working SQLite locking and durability.

Version 1 accepts exactly the following fields; unsupported extra fields and missing fields fail validation.

| Field | Requirement |
| --- | --- |
| `schema_version` | Integer `1`; booleans are rejected |
| `mode` | `paper` |
| `executor` | `freqtrade_dry_run` |
| `provenance` | `market`; `synthetic` and `historical` are rejected |
| `decision_id` | 64 lowercase hexadecimal characters; matches the filename |
| `symbol` | Exact canonical spot pair such as `BTC/USDT`; both assets uppercase, no futures settlement suffix |
| `side` | `LONG`; the Freqtrade callback may use `long` |
| `issued_at`, `expires_at` | Timezone-aware ISO timestamp strings; `issued_at <= now < expires_at`, lifetime greater than zero and at most 120 seconds |
| `signal_bar_close` | Timezone-aware ISO timestamp of the signal candle close, at or before issuance |
| `entry`, `stop`, `target` | Finite positive numbers with `stop < entry < target` |
| `quantity`, `notional`, `risk_cash` | Finite positive base-asset quantity and quote-currency budgets |
| `fee_bps`, `slippage_bps` | Finite numbers from 0 through 1,000, consistent with the native cost model |
| `entry_tolerance_bps` | Numeric `10` |

The directory is a trusted local handoff, not a signature or authentication service. Checking a `market` label cannot prove how data was obtained. Only the native producer should write tickets; changing fixture provenance text is not a way to obtain market evidence. Reads reject symlinks, nonregular files, duplicate JSON keys, mismatched identifiers, and files larger than 16 KiB. Ticket validation also rejects nonfinite numbers, numeric strings, and booleans used as numbers.

## The final entry check

The public function is:

```python
validate_ticket(
    ticket,
    pair=pair,
    side=side,
    rate=proposed_price,
    amount=base_asset_quantity,
    now=current_time,
    dry_run=True,
    runmode="dry_run",
)
```

Both configuration signals are required: `dry_run is True` and `runmode` resolves to `dry_run` (either the exact string or the upstream enum's value). Live, backtest, hyperopt, unknown modes, and truthy substitutes are rejected.

The proposed price must remain strictly between the approved stop and target and within 10 basis points of the approved entry. Quantity may decrease but cannot exceed the approval. At the actual proposed price, the following two amounts must still fit the approved budgets:

```text
notional = amount * rate
cash_risk = amount * (rate - stop + 2 * rate * (fee_bps + slippage_bps) / 10000)
```

These are the same entry-based round-trip cost assumptions used by the native gate. Floating point comparisons permit one representable float of arithmetic rounding; there is no monetary allowance. A price increase inside the 10 bp band can still be rejected because it exceeds notional or risk. A broker's quantity rounding or minimum stake must never increase an order past either budget.

`TicketStore.claim()` repeats validation and commits the decision identifier, exact approved terms, actual proposed quantity/price, and confirmation time to SQLite before returning `True`. The primary key makes duplicate confirmations fail across processes and restarts. An approval remains consumed even if order submission later fails or the order is canceled. This intentionally sacrifices retry convenience. A confirmation record is not an execution receipt; do not delete it to retry.

## What the mixin implements

The host must use a normal latest-candle entry signal with an `enter_tag` of `tm:<decision_id>`. The mixin reads only the latest analyzed 15-minute candle and requires its `enter_long`, tag, absence of an opposing exit signal, and close time to match the ticket. The candle must be closed and belong to the current 15-minute interval. A current ticket cannot be applied across an old candle series. Freqtrade's callback signature and analyzed-data access are documented in its [strategy callbacks](https://www.freqtrade.io/en/stable/strategy-callbacks/) and [advanced strategy documentation](https://www.freqtrade.io/en/stable/strategy-advanced/).

The entry callback additionally requires spot trading, limit entry orders, `max_open_trades=1`, no shorts, no position adjustment, no force-entry capability, and a disabled API server. Missing tickets, invalid tags, expired approvals, bad data, unsupported configuration, and SQLite errors deny entry.

Approved terms remain in the claims database after the entry ticket expires or its file disappears. `torinteo_approved_terms(trade.enter_tag)` exposes those original terms to the host's protective callbacks. `check_entry_timeout()` requests cancellation of unfilled entries when the ticket has expired or its retained terms are missing. It operates on Freqtrade's callback schedule; it is not a guarantee that cancellation occurs precisely at the expiry timestamp.

`confirm_trade_exit()` always permits host-generated exits, including stop-loss and emergency exits. Entry expiry, missing approval files, and an entry-mode error never veto a protective exit. This callback does not generate exits by itself.

## Host work required before starting Freqtrade

Place `TorinteoPaperConfirmationMixin` before the host's `IStrategy` class in its inheritance order, and preserve the entry and exit boundary behavior. This repository does not provide a verified host implementation. The remaining work is substantive:

1. Generate normal entry flags only for the matching latest closed candle, load the intended approval, and size and price the proposed order without exceeding its quantity or budgets after exchange precision and minimum-stake rules.
2. Persist the decision tag and original ticket terms against the actual trade when an entry fills. Handle partial fills, canceled orders, replacements, restarts, and the distinction between an accepted confirmation and a filled trade. Upstream supports per-trade JSON custom data, documented under [persistent storage](https://www.freqtrade.io/en/stable/strategy-advanced/#storing-information-persistent).
3. Wire the approved absolute stop and target into verified native protective logic. Maintain a native stop-loss throughout the trade and on restart, independently of ticket freshness or model availability. The generic `-0.01` stop-loss in the configuration template is an emergency example and does not implement each ticket's exact stop. Retaining stop/target numbers in SQLite does not enforce them.
4. Reconcile the external dry-run account, open and pending orders, position quantities, realized and unrealized P&L, fees, equity, and daily-loss state back into the approval process. Revalidate those limits before each entry. Missing protective terms or divergent state must stop new entries while existing protection remains active.
5. Pin and exercise an upstream Freqtrade version in dry-run mode, including the actual callback order, fill persistence, stop/target behavior, expiry cancellation, and restart recovery. The local callback fixtures are not a substitute for this lifecycle verification.

**External-account daily-loss and exposure reconciliation is incomplete.** Native paper-ledger limits describe that ledger; they do not establish the state of an independent Freqtrade wallet. `max_open_trades=1` restricts concurrency, but cannot replace account reconciliation or a daily-loss gate. Until the host and reconciliation work is verified, use the native local paper demonstration.

## Configuration and verification

`integrations/freqtrade/config.example.json` is intentionally a template with a placeholder strategy, placeholder exchange, stopped initial state, dry-run enabled, one maximum open trade, empty exchange keys, force entry disabled, and API/Telegram integrations disabled. Filling a placeholder alone does not complete the missing host lifecycle. No live-mode adapter or force-entry API is supplied.

Run the local boundary verification from the repository root:

```bash
python -m unittest tests.test_freqtrade_boundary -v
python -m py_compile torinteo_paper/adapters/freqtrade.py integrations/freqtrade/TorinteoPaperStrategy.py
```

The 32 boundary tests cover schema and numeric rejection, actual-price risk and notional, clock edges, wrong mode/provenance, local file safety, durable and concurrent one-time claims, matching latest-candle confirmation, retained stop/target terms, expiry cancellation requests, and unconditional exit permission. Test tickets and prices are fabricated contract fixtures. These checks establish local boundary behavior only; no full upstream bot, exchange feed, order lifecycle, or performance claim was verified.
