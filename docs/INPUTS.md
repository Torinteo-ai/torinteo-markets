# Input files and the paper-trading CLI

Run commands from the project root with `python -m torinteo_paper`. Each command performs one operation and returns JSON; it does not start a scheduler or continuous trading process. All positions, fills and account balances belong to the simulated account in the selected SQLite database.

This document follows [cli.py](../torinteo_paper/cli.py), [inputs.py](../torinteo_paper/inputs.py), [models.py](../torinteo_paper/models.py) and [demo_data.py](../torinteo_paper/demo_data.py).

## 1. Run the supplied synthetic example

Every `demo-*.json` file below describes **invented TEST/USD prices, reviews and calendar evidence**. They exercise the software; they are not market observations or a historical performance backtest.

```sh
python -m torinteo_paper doctor

python -m torinteo_paper assemble \
  --candles examples/demo-candles.json \
  --context examples/demo-context.json \
  --out runtime/demo-assembled.json

python -m torinteo_paper replay \
  --snapshot runtime/demo-assembled.json \
  --db runtime/manual-demo.sqlite3 \
  --config examples/risk.paper.example.json

python -m torinteo_paper advance \
  --db runtime/manual-demo.sqlite3 \
  --bar examples/demo-entry-bar.json

python -m torinteo_paper advance \
  --db runtime/manual-demo.sqlite3 \
  --bar examples/demo-exit-bar.json

python -m torinteo_paper audit --db runtime/manual-demo.sqlite3
```

Use fresh output and database paths for a fresh experiment. `assemble` and `collect` create their output files exclusively and refuse to overwrite existing files. The supplied [demo-snapshot.json](../examples/demo-snapshot.json) can also be passed directly to `replay` with another new database instead of assembling it first.

The separate one-command demonstration is:

```sh
python -m torinteo_paper demo --db runtime/one-command-demo.sqlite3
```

`demo` requires a nonexistent database path. It demonstrates approval, a later simulated fill, restart from disk, conservative settlement when one candle crosses both stop and target, and rejection of a later candidate with nearby opposing resistance. A `LONG`/`SHORT` decision initially creates a **PENDING** paper order; inspect `advance` and `audit` to determine whether it filled, expired, was cancelled or closed.

## 2. Collect → assemble → evaluate current evidence

| Command | Input and purpose | External access |
| --- | --- | --- |
| `doctor` | Report package presence, whether a TypeSafe key exists, and local capability limits | None; presence does not verify connectivity |
| `collect` | Retrieve candles through an explicitly installed OpenBB and write a collection file | Calls the selected market-data provider |
| `assemble` | Join that collection to independently prepared quote, level, review and calendar context | None |
| `evaluate` | Validate a `market` snapshot against the real clock and the selected paper account | Optional configured model assessments, only after deterministic checks pass |
| `replay` | Evaluate a `synthetic` or `historical` snapshot using its recorded `as_of` | No model/provider calls |
| `advance` | Apply one closed execution bar to an existing account | None |
| `audit` | Read account cashflows, recorded orders and events | None |

An example collection command is shown below. Its dates are illustrative: choose the actual interval you need and a new output filename. Running it requests provider data, but does not place a trade.

```sh
python -m torinteo_paper collect \
  --symbol BTC/USD \
  --asset-class crypto \
  --timeframe 15m \
  --start-date 2026-10-18 \
  --end-date 2026-10-19 \
  --provider yfinance \
  --out runtime/btc-candles.json

python -m torinteo_paper assemble \
  --candles runtime/btc-candles.json \
  --context runtime/btc-context.json \
  --out runtime/btc-snapshot.json

python -m torinteo_paper evaluate \
  --snapshot runtime/btc-snapshot.json \
  --db runtime/market-paper.sqlite3 \
  --config examples/risk.paper.example.json
```

Prepare `btc-context.json` from actual evidence using the context schema below. Collection does **not** supply the bid/ask quote, identify major levels, create higher-timeframe reviews, or verify the economic calendar. Do not copy synthetic reviews into a market context or update timestamps to make old evidence appear current.

The collector currently supports `crypto`, `forex` and `equity`, using only the `yfinance` provider contract. It preserves explicit instrument and quote-currency identity: `BTC/USDT` is not silently replaced with `BTC/USD`. It can collect several intervals, but the setup assembler accepts **15m** candles. Its 4h, weekly and monthly aggregation is currently limited to crypto; other asset calendars need separate implementation.

