# Initial verification — 19 October 2026

This record covers the initial Torinteo Markets paper prototype. It describes
local software checks, not a historical strategy evaluation or provider
activation. The GitHub workflow runs the core checks again when the source is
published or changed; consult its actual run status for the remote result.

## Local results

Python **3.12.14** was used for these checks.

| Check | Observed result |
| --- | --- |
| `python3 -m unittest discover -s tests` | **167 tests passed**. |
| `python3 -m compileall -q torinteo_paper integrations tests` | Completed successfully. |
| `python3 -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir /tmp/torinteo-markets-wheel .` | Built `torinteo_markets-0.1.0-py3-none-any.whl` without network access. |
| `python3 -m torinteo_paper demo --db <fresh-path>` | Synthetic approval, pending order, simulated fill, restart, conservative stop settlement and blocked opposing-level entry completed. |
| Separate file-driven commands | Assembly, replay, two bar advances and audit completed across separate CLI calls. |
| `python3 -m torinteo_paper doctor` | OpenBB, TradingAgents and Freqtrade were not installed; no TypeSafe key was present; no connectivity test ran. |

The test suite comprises 41 deterministic gate/account-flow tests, 46
OpenBB/TradingAgents adapter tests, 19 Jev adapter tests, 43 Freqtrade
consumer/export tests, and 18 command/input tests. Provider tests use injected
clients or transports; they are not installed-product integration tests.

## What the demonstration establishes

All prices, levels, timeframe reviews and calendar content in `TEST/USD` are
invented and labelled **synthetic**. The approved decision becomes one paper
order. After a restart, a candle touching both stop and target settles at the
stop, with simulated adverse slippage and fees. A second candidate near its
opposing level returns `NO_TRADE` with `OPPOSING_LEVEL_TOO_CLOSE`.

This exercises executable control flow and persistent accounting. It provides
no estimate of market returns, forecast accuracy or exchange fills.

## Independent review

A separate review inspected the paper execution/accounting core and the final
CLI boundary. Reported issues were corrected and their specific regression
cases independently rechecked. These included actual approval time, missing
execution bars, late cross-symbol events, historical cashflows, London-day
fees, executor/provenance isolation, quote-currency contradictions, collection
availability timestamps and future-dated historical replay.

All reported findings were resolved within that review's scope. This is not a
formal audit or certification of the uninstalled upstream products.

## Unverified or unfinished

- Actual OpenBB data coverage, provider timestamp conventions and data access.
- Actual TradingAgents installation, research run and configured model access.
- Actual Jev request, response latency, cost measurement and decision quality.
- A complete Freqtrade host strategy, protective lifecycle and independent
  account reconciliation. Only its dry-run entry boundary is implemented.
- QuantDinger hosting, any live exchange connection, continuous scheduling and
  automatic discovery of the operator's major reaction levels.
- Market strategy validation with point-in-time evidence, realistic execution
  constraints and out-of-sample data.

These limits remain explicit in the [activation plan](IMPLEMENTATION_PLAN.md).
