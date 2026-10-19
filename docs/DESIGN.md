# Torinteo Markets design

## Intended outcome and repository

Provide an inspectable research-to-paper-trading workflow with a runnable native
simulator, explicit data contracts and a durable audit. Synthetic demonstrations
must remain labelled; they do not represent market observations or an operator's
real holdings.

The repository is [Torinteo-ai/torinteo-markets](https://github.com/Torinteo-ai/torinteo-markets),
GitHub ID `1238002647`. Product name: **Torinteo Markets**. It was selected while
empty and named KYRONYX. The initial source is published, and the rename was
confirmed on 19 October 2026. Implementation verification and remaining provider
activation limits are recorded in [VERIFICATION.md](VERIFICATION.md).

## Implemented workflow

The native path has five responsibilities:

1. Collect or load explicit observations and combine them with operator-supplied
   quote, levels, timeframe reviews and news context.
2. Validate the snapshot and evaluate the deterministic breakout/retest,
   session, freshness, opposing-level and paper-risk constraints.
3. Optionally request research or a typed Jev assessment after a deterministic
   candidate exists. Required assessment failures block the candidate; shadow
   assessments are recorded without deciding the entry.
4. Recheck deterministic constraints under the ledger's write transaction and
   record a pending native paper order when approved.
5. Consume later execution bars, simulate fills and protective exits, and retain
   timed cashflows and events for inspection and restart recovery.

Approval is not a fill. A model assessment is not authority to bypass the
deterministic gate. The native implementation contains no live broker client.

## Strategy boundary

The first executable setup is **breakout, acceptance, then a later retest and
hold of an operator-reviewed monthly or historical reaction level**. Setup
evaluation uses contiguous, already-closed 15-minute bars. The rule requires two
closes beyond the configured level zone before the final retest candle; the
acceptance and retest cannot be the same candle. The long and short cases are
handled symmetrically in the simulator.

Monthly, weekly, daily, four-hour and hourly reviews must be supplied with source
references and timestamps. Daily, four-hour and hourly biases must agree and be
directional. A reviewed weekly level can be used as opposing resistance/support
for the target, while an entry requires a monthly or historical level. Entries
that chase too far from the reviewed level, or lack enough room before opposing
support/resistance after friction, are rejected.

The operator supplies these reviews and levels. The program validates their
shape, timing and consistency; it does not independently open a cited chart and
prove the operator's interpretation. It does not automatically discover every
major reaction level. A rejection followed by a structure break is a separate,
unimplemented setup.

### Session scope

Session classification uses `Europe/London`:

| Local time | Scope |
| --- | --- |
| 01:00 hour | High-impact AUD/NZD news gate for affected pairs only; otherwise no scan. It is not permission for a complete market scan or an automatic entry. |
| Before 05:00 outside that gate | No normal setup scan. |
| 05:00 hour | Pre-open review only. |
| From 06:00 | Setup evaluation may proceed if every other condition passes. |

This is classification inside evaluation, not an installed scheduler, chart
watcher, exchange-session calendar or background service. A configured news
window still blocks entries around relevant high-impact events.

## Evidence contract

All timestamps must include a time zone and are normalized to UTC. Calendar
semantics such as a London risk day remain explicit. The authoritative runtime
validators are in `torinteo_paper/models.py`; the following tables describe the
JSON files exposed by the command layer.

### Collection and operator context

`collect` returns explicit identity, timeframe, provider provenance, closed
`candles`, `source_kind="market"`, an `observed_at` timestamp recorded at actual
retrieval completion, and `point_in_time_archive=false`. Downloading prices for
earlier dates is still a current retrieval; it does not prove that the retrieved
values were available without later adjustment at those earlier times.

`assemble` combines that packet with a context object containing:

| Field | Required content |
| --- | --- |
| `schema_version` | Integer `1`. |
| `symbol`, `asset_class`, `timeframe` | Must match the collected identity and the supported 15-minute setup interval. |
| `as_of` | Aware snapshot timestamp no earlier than collection `observed_at`. |
| `source_kind`, `reference` | Matching collection provenance and an explicit source reference. |
| `quote_currency` | The actual instrument quote currency; it must match the configured paper account currency. |
| `quote` | Positive `bid`, `ask`, aware `as_of`, and a source `reference`. |
| `levels` | The reviewed level records described below. |
| `reviews` | The five required timeframe reviews described below. |
| `news` | A dated news/calendar review and known event records. |

Assembly does not create a quote from a candle close, invent a spread, mark news
clear, infer higher-timeframe bias, or manufacture source references. It uses
the latest available setup bars up to the schema limit and validates the result.

### Final snapshot, schema version 1

| Field | Contract |
| --- | --- |
| `schema_version` | Integer `1`. |
| `symbol` | Canonical instrument, for example `BTC/USD`; not a claim that the provider supports it. |
| `asset_class` | `crypto`, `forex`, `equity`, `index` or `commodity`. Adapter support is narrower than this core enum. |
| `quote_currency` | Required for non-pair instruments; for crypto/forex it may also be derived from the explicit pair. A supplied value must agree with the pair. |
| `timeframe` | Exactly `15m` for this setup detector. |
| `as_of` | The time at which the snapshot's information is being assessed. |
| `source` | Object with `kind` (`market`, `historical` or `synthetic`), `provider` and `reference`. |
| `quote` | Finite positive `bid` and `ask`, with `ask >= bid`; aware quote `as_of` no later than snapshot time. Assembly additionally requires its source reference. |
| `bars` | Between 5 and 1,000 ordered, contiguous 15-minute closed bars; each contains `open_time`, `close_time`, `open`, `high`, `low`, `close`, `volume`. |
| `levels` | Between 2 and 200 records with unique `id`, positive `price`, `kind` (`support`/`resistance`), `timeframe` (`1M`, `1w`, `historical`), `known_at` and `reference`. |
| `reviews` | Objects keyed by `1M`, `1w`, `1d`, `4h`, `1h`; each has `as_of`, `reference` and `bias` (`LONG`, `SHORT`, `NEUTRAL`). |
| `news` | `status` (`clear`, `blocked`, `unknown`), `as_of`, `source`, and an `events` array. |
| `news.events[]` | `currency`, `impact` (`high`, `medium`, `low`), `starts_at`, `known_at`, `reference`. Future scheduled event times are legitimate; future knowledge of the event is not. |

Malformed, non-finite, contradictory, stale or future evidence yields no entry.
Quote/level/review/news references remain inspectable evidence, not automated
verification of the referenced source's truth.

### Execution-bar envelope

The `advance` command accepts:

| Field | Contract |
| --- | --- |
| `schema_version` | Integer `1`. |
| `symbol` | The same canonical instrument as the paper order. |
| `timeframe` | `1m`, `5m` or `15m`. The bar's duration must match exactly. |
| `provenance` | `market`, `historical` or `synthetic`, matching the experiment. |
| `observed_at` | Aware receipt/observation time at or after the bar closes. Market ingestion records actual receipt time. |
| `bar` | `open_time`, `close_time`, `open`, `high`, `low`, `close`, `volume`. |

Execution data must follow approvals and previously processed observations in
chronological order. An existing identical bar is idempotent; changing that
bar's values requires a new experiment. Missing bars while a position is open
must be supplied before later settlement data can be accepted. Order the stream
across all instruments; a delayed event cannot rewrite the portfolio state used
by an already-processed decision.

## Paper risk and accounting

The following are configurable engineering defaults for paper experiments:

| Setting | Default |
| --- | --- |
| Mode and account | `paper`, 10,000 USD initial equity. |
| Position risk limit | 0.5% of the paper equity available at sizing/fill, including configured friction in the risk calculation. |
| Daily loss stop | 2% of initial paper equity, measured through the day's recorded cashflows. |
| Per-position notional | At most 10% of current paper equity. |
| Total reserved/open notional | At most 30% of current paper equity. |
| Concurrent positions | At most 3, with one pending/open position per symbol. |
| Minimum reward/risk | 2:1 after configured friction. |
| Friction | 10 basis points fees and 5 basis points adverse slippage per side. |
| Maximum spread | 25 basis points. |
| Freshness | Snapshot 120 seconds; quote 60 seconds; news review 30 minutes. |
| News window | 30 minutes either side of a relevant high-impact event. |
| Approval life | 120 seconds from the evidence snapshot. |
| Risk-day boundary | Midnight in `Europe/London`, including daylight-saving changes. |
| Optional assessments | Research and Jev both `disabled` initially. |

The ledger persists the complete configuration. CLI calls using an existing
database load its saved settings by default. An explicitly supplied different
configuration is rejected; change settings in a new experiment database.

This increment supports **same-account-quote accounting only**. USD and USDT
are not interchangeable. A quote-currency mismatch blocks the entry; there is
no implicit FX conversion, contract multiplier, financing/borrow-cost model or
live margin calculation. Simulated short arithmetic is not evidence that an
instrument is borrowable or tradable through any broker.

Entry fees are recorded at the simulated fill time. Exit P&L and exit fees are
recorded when the completed exit bar is observed. Daily loss and account equity
are reconstructed from cashflows with their timestamps, so later outcomes do
not leak into earlier decisions. Open positions are not marked to market; this
is a realised-cashflow risk gate, not a full unrealised-drawdown controller.

## Execution, clocks and persistence

Each decision is identified from its snapshot and configuration. The ledger
reserves exposure and rechecks the deterministic gate under a SQLite write
transaction. Repeating an already-recorded decision returns the saved result;
the same decision cannot be assigned to two executors. An experiment also binds
its provenance and executor.

An approved native order starts `PENDING`. It may fill only from a later eligible
bar, at that bar's opening price with adverse slippage. Its approval must still
be valid at the opening time. A moved entry, invalid bracket, breached loss
limit, insufficient reward/risk or exposure constraint cancels or reduces the
candidate rather than assuming the original fill terms still hold.

Stops and targets are simulated from subsequent OHLC observations. If both were
touched within one bar and their order is unknown, the stop is applied first.
An adverse gap uses the worse opening price; favourable target-gap improvement
is not credited. A stop is therefore not a guaranteed maximum loss.

Two persisted clocks prevent observations and economic events from moving an
experiment backwards. `evaluate` uses wall time for market evidence, including a
recheck after synchronous model work. `replay` uses explicit historical/synthetic
event time and does not call online providers. A previously closed bar or a new
model report must not be backdated to an earlier decision.

## Optional integration boundaries

| Integration | Boundary and remaining work |
| --- | --- |
| OpenBB | Closed-candle adapter; explicit identity/timezone checks. Actual installed-version and provider checks remain. Non-crypto aggregate calendars and index/commodity collection are unsupported. |
| TradingAgents | Research-only rating adapter. Current-day requests are stamped at actual completion; historical intraday cutoffs are unsupported. The engine verifies original symbol identity and observation time. |
| Jev | Typed `pass`/`reject` evidence through the documented HTTP API. Required failures block entries; shadow mode records output. Confidence is not a calibrated trade win rate. |
| Freqtrade | A dry-run entry-confirmation mixin and expiring approval-ticket boundary. It does not provide a complete strategy, native stop/target implementation, order-fill lifecycle or account reconciliation. |
| QuantDinger | Possible alternative host, not a deployed component or another execution authority. Client licensing differs from the backend; its documented AI fail-open entry policy is not adopted. |
| jev-trader | Reference inspected; its block-based market-making loop was not adopted. |

The working native ledger and a future Freqtrade host are alternative execution
destinations. Native-account limits cannot establish the state of an independent
Freqtrade wallet. There is no Freqtrade CLI activation command in this increment.
See [FREQTRADE.md](FREQTRADE.md) before implementing that host lifecycle.

Provider tests use injected responses and establish local contract behaviour
only. They do not establish actual upstream execution. No model can convert a
failed deterministic setup into an approved entry; protective exits do not
depend on entry-model availability. Detailed primary references are recorded in
[UPSTREAMS.md](UPSTREAMS.md) and [JEV.md](JEV.md).

## Verification and reporting

Acceptance work covers valid/invalid retests, opposing levels, stale and future
evidence, non-finite values, currency mismatch, model failures, duplicate and
concurrent decisions, restarts, delayed events, missing execution bars, exposure
and daily-loss limits, next-bar fills and ambiguous exits.

Run the local tests and labelled synthetic demo, then inspect the persisted audit.
Report those results separately from optional package execution, real provider
access, repository publication, repository renaming and any future deployment.
None of those external states follows merely from a passing fixture test.