Use `--timestamp-timezone` only when the provider's timestamp convention has been independently established. It is not a display-timezone preference. Naive timestamps without a supplied convention, and ambiguous or nonexistent local times, are rejected.

**A collection made now is not a point-in-time archive.** `collect` records actual request/receipt timestamps, marks `source_kind` as `market`, and sets `point_in_time_archive` to `false`, even when the requested candle dates are in the past. Do not relabel that file as historical. Input validation checks identities, values, time ordering and supplied provenance labels; it does not authenticate or certify the truth of a source, reference, review or archive declaration.

## 3. File contracts

All input files are UTF-8 JSON objects, at most 2 MiB. Duplicate keys and non-finite JSON constants are rejected. Numeric fields require JSON numbers, not strings or booleans. Timestamps must be ISO 8601 with an explicit timezone; `Z` is recommended and values are normalized to UTC. `schema_version` must be the integer `1`.

### Candle collection

See [demo-candles.json](../examples/demo-candles.json).

| Field | Contract |
| --- | --- |
| `schema_version` | `1` |
| `symbol` | Explicit canonical instrument, such as `BTC/USD`; must match context exactly |
| `asset_class` | Must match context; the collector supports `crypto`, `forex`, `equity` |
| `timeframe` | Must match context; `15m` for setup assembly |
| `source` | Nonempty provider label, e.g. `openbb:yfinance` |
| `source_kind` | `market`, `historical` or `synthetic`; must match context |
| `reference` | Nonempty bounded source reference |
| `as_of` | The candle selection cutoff |
| `observed_at` | When this collection was available |
| `point_in_time_archive` | Boolean; must be exactly `true` for historical assembly |
| `candles` | Array of the closed OHLCV bars defined below |

The collector also records `provider_symbol`, `source_timeframe`, `excluded_open_candles` and `excluded_incomplete_buckets`. These describe its normalization and exclusions; they do not fill missing evidence.

Each bar contains `open_time`, `close_time`, `open`, `high`, `low`, `close` and `volume`. Prices must be positive and finite; volume must be finite and nonnegative. High/low must contain open and close. A setup bar lasts exactly 900 seconds and closes no later than the decision time. The selected setup window must contain 5–1000 contiguous, unique bars in ascending order.

### Context

See [demo-context.json](../examples/demo-context.json). Context supplies the evidence that candle collection cannot infer.

| Field | Contract |
| --- | --- |
| `schema_version`, `symbol`, `asset_class`, `timeframe`, `source_kind` | Identity/version fields shared with the collection |
| `quote_currency` | Explicit uppercase quote currency matching the instrument and paper account; no automatic currency conversion |
| `as_of` | Decision evidence time; must be no earlier than collection `observed_at` |
| `reference` | Nonempty reference for the supplied context |
| `quote` | `{bid, ask, as_of, reference}`; positive prices, `ask >= bid`, timestamp no later than context `as_of` |
| `levels` | 2–200 reviewed level records, with unique IDs |
| `reviews` | Objects under each exact key: `1M`, `1w`, `1d`, `4h`, `1h` |
| `news` | `{status, as_of, source, events}` |

Every level has `id`, `price`, `kind`, `timeframe`, `known_at` and `reference`. `kind` is `support` or `resistance`; timeframe is `1M`, `1w` or `historical`. `known_at` cannot be later than the decision. The initial setup requires a reviewed monthly/historical entry level and a usable opposing target level; labelling a level does not establish that it was correctly identified.

Every timeframe review has `as_of`, `reference` and `bias`, where bias is `LONG`, `SHORT` or `NEUTRAL`. D1/4h/1h must agree in a non-neutral direction for entry. Monthly/weekly reviews are also required for context.

News status is `clear`, `blocked` or `unknown`. Only a sufficiently recent `clear` review can permit entry. Each event has `currency` (three uppercase letters), `impact` (`high`, `medium`, `low`), `starts_at`, `known_at` and `reference`. Event time can be future; knowledge of the event cannot postdate the decision. An empty event array means the supplied review reports no relevant events, not that the application independently checked the calendar.

Assembly enforces:

```text
collection.as_of <= collection.observed_at <= context.as_of
```

Every candle in the collection, including one outside the selected window, must already be closed by **`collection.as_of`**. A later context time cannot make prematurely included candles valid. `assemble --limit N` selects the last N candles, with N between 5 and 1000, then validates the assembled window. Nontrading gaps or missing bars are not automatically repaired.

