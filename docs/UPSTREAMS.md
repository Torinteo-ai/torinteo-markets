# Upstream verification and adapter boundaries

Reviewed on 19 October 2026. Product: **Torinteo Markets**. The confirmed target
is the existing empty [Torinteo-ai/KYRONYX](https://github.com/Torinteo-ai/KYRONYX)
GitHub repository (repository ID `1238002647`). Its planned name is
`torinteo-markets`; the rename has not been applied at this checkpoint because
repository-settings authentication is still required.

## What was verified

The four repositories are public projects with different maintainers. The
primary sources reviewed do not substantiate the claim that BlackRock leaked
them. A public repository or its star count does not establish trading results.
The viral post repeats Freqtrade's URL for QuantDinger; use the canonical links
below.

| Project | Canonical repository observed | Licence observed | Role in this increment |
| --- | --- | --- | --- |
| TradingAgents | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | Apache 2.0 | Optional current-day research adapter. |
| OpenBB | [openbq-org/OpenBB](https://github.com/openbq-org/OpenBB) | README specifies Apache 2.0 | Optional market-data adapter. |
| Freqtrade | [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | GNU GPL v3, see [LICENSE](https://github.com/freqtrade/freqtrade/blob/develop/LICENSE) | Dry-run entry-confirmation boundary; the complete host lifecycle remains unimplemented. |
| QuantDinger | [OpenByteInc/QuantDinger](https://github.com/OpenByteInc/QuantDinger) | Backend Apache 2.0; clients have separate source-available licences | Reviewed alternative host, not a second execution authority. |

The old `https://github.com/OpenBB-finance/OpenBB` URL redirected to
`https://github.com/openbq-org/OpenBB` during the review. The latter spelling is
the observed current repository location.

### Version and reproducibility status

| Project | Version information observed | Exact commit pin |
| --- | --- | --- |
| TradingAgents | Its README advertised v0.7.0 in October 2026. | **Not retrieved or verified.** |
| OpenBB | Current repository plus Python v4 reference examples were reviewed. The docs also link a v5 interface; compatibility must be checked against the installed version. | **Not retrieved or verified.** |
| Freqtrade | Stable documentation and the public repository were reviewed. | **Not retrieved or verified.** |
| QuantDinger | README documents its v5 runtime. The Jev entry-filter source was inspected; no installed runtime version was established. | Jev filter inspected at [`f5da57a0e01220ec87b7bc3badc2c6871bfcce22`](https://github.com/OpenByteInc/QuantDinger/blob/f5da57a0e01220ec87b7bc3badc2c6871bfcce22/backend_api_python/app/services/ai_decision_filter.py), as recorded in [JEV.md](JEV.md). This is a source inspection, not an installed/deployed stack pin. |

The initial repository-wide SHA queries were unavailable in this environment.
The separate Jev source review established the QuantDinger filter commit linked
above. Other unverified SHAs remain explicitly unverified. No installed version,
lockfile, container digest or successful live integration is inferred from that
source inspection. Repository homepage links are mutable references; the one
explicit source-commit link is distinguished in the table.

Before activating an optional integration, select and pin a reviewed upstream
release/commit, install it in an isolated environment, and run a real contract
check for that version. A local injected-double test is not that check.

## OpenBB data adapter

Implemented in `torinteo_paper/adapters/openbb.py` with standard-library imports
only. Constructing `OpenBBAdapter` imports no optional package and makes no
request. Calling `fetch_closed_candles` is the explicit activation point. The
initial provider contract is `yfinance`; unsupported providers fail explicitly.

Primary references:

- [OpenBB repository quickstart](https://github.com/openbq-org/OpenBB).
- [Crypto historical prices](https://docs.openbb.co/odp/python/reference/crypto/price/historical/).
- [Equity historical prices](https://docs.openbb.co/odp/python/reference/equity/price/historical/).
- [Currency historical prices](https://docs.openbb.co/odp/v4/python/reference/currency/price/historical).
- [Query parameters and provider-specific symbols](https://docs.openbb.co/odp/v4/python/basic_usage/query_parameters).
- [Data provider coverage and entitlement limitations](https://docs.openbb.co/odp/v4/python/faqs/data_providers).

The adapter calls the documented `historical` function with explicit `symbol`,
`provider`, `interval`, `start_date` and `end_date`, then reads the `OBBject.results`
OHLCV models. The endpoint is selected from the explicit asset class:

| Requested asset | OpenBB route | Symbol convention |
| --- | --- | --- |
| `crypto` | `obb.crypto.price.historical` | Explicit base/quote. `BTC/USD` becomes `BTC-USD`; `BTC/USDT` becomes `BTC-USDT`. No USD/USDT substitution. A provider can still report a particular pair unavailable. |
| `equity` | `obb.equity.price.historical` | Preserve the explicit provider ticker and exchange suffix, e.g. `AZN.L`. |
| `forex` | `obb.currency.price.historical` | Require both currencies, e.g. `GBP/USD` becomes `GBPUSD`. |
| `index`, `commodity`, other | Unsupported in this adapter | No guessed equity proxy or futures substitute. |

### Candle contract

- `as_of` must be an aware datetime and output times are UTC ISO strings.
- A naive provider timestamp requires an explicit `timestamp_timezone` in the
  constructor. This is the provider's timestamp convention, not the viewer's
  time zone. Ambiguous or nonexistent local times at clock changes are rejected.
- Direct intervals are `1m`, `5m`, `15m`, `30m`, `1h`, and `1d`.
- `4h` is built from four consecutive, fully closed `1h` crypto bars aligned to
  UTC 00:00/04:00/08:00/etc.
- `1w` is built from seven complete daily crypto bars, Monday 00:00 UTC to the
  following Monday. It is not an arbitrary rolling seven-day observation.
- `1M` uses complete calendar months from daily crypto bars, including leap
  years, unequal month lengths and December/January rollover. `1m` means a minute.
- Crypto source bars must be continuous. Missing bars are rejected, never
  forward-filled. Incomplete first/last aggregation buckets are excluded and
  counted in the result.
- Non-crypto `4h`/weekly/monthly aggregation is explicitly unsupported until a
  reviewed exchange/session calendar is integrated. It does not assume that
  equities or forex trade every calendar day or start a session at UTC midnight.
- Direct non-crypto daily data uses the next calendar-day boundary in the
  explicitly represented time zone as a conservative completion boundary. It
  does not claim to know an exchange's actual session close.
- Data must be ordered, unique and non-overlapping, with finite, consistent
  OHLC values and non-negative volume. Provider warnings, provider mismatches,
  contradictory interval metadata and future bars are rejected.
- At least one adjacent interval, or an explicit matching source `close_time`,
  must establish the returned cadence. A single unexplained bar or daily bars
  returned for an hourly request do not pass. Legitimate equity/forex session
  gaps are permitted when the cadence is otherwise established.
- A still-open candle is excluded. Empty valid results remain empty; no market
  observations are manufactured.

Closed-bar filtering is **not** a point-in-time archive guarantee. Historical
provider data may have been revised or adjusted after the requested date.
`point_in_time_archive` is therefore false. Intraday history, source coverage,
data entitlements and request limits must be verified on the actual provider.
The downstream engine independently checks that enough recent evidence exists.

## TradingAgents research adapter

Implemented in `torinteo_paper/adapters/tradingagents.py` with a lazy optional
import. The reviewed [graph implementation](https://raw.githubusercontent.com/TauricResearch/TradingAgents/main/tradingagents/graph/trading_graph.py)
declares `propagate(company_name, trade_date, asset_type="stock", portfolio=None)`
and returns `(final_state, run_rating(final_state))`.

The adapter treats the second value strictly as a research rating. Its accepted
vocabulary is `STRONG BUY`, `BUY`, `HOLD`, `SELL`, `STRONG SELL`; prose, numerical
scores, dictionaries, `LONG` and `SHORT` are not coerced into ratings. This exact
vocabulary remains a required installed-version compatibility check. A bearish
rating does not mean opening a short, closing a real holding or placing an order.
The wrapper does not import any real account holdings.

`stock` and `crypto` map directly to the upstream asset type. The core's `equity`
value maps explicitly to `stock`. Other asset types are unavailable through this
adapter. Crypto inputs require an explicit base/quote pair: the engine's
`BTC/USD` is sent to the provider as `BTC-USD`, while `BTC/USDT` is sent as
`BTC-USDT`. The quote is never substituted. The result's `symbol` retains the
original engine identity and `provider_symbol` records the translated spelling.
Equities require an explicit provider ticker such as `AAPL`, `BRK-B` or `AZN.L`;
slashes, paths, whitespace and empty ticker segments are rejected before graph
activation. Selected report fields are retained as serializable evidence.

### Date-only limitation and completion timestamps

Upstream accepts a date, not an intraday data cutoff. Historical replay and
earlier same-day research cannot be certified as known at a requested snapshot.
This adapter therefore:

1. Accepts only requests made on the current UTC date and no more than 60 seconds
   before the adapter starts. That allowance is for request transit; it does not
   backdate the resulting evidence. The limit may be tightened to zero.
2. Returns `unavailable` without calling the graph for historical/older intraday
   timestamps, including previous UTC dates that share the user's local date.
3. Records successful `as_of` at the actual research completion time and also
   returns `requested_as_of` and `started_at` for inspection.
4. Requires consumers to preserve the actual completion timestamp. Research
   completed after a snapshot must not be backdated to the snapshot's time. The
   engine checks identity and observation time, then revalidates the market
   evidence at the actual post-research decision time; stale evidence blocks
   the entry.
5. Rejects future requested times or a completion clock that moves backwards.

No intraday replay guarantee is claimed, even if upstream has separate daily
backtesting features. The adapter makes no historical backtest call.

Unless a graph is explicitly injected, the caller must supply `llm_provider`,
`quick_think_llm` and `deep_think_llm`. No default paid model is silently activated.
Provider errors return `unavailable` with an error class, not potentially secret
request/error text. Missing or malformed research remains unavailable/invalid;
the engine decides whether that blocks an entry or is recorded in shadow mode.

## Freqtrade integration boundary

[Freqtrade's configuration documentation](https://www.freqtrade.io/en/stable/configuration/)
describes dry-run orders as simulated rather than posted to an exchange. The
worker's Freqtrade destination must assert `dry_run` itself and retain its own
normal entry/exit controls. Relevant contracts are the
[strategy callbacks](https://www.freqtrade.io/en/stable/strategy-callbacks/) and
[backtesting docs](https://www.freqtrade.io/en/stable/backtesting/).

The [REST API](https://www.freqtrade.io/en/stable/rest-api/) has forced-entry
commands, but this increment does not use a forced-entry bypass as its strategy
gate. Its lightweight `freqtrade-client` is an optional upstream facility, not a
required dependency of the standard-library worker.

## QuantDinger is an alternative host

The [QuantDinger README](https://github.com/OpenByteInc/QuantDinger) describes a
Jev gate in front of ordinary live entry orders. It also explicitly documents
**fail-open entry behaviour** when AI providers are unavailable. Exits,
stop-loss, take-profit and emergency actions bypass that AI filter. The policy
is an upstream choice, not a policy adopted by this worker: here, a missing
required assessment blocks new entries while protective/exit actions remain
available.

The backend has an [Agent Gateway](https://github.com/OpenByteInc/QuantDinger/blob/main/docs/agent/AGENT_QUICKSTART.md)
under `/api/agent/v1`, separate bearer agent tokens, paper restrictions, scopes,
idempotency keys, backtest jobs and runtime APIs. These are useful possible
integration contracts. QuantDinger is not installed, registered or executing
orders in this increment. Freqtrade and QuantDinger must not independently act
on the same approved decision.

### Licence and running-cost caveats

QuantDinger's backend Apache licence does not cover every product component.
The [web frontend](https://github.com/OpenByteInc/QuantDinger-Vue) uses the
QuantDinger Frontend Source-Available License v1.0; its README states commercial
use needs a separate written agreement with Open Byte Inc. The mobile client
also has a separate licence. The frontend is not copied into this component.
Branding/trademark permissions are separate from code permissions.

OpenBB data subscriptions, hosted model/Jev calls, exchange fees and compute
remain separate from repository licence fees. No zero-cost operational or
trading-performance claim is made.

## What the local tests establish

`python -m unittest discover -s tests -p test_data_adapters.py -v` runs synthetic
contract doubles using the documented request signatures. They check mapping,
time zones, DST ambiguity, future/partial/duplicate/invalid data, cadence,
complete aggregation, date-only research restrictions, strict ratings, lazy
activation and failure handling.

They do **not** establish that an optional package is installed, a real provider
responds with compatible fields, a given ticker exists, market data is fresh,
historical data is revision-free, a model is profitable, a deployment is running
or any real account has been connected.
