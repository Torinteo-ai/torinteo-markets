# Torinteo Markets

A Python paper-trading prototype that evaluates a reviewed major-level breakout
and later retest, records its decision, and simulates fills and exits in SQLite.
The core uses Python's standard library and runs without an API key.

**The runnable execution path is the native paper simulator.** OpenBB,
TradingAgents and Jev have optional adapters tested with injected contract
fixtures. Those checks do not establish installed upstream packages, working
provider credentials, real model latency or trading performance.

Current repository: [Torinteo-ai/KYRONYX](https://github.com/Torinteo-ai/KYRONYX),
ID `1238002647`. The requested repository name is `torinteo-markets`. Renaming is
pending repository-settings authentication; this source targets the existing
KYRONYX repository. Publication and renaming are separate operations.

## Run locally

Use Python 3.11 or later from the directory containing `torinteo_paper`.

```sh
python3 -m torinteo_paper doctor
python3 -m torinteo_paper demo --db /tmp/torinteo-demo.sqlite3
python3 -m torinteo_paper audit --db /tmp/torinteo-demo.sqlite3
```

`doctor` checks local readiness without contacting providers or printing secret
values. `demo` requires a **fresh database path** and uses labelled synthetic
prices and research context. Its results demonstrate program behaviour, not a
market opportunity, historical strategy return or a real account position.

Run the tests with:

```sh
python3 -m unittest discover -s tests -v
```

## Work with market evidence

Market collection is an explicit optional-provider action. Review and pin a
compatible OpenBB installation first; the standard-library core does not install
it automatically. Replace the example dates with the range you need.

```sh
python3 -m torinteo_paper collect --symbol BTC/USD --asset-class crypto --start-date 2026-10-01 --end-date 2026-10-19 --out candles.json
python3 -m torinteo_paper assemble --candles candles.json --context context.json --out snapshot.json
python3 -m torinteo_paper evaluate --snapshot snapshot.json --db market.sqlite3
python3 -m torinteo_paper advance --bar bar-envelope.json --db market.sqlite3
python3 -m torinteo_paper audit --db market.sqlite3
```

`collect` returns a current retrieval with its actual completion time. It does
not turn downloaded historical prices into a point-in-time archive.
`assemble` requires operator-supplied quote, source references, reviewed levels,
timeframe reviews and news review; it does not invent those inputs. The final
setup snapshot uses closed 15-minute bars.

See the [input guide and examples](docs/INPUTS.md) for the collection,
context, snapshot and execution-bar fields. `evaluate` accepts current market
evidence and uses the actual clock. An approved decision creates a pending paper
order; a later execution bar is required for a fill.

For an explicitly historical or synthetic snapshot:

```sh
python3 -m torinteo_paper replay --snapshot snapshot.json --db replay.sqlite3
```

`replay` evaluates at the snapshot's event time and does not call online research
providers. Feed subsequent, correctly labelled bars through `advance` to
simulate execution and settlement. A single replay evaluation is not a full
strategy backtest.

## Experiment rules

- Keep market, historical and synthetic runs in separate databases. Submit
  observations in chronological order; late events cannot rewrite processed
  state. Open positions require continuous execution bars.
- Prices and P&L use the paper account's currency. An instrument's quote
  currency must match it exactly. USD and USDT are different; no currency
  conversion is implemented.
- New databases use the validated default configuration unless `--config
  config.json` is supplied. Existing databases use their persisted settings when
  the flag is omitted. Any explicitly supplied configuration must match the
  stored configuration; use a fresh database for changed experiment settings.
- The default risk day is `Europe/London`. Daily cashflows include entry fees
  when incurred and exit settlements when observed. Account equity is based on
  those realised cashflows, not a marked-to-market valuation of open positions.

Configuration fields and default limits are described in
[the design](docs/DESIGN.md#paper-risk-and-accounting). Defaults are paper
engineering choices, not claims of profitable or suitable risk settings.

## What is included

| Component | Implemented boundary |
| --- | --- |
| Native engine and ledger | Evidence validation, breakout/retest checks, risk checks, pending orders, simulated next-bar fills, stop/target settlement, restartable audit. |
| OpenBB adapter | Explicit instrument mapping and closed-candle checks; real provider activation remains unverified. |
| TradingAgents adapter | Current-day research with completion timestamps and strict ratings; no historical intraday guarantee or order authority. |
| Jev adapter | Typed optional entry assessment; disabled, shadow and required modes. A required failure blocks entry. |
| Freqtrade boundary | Dry-run entry-confirmation mixin and local ticket checks only. A complete host strategy, protection and external-account reconciliation are unfinished. |
| QuantDinger | Reviewed as an alternative host; not implemented or deployed here. Its client UI has separate source-available licensing. |
| jev-trader | Reviewed reference; its market-making strategy was not adopted. |

Major monthly/historical reaction levels and the wider review context are
operator-supplied. Automatic discovery of those levels and a separate
rejection/structure-break setup are not implemented. No live broker, continuous
market watcher or external account connection is supplied by the native demo.

## Further documentation

- [Input guide and runnable examples](docs/INPUTS.md)
- [Local verification and remaining integration limits](docs/VERIFICATION.md)
- [Design and JSON contracts](docs/DESIGN.md)
- [Implementation and activation plan](docs/IMPLEMENTATION_PLAN.md)
- [Upstream sources, versions and limitations](docs/UPSTREAMS.md)
- [Jev request and response contract](docs/JEV.md)
- [Freqtrade boundary and missing host lifecycle](docs/FREQTRADE.md)