### Assembled snapshot

See [demo-snapshot.json](../examples/demo-snapshot.json). The result contains:

```text
schema_version, symbol, asset_class, quote_currency, timeframe, as_of,
quote, levels, reviews, news, bars, source
```

`bars` is the selected `collection.candles` window. `source` replaces the separate collection/context labels:

| Snapshot source field | Comes from |
| --- | --- |
| `provider` | `collection.source` |
| `kind` | `collection.source_kind` |
| `reference` | `collection.reference` |
| `collected_at` | `collection.observed_at` |
| `context_reference` | `context.reference` |
| `point_in_time_archive` | Whether the collection flag is exactly `true` |

Direct snapshot input requires at least `source.provider`, `source.kind` and `source.reference`, alongside the other snapshot fields. Prefer assembly when combining separately supplied candles and context: it performs their additional identity and availability checks. The snapshot validator also accepts `index` and `commodity` as asset classes, but the current collector does not fetch them.

### Execution bar envelope

See [demo-entry-bar.json](../examples/demo-entry-bar.json) and [demo-exit-bar.json](../examples/demo-exit-bar.json). One `advance` call accepts one envelope:

```json
{
  "schema_version": 1,
  "symbol": "TEST/USD",
  "provenance": "synthetic",
  "timeframe": "15m",
  "observed_at": "2026-10-19T09:15:00Z",
  "bar": {
    "open_time": "2026-10-19T09:00:00Z",
    "close_time": "2026-10-19T09:15:00Z",
    "open": 100.23,
    "high": 100.8,
    "low": 100.1,
    "close": 100.5,
    "volume": 1200
  }
}
```

These numbers are synthetic. The envelope field is **`provenance`**, not `source_kind`. It must match the experiment and its orders. Execution supports **`1m`, `5m`, or `15m`**, with exact 60/300/900-second bar durations. Every submitted bar must already be closed by its observation time.

For `market` envelopes, the CLI rejects a future supplied `observed_at`, then uses the actual receipt clock. It does not backdate current market observations. For `historical` envelopes, the supplied `observed_at` also cannot exceed the actual current time. Synthetic fixtures may use arbitrary dates, provided their internal time ordering is valid. Feed the subsequent closed execution bars contiguously while a position is open; missing intervals are rejected. Repeating an identical bar is idempotent; changing a previously processed bar requires a separate experiment.

## 4. Timing, replay and settlement

`evaluate` accepts only `source.kind = market` and uses the actual clock. There is no `--now` override. The engine checks time again after any model work and before recording/reserving exposure. Slow research does not refresh old market evidence into a new valid candidate.

`replay` accepts only `synthetic` or `historical` snapshots and uses their `as_of` as the experiment time. A historical snapshot cannot have an `as_of` later than the actual current time; synthetic fixtures may use arbitrary dates. Replay does not call optional model providers. Historical studies require evidence that was actually available at each decision time, including levels, reviews and calendar knowledge; today’s retrieved data and date-only research are not substitutes.

Default snapshot freshness is 120 seconds, quote freshness is 60 seconds and news-review freshness is 1800 seconds. Higher-timeframe review age limits are 40 days for `1M`, 14 days for `1w`, 2 days for `1d`, 8 hours for `4h`, and 2 hours for `1h`. The London session filter treats 01:00 as an AUD/NZD high-impact-news review gate, 05:00 as pre-open review and 06:00 onward as setup time. A permitted session does not create a setup; this increment does not open an early-Asia trade through the news-only review gate.

**Approval and fill times are distinct.** Default expiry is `snapshot.as_of + 120 seconds`; `approved_at` records when approval was actually persisted. An eligible execution bar must satisfy:

```text
approved_at <= bar.open_time < expires_at
```

It cannot use the already-open candle’s old opening price after an approval arrives. For example, an approval at 09:00:03 cannot fill at the 09:00 candle open. A 1m bar starting at 09:01 may be eligible; the next 15m bar starting at 09:15 is too late for a two-minute approval. `advance` is called after the chosen bar closes. Exact boundary equality is supported in the synthetic example, where approval and the next bar opening share a recorded timestamp.

At fill time the ledger rechecks price movement, stop/target ordering, reward/risk and available exposure; an approval can therefore be cancelled or filled at a reduced quantity. Fees and adverse slippage apply. Stop and target both crossed in one OHLC bar settle at the stop first. A gap through the stop is recorded at the worse opening price, with slippage. These are explicit simulation assumptions.

