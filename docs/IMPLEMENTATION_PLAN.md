# Torinteo Markets implementation and activation plan

## Goal and destination

Deliver a runnable native paper simulator with evidence validation, deterministic
entry checks, durable accounting and explicit optional research/data adapters.
The controlling design is [DESIGN.md](DESIGN.md).

The target is the existing empty `Torinteo-ai/KYRONYX` GitHub repository,
repository ID `1238002647`. Product branding is **Torinteo Markets** and the
requested future slug is `torinteo-markets`. Repository-settings authentication
is still required to rename it; the available connector does not expose that
operation. Initial source publication can target KYRONYX. Do not report the
rename, publication or deployment as complete without evidence for that action.

## Component scope

| Component | Implementation covered by this increment | Boundary |
| --- | --- | --- |
| Evidence and deterministic gate | Explicit source/time/currency contracts, reviewed monthly/historical levels, 15-minute breakout and later retest, review/news/opposing-level checks. | No automatic reaction-level discovery or rejection/structure-break setup. |
| Native paper engine and SQLite ledger | Transactional risk rechecks, unique decisions, pending orders, later-bar fills, timed fees and exit settlements, audit and restart state. | Same-account-quote currency only; no live broker or unrealised-drawdown controller. |
| Command layer and examples | Local readiness, labelled synthetic demo, explicit collection, context assembly, market evaluation, historical/synthetic replay, bar advancement and audit. | No installed scheduler; optional collection requires a separately verified dependency/provider. |
| OpenBB and TradingAgents | Lazy adapters, explicit instrument mapping and time handling, strict research ratings. | Contract doubles are not actual upstream execution. |
| Jev | Documented typed HTTP request, response validation, disabled/shadow/required assessment modes. | No verified inference access, latency or trading accuracy. |
| Freqtrade | Dry-run entry-confirmation mixin, expiring tickets and durable local one-time claims. | Full host strategy, protection, fill lifecycle and account reconciliation remain unfinished. |
| QuantDinger / jev-trader | Primary-source review and documented integration decisions. | QuantDinger is an alternative host, not implemented here; jev-trader's strategy is not adopted. |

## Initial delivery acceptance gates

These are verification requirements, not pre-filled claims of completion. Record
the actual command results and source-control state when they are performed.

1. **Core and boundaries.** Run the full local unittest suite. Cover valid
   long/short retests, no retest, nearby opposing levels, invalid numbers,
   unknown/future/stale evidence, missing reviews, news windows, currency
   mismatch, duplicate decisions and invalid model responses.
2. **Chronological paper execution.** Exercise pending approval, later-bar fill,
   size/risk recheck, entry fee, protective exit and settlement. Check that late
   events, changed historical bars and missing bars for an open position fail
   without rewriting prior state. Exercise 1m/5m/15m execution boundaries.
3. **Accounting and restart.** Reopen the same database with its persisted
   configuration; reject a conflicting explicit configuration. Inspect London
   risk-day cashflows, position/notional reservations and restart idempotency.
   Keep provenance and execution destinations separate.
4. **Command-layer smoke checks.** Run `doctor`, a labelled `demo` against a new
   database, and `audit`. Exercise assembly rejection when operator context is
   absent or inconsistent. Verify that replay does not activate online
   providers and that existing databases retain their saved settings.
5. **Independent review.** Review the final source diff and remaining findings.
   Check that documentation describes the native runnable path and the
   incomplete Freqtrade host accurately. Avoid tests that only repeat the
   implementation's own assumptions.
6. **Publish the reviewable source.** Confirm repository identity, then publish
   the initial source to KYRONYX through the available repository capability.
   Record the branch/commit or review URL. Treat renaming as a separate pending
   settings action if authentication remains unavailable.

## Optional integration activation

Keep these gates distinct from the initial standard-library delivery:

1. Choose a reviewed upstream release/commit and record its exact identity.
   Install each optional package in an isolated environment; do not infer
   compatibility from current mutable documentation alone.
2. For OpenBB, verify a real selected instrument, route, symbol mapping,
   timestamp convention, interval, returned coverage and provider entitlements.
   Preserve actual receipt time and do not relabel a current price download as
   a historical point-in-time archive.
3. Supply real sourced operator context for one market snapshot: quote and
   reference, reviewed levels, timeframe reviews and dated news status. The
   collection step must not manufacture those fields.
4. For TradingAgents, select the provider and both model tiers explicitly. Check
   its installed return vocabulary and symbol mapping with a real current-day
   research run. Verify completion timestamps and reject historical intraday
   reuse. Retain all provider access/cost decisions as explicit activation.
5. For Jev, verify the chosen version and actual access before a request. Measure
   the observed response, timeout behaviour and costs. Begin with observation
   in shadow mode; model confidence must not be reported as a trade win rate.
6. If required assessments are selected, confirm that their absence, failure,
   staleness or disagreement blocks entries while native exits continue.

Each step needs evidence from the actual upstream component. Mocked network
responses do not establish connectivity or profitable decisions.

## Separate Freqtrade completion work

The mixin is not a runnable end-to-end strategy. Before offering that execution
destination, implement and verify:

- A host strategy that maps valid approved tickets into normal entry signals,
  reconciles callback order and records actual dry-run fills.
- Native absolute stop/target protection that survives restarts and does not
  depend on entry-ticket freshness or model availability.
- Expiry cancellation plus recovery after interrupted order/fill processing.
- External dry-run equity, fees, open/pending positions and daily-loss/exposure
  reconciliation before every entry. Native SQLite account limits alone cannot
  establish the independent Freqtrade wallet's state.
- A pinned upstream dry-run lifecycle test with real public market data.

Until those requirements are met, use the native paper simulator and keep the
Freqtrade integration labelled as an entry-confirmation boundary. Detailed
requirements are in [FREQTRADE.md](FREQTRADE.md).

## Deliberate later extensions

Automatic identification of major reaction levels, a rejection/structure-break
setup, exchange-specific calendars, currency conversion and contract accounting,
continuous scheduled collection, additional execution platforms and any live
trading capability each require their own implementation and verification.
They are not implied by this initial build or by attaching another repository.
