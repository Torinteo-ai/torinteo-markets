# Torinteo Markets web app

## Purpose and scope

Build the usable owner paper desk around the existing transactional Python engine in `Torinteo-ai/torinteo-markets`. The user authorised beginning this build after choosing the repository and product name. The app makes observed market data, human-reviewed evidence, deterministic decisions and their simulated outcomes visible in one workflow. It has no live order capability.

## Architecture

A React, TypeScript and Vite frontend is served by the same-origin FastAPI application. The API creates isolated experiments with immutable risk configurations and one durable SQLite ledger per experiment. Experiments have explicit `synthetic`, `market` or `historical` provenance. The existing engine and ledger remain authoritative; the browser never computes approvals or writes simulated fills itself.

Use the loopback interface by default. Serving on a network requires an owner token, an explicit allowed hostname and an HTTPS reverse proxy. Mutations require JSON, bounded strict decoding and same-origin checks. The frontend contains no provider or exchange credentials. This storage architecture requires a persistent volume and is not compatible with ephemeral Vercel function filesystems. Deployment to a shared remote account requires a durable database migration and explicit account authorization, separate from this owner app.

## Primary workflows

1. Create a synthetic experiment. Inspect its five actual invented candles, reviewed context and fixed paper-risk configuration. Evaluate, advance the next execution bar, advance the adverse exit bar, then evaluate a blocked near-resistance example. All actions update the native ledger and survive a server restart.
2. Create a market experiment. Retrieve real Coinbase public spot candles and bid/ask for BTC/USD, ETH/USD or SOL/USD. Inspect provider and receipt timestamps. Add reviewed major levels, five timeframe reviews and a sourced news review. Evaluate the snapshot through the existing gate. Missing, stale or contradictory evidence must fail clearly. No automatic monthly levels or calendar clearance are fabricated.
3. Import a snapshot or execution-bar envelope into an experiment with matching provenance. Review decisions, pending/open/closed orders and the append-only audit history. Export the complete experiment as JSON.

## Visual design

The generated primary-screen concept is `../design/markets-desktop-concept.png`. It is a design reference, never a UI screenshot shipped as the app. Use the exact owned Torinteo TT SVG and locally packaged Roboto fonts.

Tokens: background `#060b14`, sidebar `#08111d`, surface `#0b1321`, border `#1c2938`, cyan `#20e6ff`, text `#e7edf5`, muted text `#a2b2c7`, subdued red `#ef8491`. No gradients or decorative glow. Headings 26–30px; body 14px; chrome 13px; labels 12px; monospace tabular numbers. Radius 6px. Desktop sidebar 260px, content gutters 30px, chart/inspector approximately 2:1. Use a horizontal metric band and a decision table, with fine boundaries rather than nested cards. Icons use Lucide outlines with 1.5–1.8px strokes.

Navigation and primary-screen copy: Torinteo / Markets; Desk; Research & levels; Decisions; Paper positions; Audit trail; Integrations; Workspace / Paper desk; Local account; New experiment; Paper desk; Evidence first. Every decision recorded.; Export audit; Paper equity; Realised P&L; Open positions; Risk per trade; Setup review; Evaluate paper setup; Review inputs; Recent decisions; No decisions yet; PAPER ONLY; Orders use simulated funds.

Functional deviations from the illustrative concept are deliberate: chart geometry, prices, number of candles and metrics come from API data; the synthetic example has five candles and an opposing level at 105.00, not the concept's illustrative 110.00. The canonical SVG replaces the concept's text placeholder. Loading, login, empty, error, creation, evidence-editing and export states extend the same controls and tokens. At <=1000px collapse the sidebar into a scrollable navigation bar; at <=720px stack chart and inspector and keep tables horizontally scrollable inside their region.

## Acceptance

Verify the rendered desktop and mobile app, compare against the concept, run the actual synthetic approval/fill/exit path and inspect the recorded account outcome. Exercise market observations with the real HTTP adapter and clearly distinguish those from fixtures. Test API persistence and experiment/auth/input boundaries. Build the production frontend, run core and web tests in CI, and publish a single reviewed source change without committing accounts, secrets, cached market observations or dependency directories.
