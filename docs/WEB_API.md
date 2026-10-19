# Owner web API

The optional web service runs the same `PaperEngine` and `PaperLedger` used by the command-line application. It persists each experiment in its own SQLite database. The server never exposes a live-trading route or accepts an exchange account key.

## Run locally

Install the pinned web environment and build the frontend from the repository root:

```bash
python -m pip install -r requirements-web.txt
python -m pip install --no-deps -e .
npm --prefix web ci
npm --prefix web run build
python -m torinteo_paper.web --data-dir runtime/web --static-dir web/dist
```

Open `http://127.0.0.1:8787`. The default server binds only to loopback. The owner application accepts `localhost`, `127.0.0.1` and `::1` Host headers and requires the request Origin to match. Mutations require strict `application/json` requests; there is no permissive CORS policy.

For the Vite development proxy, explicitly allow its origin:

```bash
python -m torinteo_paper.web --allow-origin http://127.0.0.1:5173 --allow-origin http://localhost:5173
```

The browser sends its real Origin. The proxy must not remove it to circumvent the owner boundary. Production builds are served from `web/dist` on the API origin.

## Storage and access

`--data-dir` is a server startup setting. API users can only select opaque UUID experiment IDs; they cannot submit filenames, database paths or directory names. Experiment names contain 1–80 printable characters. The owner workspace supports up to 250 experiments.

Each experiment stores immutable configuration, a declared provenance (`synthetic`, `historical` or `market`), decisions, orders, events, cashflows and the latest successfully submitted snapshot. Source data and simulated account state from different kinds cannot be mixed. SQLite transactions retain the engine's risk recheck and exposure-reservation guarantees. The web layer serializes operations per experiment; POSIX file locks also serialize web requests from separate local processes. On platforms without POSIX file locking, run one web worker.

The configured data directory must reside on a persistent local disk or server volume. Back up that directory with a consistent SQLite backup or after stopping the application. Restarting the process with the same directory restores experiments. Changing the directory starts a different owner workspace.

