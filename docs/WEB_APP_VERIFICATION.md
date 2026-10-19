# Torinteo Markets web app verification

Recorded on **19 October 2026**. The local owner app is complete: the browser uses the native paper engine, experiments and audit records persist on disk, and public Coinbase market data is available through the server adapter. This record covers local checks and their limits. External deployment, optional model-provider activation and live exchange orders have not been performed.

[![Paper checks](https://github.com/Torinteo-ai/torinteo-markets/actions/workflows/paper-checks.yml/badge.svg?branch=main)](https://github.com/Torinteo-ai/torinteo-markets/actions/workflows/paper-checks.yml?query=branch%3Amain)

The badge links to the current workflow status. Publication, a completed GitHub Actions run for this web-app revision and a successful container build are **pending at the time this local verification record was written**; the badge is not evidence that those gates have already passed. The configured gates are in [Paper checks](../.github/workflows/paper-checks.yml).

## Recorded results

| Check | Result | Scope and evidence |
| --- | --- | --- |
| Python suite | **238 passed** on Python **3.12.14** | 167 existing/core tests, 30 web API tests, 21 market-sync tests and 20 public-market contract tests. |
| Frontend production build | **Passed** | `npm --prefix web run build` runs strict TypeScript checking with `tsc --noEmit`, then the Vite production build. |
| Browser acceptance | **Passed** | [Executable browser workflow](../web/tests/smoke.mjs) exercised the real local API and persisted native-engine paper ledger. |
| Desktop and mobile rendering | **Passed** | Viewports of **1536 × 1024** and **390 × 844**; no horizontal overflow of the document or body, and zero browser `pageerror` events. |
| Account display regressions | **Passed** | USD, EUR, the non-ISO asset code USDT, and missing/null amounts were checked so currency formatting does not crash or silently mislabel account values. |
| Real public-data browser path | **Passed** | Actual Coinbase HTTPS responses for BTC/USD and ETH/USD, 100 closed 15-minute candles each; no provider doubles, decisions or orders. [Recorded observations](public-ui-verification.json). |
| Container and hosted app | **Pending / not deployed** | Container build is configured in CI. Local persistence has been verified; remote-volume operation has not. |

The API suite covers the owner-session boundary, Host/Origin rejection, strict JSON and bounded inputs, static-file containment, experiment/provenance separation, observation time, idempotency and persisted state after restart. Sync regressions cover interrupted multi-symbol processing, resuming entries and expirations at a shared candle close, and preserving the newer-decision timing guard. These are targeted functional checks, not a claim of exhaustive security verification. See [web API tests](../tests/test_web_api.py) and [sync tests](../tests/test_web_sync.py).

## What the browser acceptance check did

The synthetic walkthrough uses explicitly invented `TEST/USD` market inputs with the **actual paper engine and SQLite ledger**. Its decisions, orders, account balances and audit records come from API responses; browser controls do not fabricate a successful trade.

1. Created a fresh, labelled synthetic experiment with zero decisions and orders.
2. Evaluated the supplied setup and received a **LONG** decision with one **PENDING** order. Approval was visibly distinct from a fill.
3. Advanced the next execution candle, producing an **OPEN** position and recorded entry fees.
4. Reloaded the page, then advanced the exit candle. The persisted position became **CLOSED**, with **STOP_LOSS** as the exit reason and a negative net result. The demonstration does not present a guaranteed profit.
5. Evaluated the blocked example and received **NO_TRADE**. The final record contained two decisions, one closed order and **five audit events**.
6. Filtered the decision table to `NO_TRADE`, opened and closed decision details, inspected the closed position, and checked the audit table against the experiment's events. Export produced a downloadable paper-audit JSON file.
7. Opened Integrations and checked the native-engine status and the explanation that live exchange credentials are not used.
8. Created another experiment for mobile review, inspected the stored labelled synthetic research, and checked the layout at the narrow viewport.
9. Deliberately returned HTTP 503 for the browser's public-market request. The market experiment displayed the error, retained blank research with unknown news clearance, and kept **Evaluate paper setup** disabled.
10. Saved a local research draft, reloaded the browser and confirmed that the draft value persisted. Saving a draft did not create a decision or order.

The forced HTTP 503 is a **test double for an unavailable feed**. The synthetic candles are controlled test inputs. Neither is represented as live market data. In contrast, order processing, fees, stop handling, persistence and audit export in this workflow use the application itself. The zero-error assertion concerns JavaScript `pageerror` events, rather than a claim that every browser console message was inspected.

## Real public-market verification

The public adapter was first checked directly using its default HTTPS transport, with no injected provider response, API key or exchange account. These checks are documented in [Public market data](PUBLIC_MARKET.md).

| Instrument | Received on 19 October 2026, UTC | Provider quote time, UTC | Closed 15-minute candles | Latest candle close, UTC |
| --- | --- | --- | ---: | --- |
| BTC/USD | 09:13:28.063511 | 09:13:26.881389 | 100 | 09:00:00 |
| ETH/USD | 09:14:35.786846 | 09:14:35.269677 | 100 | 09:00:00 |
| SOL/USD | 09:14:36.026443 | 09:14:34.694660 | 100 | 09:00:00 |

Each collection excluded the unfinished candle. BTC/USD was also checked with 100 closed one-minute candles received at **09:16:08.336636 UTC** and 100 closed five-minute candles received at **09:16:08.495788 UTC**, with latest closes of **09:16:00 UTC** and **09:15:00 UTC**, respectively. Other supported interval mappings have controlled contract coverage; these observations do not assert that every interval was exercised against the live provider.

The separate browser check then exercised the running app's public-data path and switched the selected instrument from BTC/USD to ETH/USD:

| Instrument | Browser/API observation, UTC | Preserved provider quote time, UTC | Closed 15-minute candles | Latest candle close, UTC |
| --- | --- | --- | ---: | --- |
| BTC/USD | 09:58:27.652619 | 09:58:27.192267 | 100 | 09:45:00 |
| ETH/USD | 09:58:29.382572 | 09:58:28.427060 | 100 | 09:45:00 |

Both observations are dated **19 October 2026** and recorded in [public-ui-verification.json](public-ui-verification.json). The ETH selection updated the market UI, the mobile layout fit its viewport, and the browser reported zero page errors. The market experiment retained **zero decisions, zero orders and no research snapshot**: loading candles and quotes did not invent levels, alignment or news clearance. No live order endpoint was called.

These are timestamped connectivity, data-validation and UI checks. They are not continuous-feed monitoring, strategy backtests or evidence of trading returns. Public-data screenshots show the captured observation; they do not claim to display the market at the time a reader opens this document.

## Design fidelity ledger

The [desktop concept](../design/markets-desktop-concept.png), [built desktop desk](images/desk-desktop.png) and [built mobile desk](images/desk-mobile.png) were visually inspected together in the same QA pass. The implementation follows the structure and tokens recorded in [Web app design](WEB_APP_DESIGN.md). **No perfect-match or pixel-identical claim is made.**

| Aspect | Observed fidelity | Deliberate difference or practical limit |
| --- | --- | --- |
| Layout and hierarchy | Dark left navigation, top workspace bar, page heading, four-metric band, wide chart with a narrower setup inspector, and recent-decision table match the concept's hierarchy. | The functional screen includes additional evidence controls and walkthrough state. It is a working owner desk, with empty and error states as well as the initial composition. |
| Copy and account meaning | The principal labels are retained: “Paper desk”, “Evidence first. Every decision recorded.”, “Setup review”, “Evaluate paper setup”, “Recent decisions” and “PAPER ONLY”. | Account values identify USD explicitly. Per-metric help distinguishes synthetic equity, recorded fees, pending/reserved funds and the daily-loss limit, replacing the concept's repeated generic helper text. |
| Typography and density | Locally bundled Roboto, compact labels, restrained heading sizes and tabular/monospaced numeric treatments preserve the reference's information density. | Actual browser glyph rasterisation and line wrapping vary with rendering environment. No exact typographic raster match is asserted. |
| Palette and surfaces | The implementation uses the specified near-black/navy background and panels, quiet borders, pale text and cyan accent, with muted red for negative states. | Functional panels use flat surfaces without added glow; chart and status colours carry meaning. See the exact tokens in the design specification. |
| Logo and provenance | The cyan mark is the owner's canonical Torinteo SVG, preserved at [the branding asset path](../web/public/branding/torinteo-logo.svg). | The concept's framed “TT” text is a placeholder. Replacing it with the exact owned asset is intentional; provenance is recorded in [Branding reference](../design/reference/BRANDING.md). |
| Chart and evidence | The initial desk renders the **five actual synthetic candles**, monthly level at **100.00**, volume, current OHLC and bid/ask evidence. | The concept's larger illustrative candle series and **110.00** resistance are not fixture data. The actual opposing resistance is **105.00**, outside the plotted price range, and is labelled as such instead of distorting the chart to imitate the illustration. |
| Above-fold composition | The core chart/inspector relationship and main action remain recognisable at the desktop viewport. | OHLC, volume, quotes and walkthrough progress add useful screen height. The initial desktop capture is **1536 × 1082**, compared with the **1536 × 1024** concept; the browser viewport itself was 1536 × 1024. |
| Responsive layout | At 390 pixels wide, navigation becomes a scrollable row, metrics form two columns, and the chart and setup inspector stack vertically. | Tables scroll inside their own region and the page becomes taller. Neither the document nor body overflowed horizontally; a full-page mobile image is taller than the 844-pixel viewport. |

### Captured screens

All links below point to saved images from the verification pass:

- [Synthetic desk — desktop](images/desk-desktop.png)
- [Synthetic desk — mobile](images/desk-mobile.png)
- [Public market desk — desktop](images/market-desktop.png)
- [Public market desk — mobile](images/market-mobile.png)
- [Research and levels — desktop](images/research-desktop.png)
- [Paper positions — desktop](images/positions-desktop.png)

## Reproduce the checks

Use Python 3.11 or 3.12 and Node.js 22.12 or newer. CI uses Python 3.11/3.12 for the Python suite, Python 3.12 for the web job, and Node.js 24. Run these commands from the repository root; [Makefile](../Makefile) is the command source.

```sh
make setup
make test
make run
```

Open `http://127.0.0.1:8787`. The default run uses `runtime/web` for durable local application data and serves `web/dist` from the same origin. Keep that data directory to retain experiments across restarts.

The acceptance test creates experiments and advances synthetic paper orders. For an isolated verification run, stop the normal server and start a separate local data directory:

```sh
.venv/bin/python -m torinteo_paper.web \
  --data-dir /tmp/torinteo-markets-web-check \
  --static-dir web/dist
```

In another terminal, install standard Chromium and run the browser test:

```sh
npm --prefix web exec -- playwright install --with-deps chromium
make web-test
```

The equivalent browser command is `npm --prefix web run test:e2e`. It targets `http://127.0.0.1:8787` by default and requires a loopback URL. `TORINTEO_TEST_URL` selects another loopback port; `TORINTEO_SCREENSHOTS` chooses the capture directory. The CI workflow installs Chromium from the `web` working directory with `npx playwright install --with-deps chromium`, starts the server against a temporary data directory, waits for `/api/health`, runs acceptance and retains the screenshots as an artifact.

### Workspace browser fallback and verification limits

The standard Playwright Chromium download CDN was unavailable in this workspace. The completed local browser checks used **Playwright 1.62.1** with the **`@sparticuz/chromium` 153.0.0** executable instead. The smoke script accepts an explicit JSON launch configuration through `TORINTEO_BROWSER_LAUNCH_FILE`; this environment used `/tmp/torinteo-browser-launch.json` to select its extracted `/tmp/chromium` binary and launch arguments:

```sh
TORINTEO_BROWSER_LAUNCH_FILE=/tmp/torinteo-browser-launch.json make web-test
```

Those paths describe the verification environment, not files supplied by the repository. The final acceptance run removed the fallback's `--disable-web-security` argument and passed with web-security checks enabled. The executable still runs without the Linux process sandbox in this isolated workspace. HTTP owner authentication, Host/Origin and path-handling assertions come from the API tests. Standard Chromium is configured for CI and remains a separate validation gate.

The workspace also required its authorized execution mode for local ASGI socket wakeups and outbound public-data HTTPS. This is an execution-environment restriction; a normal local installation needs the documented dependencies and network access for public quotes. No access workaround is part of the application.

## Publication and operational status

The verified deliverable is a local, persistent **paper-only** application. Its synthetic walkthrough and public-data view are usable, and its tests exercise the existing execution engine rather than an in-browser substitute. Optional research/decision providers have not been activated, and no live exchange account or order route has been exercised.

GitHub publication, the standard-browser CI result and container build must be recorded against the published revision before describing those steps as complete. A remote deployment additionally needs durable storage and the network owner-authentication configuration described in [Web app design](WEB_APP_DESIGN.md). This verification does not assert that a hosted service is already running.