**Process events chronologically across all symbols in a database.** Do not replay a full day of one instrument and then insert an earlier event for another: both share account exposure and loss limits. Observation time and event time cannot move backwards. Interleave decisions and execution bars in chronological order, and settle the relevant closed bars before evaluating later snapshots.

## 5. Risk configuration and optional assessments

Use [risk.paper.example.json](../examples/risk.paper.example.json) for a new experiment. A configuration file may provide a subset of `RiskConfig` fields; omitted values take these defaults. Unknown fields are rejected. The values are engineering defaults for paper experiments, not validated profitability settings.

| Setting | Default |
| --- | --- |
| `mode` | `paper`; there is no live mode |
| `account_currency` / `risk_day_timezone` | `USD` / `Europe/London` |
| `initial_equity` | `10000.0` |
| `risk_fraction` / `daily_loss_fraction` | `0.005` / `0.02` |
| `max_notional_fraction` / `max_total_notional_fraction` | `0.10` / `0.30` |
| `max_open_positions` | `3`; one position per symbol |
| `min_reward_risk` | `2.0`, after configured friction |
| `fee_bps` / `slippage_bps` | `10.0` / `5.0` each side |
| `max_spread_bps` | `25.0` |
| `level_tolerance_bps` / `stop_buffer_bps` | `20.0` / `10.0` |
| `max_chase_tolerances` | `3.0` |
| `max_snapshot_age_seconds` / `max_quote_age_seconds` | `120` / `60` |
| `max_news_age_seconds` / `news_window_minutes` | `1800` / `30` |
| `ticket_ttl_seconds` | `120` |
| `enforce_session` | `true` |
| `jev_mode` / `research_mode` | `disabled` / `disabled` |
| `jev_min_confidence` | `0.60` |

The two provider modes can each be `disabled`, `shadow` or `required`. Shadow assessments are recorded without a provider veto; required assessments must be valid and supportive. Required Jev needs a `pass` above the configured confidence threshold. Required TradingAgents needs a bullish stance for LONG or bearish stance for SHORT. Missing/conflicting required assessments block new entries. Providers cannot override deterministic rejections and do not block protective settlement.

Only `evaluate` constructs optional assessment clients, and only an otherwise valid candidate reaches them. `replay`, `advance`, `audit`, `assemble`, `demo` and `doctor` do not call them. In particular, a replay with a provider marked `required` has no current provider available and cannot use that absence to approve an entry.

Jev uses `TYPESAFE_API_KEY` from the process environment. TradingAgents additionally requires an explicit settings file passed with **`--research-config`**; it does not silently use the upstream paid-model defaults. That JSON object must specify nonempty `llm_provider`, `quick_think_llm` and `deep_think_llm` values corresponding to providers/models you have selected and configured.

```sh
python -m torinteo_paper evaluate \
  --snapshot runtime/current-snapshot.json \
  --db runtime/research-paper.sqlite3 \
  --config runtime/research-risk.json \
  --research-config runtime/research-provider.json
```

For this command to request research, set `research_mode` to `shadow` or `required` in `research-risk.json`. Merely passing `--research-config` does not enable it. Current TradingAgents research supports equity/crypto, uses a date-only upstream interface and is recorded at completion; it is not used as historical intraday evidence. Keep credentials in the runtime environment or the provider's supported secret configuration, never in committed example files or evidence. See [JEV.md](JEV.md) and [UPSTREAMS.md](UPSTREAMS.md).

## 6. Database reuse and audit

An existing database stores its exact `RiskConfig`, provenance and executor. Subsequent commands use that stored configuration when `--config` is omitted. Supplying a different configuration to the same database is rejected: **choose a new database for a new configuration**. Keep market, historical and synthetic experiments separate. `advance` and `audit` require an existing database and never silently create an account.

`audit` reports cash-based paper equity and realised cashflows, orders and recorded events. It is not a live portfolio valuation. Its optional `--at` sets the account cashflow cutoff only; the orders and events arrays still show the full recorded history. It is not a historical reconstruction of every displayed field.

Commands print JSON to stdout. Command/input errors print an error object to stderr and exit with status 2. A valid `NO_TRADE` decision is an ordinary result, not a shell error: read `call`, `reason_codes`, `assessments` and the recorded order status instead of treating process success as entry approval.