**Vercel Functions cannot provide durable local SQLite storage.** The application refuses startup when `VERCEL=1`; it does not silently place an account under `/tmp`. Hosted account operations require either a persistent-volume Python server or a separately implemented transactional database backend. A static frontend deployment alone does not activate this service. See [Vercel's SQLite guidance](https://vercel.com/kb/guide/is-sqlite-supported-in-vercel).

### Network access

A non-loopback bind requires `TORINTEO_WEB_TOKEN`, containing 32–512 non-whitespace characters. Set a securely generated token directly in the server environment. It is not a frontend build variable, URL parameter or command-line argument.

Add the exact hostname/IP clients will use with `--allow-host`. No wildcards are supported. Serve network traffic through HTTPS. If a trusted TLS proxy forwards to this service, configure its exact IP using `--trusted-proxy`; other forwarded headers remain untrusted. The proxy must preserve the approved Host and set the forwarded HTTPS scheme.

```bash
# TORINTEO_WEB_TOKEN must already be set in the server environment.
python -m torinteo_paper.web --host 0.0.0.0 --allow-host markets.example.com --trusted-proxy 127.0.0.1 --data-dir /data/torinteo-markets --static-dir web/dist
```

The owner enters the token through `POST /api/login`. Successful login creates a signed eight-hour `HttpOnly`, `SameSite=Strict` cookie, additionally marked `Secure` on HTTPS. The token is never returned in an API response. Credential presence is exposed only as booleans; access logging is disabled by the supplied launcher. Changing the token invalidates existing signed cookies. Logout clears the browser cookie.

This is one owner workspace, not a customer authentication or multi-tenant billing system. It does not attach the Markets application to other Torinteo customer accounts or Supabase tables.

## API contract

All responses represent paper operation. Errors use an HTTP error status and a bounded object such as `{"error":"Experiment not found.","mode":"paper"}`. No server database path is returned.

| Route | Request or purpose |
|---|---|
| `GET /api/health` | Service version, paper-only status, storage type and login requirement. |
| `GET /api/session` | `{authenticated, requires_login, mode}`. |
| `POST /api/login` | `{token}`; returns session status and sets the owner cookie. |
| `POST /api/logout` | `{}`; clears the owner cookie. |
| `GET /api/experiments` | `{experiments, mode}`. |
| `POST /api/experiments` | `{name, kind, config?}`; creates a persisted experiment and returns its detail with status 201. |
| `GET /api/experiments/{id}` | Current account, complete orders, decisions and event history, latest snapshot and demo guidance. |
| `POST /api/experiments/{id}/demo` | `{step: "prepare" \| "evaluate" \| "fill" \| "close" \| "blocked"}`. |
| `POST /api/experiments/{id}/evaluate` | `{snapshot}` using the existing validated snapshot contract. |
| `POST /api/experiments/{id}/advance` | Execution bar envelope described below. |
| `POST /api/experiments/{id}/sync` | `{}`; fetch and process closed one-minute public candles for existing native market paper orders. |
| `GET /api/experiments/{id}/export` | Full paper audit JSON download. |
| `GET /api/market?symbol=BTC%2FUSD&limit=100&timeframe=15m` | Actual public provider collection; failure is surfaced without invented fallback candles. |
| `GET /api/integrations` | Package/configuration presence and supported capabilities; presence is not connectivity verification. |

Apart from health, session status and login, API routes require an owner cookie when token authentication is configured. Requests are bounded to 2 MiB, UTF-8 JSON objects, with duplicate keys, non-finite numbers and excessive nesting rejected. Unknown action fields are rejected. Static files are restricted to the configured build directory; missing API routes never fall back to HTML.

### Experiment detail

```json
{
  "experiment": {
    "id": "opaque-uuid",
    "name": "My paper desk",
    "kind": "synthetic",
    "created_at": "2026-10-19T10:00:00.000000Z",
    "config": {"mode": "paper"}
  },
  "mode": "paper",
  "as_of": "2026-10-19T09:00:00.000000Z",
  "account": {
    "equity": 10000,
    "daily_pnl": 0,
    "open_symbols": [],
    "open_notional": 0,
    "currency": "USD"
  },
  "orders": [],
  "decisions": [],
  "events": [],
  "latest_snapshot": {},
  "demo": {
    "enabled": true,
    "stage": "prepared",
    "next_step": "evaluate",
    "available_steps": ["evaluate"]
  }
}
```

This abbreviated schema is illustrative; real `config` and `latest_snapshot` contain the full validated fields. Decisions contain the core's full parsed payload: `decision_id`, `call`, `reason_codes`, `intent`, `evidence`, `assessments`, provenance and approval times. Orders retain the core's numeric fields and `PENDING`, `OPEN`, `CLOSED` or `CANCELLED` state. Account equity is cashflow-based paper equity; no unrealized mark-to-market figure is invented.

### Guided demonstration

Creating a synthetic experiment with the standard configuration prepares the invented `TEST/USD` snapshot automatically, without recording a trade. The steps are:

1. `evaluate`: run the real gate and reserve a pending paper order.
2. `fill`: submit the next synthetic closed candle and simulate an eligible fill.
3. `close`: submit the candle that crosses both stop and target; the real simulator applies the stop first.
4. `blocked`: evaluate the invented opposing level near the entry and record `NO_TRADE`.

The next available step comes from persisted account records, including after restart. Repeating the current completed step does not create a duplicate order. Skipping steps returns 409. Running manual evaluation/execution marks that experiment as custom and disables the guided sequence. A market or historical experiment cannot run synthetic demo actions.

### Evaluation and execution

Market evaluation uses the server's actual wall clock; an old input cannot claim a fresh approval by changing the request time. Synthetic and historical replay use the snapshot's timestamp, following the existing CLI replay behavior. Historical snapshots cannot lie in the future. The engine also rejects future or unclosed evidence and rechecks entry risk within its transaction.

The manual advance route accepts the existing execution envelope directly:

```json
{
  "schema_version": 1,
  "symbol": "TEST/USD",
  "timeframe": "15m",
  "provenance": "synthetic",
  "observed_at": "2026-10-19T09:15:00.000000Z",
  "bar": {
    "open_time": "2026-10-19T09:00:00.000000Z",
    "close_time": "2026-10-19T09:15:00.000000Z",
    "open": 100.23,
    "high": 100.8,
    "low": 100.1,
    "close": 100.5,
    "volume": 1200
  }
}
```

Execution supports 1-minute, 5-minute and 15-minute closed bars. Market receipt time is taken from the server clock. Provenance must match the experiment. Existing gap, duplicate/conflicting-bar, approval-expiry, account-currency and event-clock protections apply unchanged.

### Observe market paper positions

`POST /api/experiments/{id}/sync` is an explicit owner action. It processes available execution evidence for existing native paper orders in a market experiment. It does not scan for entries, create a recurring job or contact a trading account. An empty experiment returns `sync.status: "idle"` without calling a provider.

For active `BTC/USD`, `ETH/USD` or `SOL/USD` orders, each request retrieves a bounded window of 100 closed one-minute candles from the public Coinbase feed. Every required collection is validated before processing begins. Observations are then applied in chronological order across instruments, up to the most recent candle close shared by every required feed. A candle whose open precedes the approval cannot manufacture an earlier fill.

The engine retains approval expiry, real receipt times, already-recorded candle hashes, price limits, stop/target settlement and event-clock checks. Missing execution history cannot be skipped. If an open order needs observations older than the available window, the response explains the gap; the operator must supply complete chronological observations or start a separate experiment. An expired pending approval may be cancelled from an actually observed later candle without inventing a fill in the missing window.

Each candle remains a separate atomic ledger transaction. A process interruption can leave a committed chronological prefix; retry resumes from the ledger and does not duplicate fills or fees. Feed unavailability returns HTTP 503 with no fallback prices. Historical/synthetic experiments, invalid provenance or chronological conflicts return a validation error. No provider exception details or credentials are returned.

The response contains refreshed experiment detail plus:

```json
{
  "sync": {
    "status": "updated",
    "processed_bars": 1,
    "symbols": ["BTC/USD"],
    "source": "Coinbase public data",
    "timeframe": "1m",
    "observed_at": "2026-10-19T10:02:03.000000Z",
    "through": "2026-10-19T10:02:00.000000Z",
    "last_processed_close": "2026-10-19T10:02:00.000000Z",
    "runs_continuously": false
  }
}
```

These example timestamps and counts illustrate the schema. Real values come from the request and its persisted observations. `status` is `updated`, `waiting` when active orders have no newly eligible candle, or `idle` when no active orders exist. `through` is the shared feed cutoff; it is not a claim that every candle was processed after an order had already closed.

Sync uses the same owner-cookie, Host/Origin, JSON-only and size-limit boundary as other mutations. Its body must be exactly `{}`; requests cannot select an executor, bypass chronology, specify a provider URL or force an order.

### Optional research

The web API only constructs external assessment adapters when an experiment explicitly enables their mode. Jev uses the server's `TYPESAFE_API_KEY`. TradingAgents additionally requires its installed package and all three explicit server settings:

```text
TORINTEO_RESEARCH_PROVIDER
TORINTEO_RESEARCH_QUICK_MODEL
TORINTEO_RESEARCH_DEEP_MODEL
```

The selected provider's credentials stay in its documented server environment. No implicit paid model defaults are selected. Missing required assessments block entry. Synthetic/historical replay does not run current-day TradingAgents research.

## Verification

`tests/test_web_api.py` runs actual SQLite-backed HTTP scenarios and controlled public-data doubles. It covers restart persistence, the complete guided execution sequence, provenance separation, wall-clock validation, body/path limits, Host/Origin checks, owner login/cookies, credential non-disclosure and the sync route's authentication/error mapping. The focused sync suite separately tests candle chronology and settlement. Optional HTTP dependencies are required for these tests; the core CLI remains usable without them.

```bash
python -m unittest tests.test_web_api -v
```
