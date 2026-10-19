# Torinteo Markets Web App Implementation Plan

> **For agentic workers:** Use the existing parallel task ownership for the public adapter, API and research form. Root integrates, verifies and publishes the implementation.

**Goal:** A usable owner paper desk backed by the existing Python decision engine and durable account ledger.

**Architecture:** React/Vite communicates with a same-origin FastAPI service. Opaque experiment IDs select isolated SQLite ledgers. Public Coinbase observations and explicitly reviewed strategy context feed the existing engine.

**Tech stack:** Python >=3.11, FastAPI, uvicorn; React 19, TypeScript 7, Vite 8; standard-library SQLite ledger.

**Spec:** `docs/WEB_APP_DESIGN.md`.

## Global constraints

- Product/repository: Torinteo Markets / `Torinteo-ai/torinteo-markets`.
- Paper execution only; existing risk checks and provenance separation remain authoritative.
- No credentials in browser bundles or repository files.
- Account data requires persistent disk, default loopback access; public network access requires owner authentication.
- Exact canonical Torinteo SVG, dark navy/cyan palette and code-native controls.

## Review focus

- A stale market quote or incomplete human review must not silently become a fresh approved setup.
- Retrying a mutation or restarting the server must not duplicate a decision/order or lose an account.
- Concurrent requests cannot cross experiment IDs or circumvent the ledger's transaction lock.
- A cross-origin page, hostile Host header or malformed/oversized JSON cannot perform account mutations.
- A small screen must preserve chart context, visible errors and accessible primary actions.

### Task 1: Public observations

Files: `torinteo_paper/adapters/public_market.py`, `tests/test_public_market.py`, `docs/PUBLIC_MARKET.md`.

Interface: `PublicMarketClient().collect(symbol='BTC/USD', timeframe='15m', limit=100)` returns the existing collection envelope plus the actual bid/ask quote and receipt timestamps.

- [x] Add bounded contract tests for exact identity, closed contiguous candles, stale/future data and response limits.
- [x] Implement fixed public GET routes and no fabricated fallback.
- [x] Verify real public API responses and record observation times separately from test fixtures.

### Task 2: Persistent owner API

Files: `torinteo_paper/web/`, `tests/test_web_api.py`, `docs/WEB_API.md`.

Interface: `create_app(data_dir, static_dir, host, token, clock, market_client)`; `/api/experiments`, `/api/experiments/{id}`, `/demo`, `/evaluate`, `/advance`, `/export`, `/api/market`, `/api/session`, `/api/integrations`.

- [x] Implement experiment creation/detail/export and canonical config binding.
- [x] Exercise approval, fill and close through `PaperEngine` and `PaperLedger`.
- [x] Implement strict request and owner-access boundaries.
- [x] Verify restart durability, isolated kinds, duplicate actions, malicious paths/body inputs and origin checks with unittest.

### Task 3: Working desk interface

Files: `web/src/App.tsx`, `components/`, `features/`, `types.ts`, `api.ts`, `format.ts`, `styles.css`.

- [x] Implement shared shell, accessible creation/login/error states and experiment selection.
- [x] Render real OHLC data and reviewed levels, with pointer/keyboard candle inspection and timeframe selection.
- [x] Wire synthetic steps and market review submission to the API.
- [x] Show decisions, order lifecycle, audit details and downloadable JSON.
- [x] Verify strict TypeScript build and full browser interactions.

### Task 4: Launch, verification and publication

Files: root launch/deployment configuration, README, workflow, `docs/WEB_APP_VERIFICATION.md`.

- [x] Build a repeatable local launch and persistent-volume container configuration.
- [x] Run complete tests, production frontend build, desktop/mobile QA and restart check.
- [x] Compare the primary screen with the concept and record functional deviations.
- [x] Publish to the observed GitHub parent using a guarded ref update and verify remote CI. Source revision `d5baab2` passed run `39236011092`, including standard Chromium and container restart persistence.
