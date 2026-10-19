# Public spot market data

Torinteo Markets can collect current Coinbase spot candles and a public bid/ask
quote without an exchange account, API key or third-party Python package.
`PublicMarketClient` is a read-only source adapter. It does not discover reviewed
levels, determine higher-timeframe bias, clear the economic calendar, or issue
orders.

## Supported observations

| Selection | Supported values |
| --- | --- |
| Canonical instruments | `BTC/USD`, `ETH/USD`, `SOL/USD` |
| Provider products | `BTC-USD`, `ETH-USD`, `SOL-USD`, respectively |
| Quote currency | Actual `USD`; no substitution with USDT, USDC or GBP |
| Candle timeframes | `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d` |
| Closed candles per request | 5–300; default 100 |
| Provider | Coinbase Advanced Trade public REST endpoints |
| Credentials and order access | Neither is part of this adapter |

The initial strategy still requires **15-minute setup candles**. Other supported
timeframes are available for chart inspection and evidence preparation; they do
not change that strategy requirement. Monthly and weekly reviewed levels remain
independent evidence. Forex, commodities, indices and other quote currencies are
not covered by this source.

Coinbase documents [public product candles](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/get-public-product-candles)
and a [public product book](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/get-public-product-book).
Its [REST endpoint guide](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/rest-api)
states that these public routes do not require a JWT and that public responses
are ordinarily cached for one second. The adapter sends `Cache-Control:
no-cache` and preserves the book's own timestamp. The feed is a REST observation,
not a subscribed streaming connection.

## Application integration

```python
from torinteo_paper.adapters.public_market import (
    PublicMarketClient,
    PublicMarketError,
    PublicMarketUnavailable,
    SUPPORTED_SYMBOLS,
    SUPPORTED_TIMEFRAMES,
)

client = PublicMarketClient(timeout_seconds=8, max_quote_age_seconds=30)
collection = client.collect(symbol="BTC/USD", timeframe="15m", limit=100)
```

Use a same-origin application endpoint to make this server-side call and return
the collection. A request selects a whitelisted instrument, timeframe and bounded
candle count; it cannot choose a hostname or arbitrary API route. The adapter
uses two fixed public `GET` routes and rejects redirects. The book is retrieved
after the candles to obtain the latest available quote.

On `PublicMarketUnavailable`, show that the provider is unavailable. On
`PublicMarketError`, show that the observations failed validation and need a
refresh. Neither failure supplies fabricated data. An application may retain an
earlier chart for inspection, but it must retain its original timestamps and
clearly identify it as an earlier observation. It must not relabel that chart
with a new collection time or use a stale quote for entry evaluation.

Avoid a new provider request on every render or per visitor. A server-side
refresh action can deduplicate concurrent requests. Any retained observation
must still pass the entry engine's freshness checks; a UI cache is not evidence
of quote freshness.

## Collection contract

The result uses the collection envelope consumed by `assemble_snapshot` and
adds a public quote:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `symbol`, `provider_symbol` | Canonical instrument and exact Coinbase product |
| `asset_class`, `base_currency`, `quote_currency` | Spot crypto identity and currencies |
| `source`, `source_kind` | `coinbase-public`, `market` |
| `reference` | The public candle URL, including the actual requested time window |
| `as_of` | Actual time the candle collection started; closed-candle cutoff |
| `candles_observed_at` | Time the candle response was received |
| `observed_at` | Time the subsequent quote response was received |
| `candles` | Ordered, contiguous, closed OHLCV observations with UTC open/close times |
| `excluded_open_candles` | Number of returned unfinished candle buckets excluded |
| `quote.bid`, `quote.ask` | Provider bid/ask, converted from numeric strings |
| `quote.as_of` | The provider's order-book timestamp |
| `quote.observed_at` | Receipt time, kept separate from provider time |
| `quote.reference`, `quote.provider` | The public book URL and source |
| `point_in_time_archive` | `False`; a current retrieval is not a historical archive |

To assemble a strategy snapshot, supply the independent reviewed context using
the same canonical instrument, timeframe and `source_kind`. Its decision
`as_of` must be **at or after `collection.observed_at`**, because the observations
were not available before receipt. Copy the observed quote with its original
`as_of` and reference; do not use the collection's earlier request-start time as
a later decision's timestamp. `assemble_snapshot` validates the remaining
reviewed levels, timeframe reviews, news and identity requirements.

The public quote alone cannot produce a valid strategy snapshot. The adapter
deliberately returns no `levels`, `reviews` or `news` fields.

## Validation behavior

The adapter requires all requested closed intervals, including the most recent
closed interval. It rejects duplicates, missing intervals, a mismatched
instrument, unaligned timestamps, future/out-of-window bars and contradictory or
non-finite OHLCV values. It excludes the current unfinished candle. Missing bars
are not synthesized or forward-filled.

The book must contain the requested instrument and one nonempty bid and ask,
with positive prices and size. Crossed prices are rejected. The provider quote
timestamp must not be in the future and must be no more than 30 seconds old at
receipt by default. Applications may configure a tighter limit; the adapter's
maximum is 60 seconds. The trading engine applies its own freshness check again
at evaluation time.

If collection crosses a candle boundary, it raises an error so a fresh request
can obtain the newly closed bar. This avoids combining an old candle cutoff with
a quote observed after the next interval closed. The caller should expose a
refresh action rather than treating the earlier series as current.

Responses are bounded to 1 MiB, parsed as strict JSON, and rejected on duplicate
JSON keys. Network errors are returned without proxy internals or raw upstream
error messages. Network timeouts apply per request; they are not a guarantee of
hard wall-clock cancellation of operating-system DNS or socket work.

## Verification recorded on 19 October 2026

The default adapter, using real HTTPS responses and no injected transport, was
verified against all three configured instruments:

| Instrument | Quote received (UTC) | Provider quote time (UTC) | Closed 15m candles | Latest candle closed (UTC) |
| --- | --- | --- | ---: | --- |
| BTC/USD | 09:13:28.063511 | 09:13:26.881389 | 100 | 09:00:00 |
| ETH/USD | 09:14:35.786846 | 09:14:35.269677 | 100 | 09:00:00 |
| SOL/USD | 09:14:36.026443 | 09:14:34.694660 | 100 | 09:00:00 |

Each collection excluded one open candle. These are integration-check receipt
times, not a continuously monitored feed or strategy-performance result. Price
values from those checks are not bundled as a live-data fixture.

BTC/USD was also verified with 100 closed **one-minute** candles at
09:16:08.336636 UTC (latest close 09:16:00 UTC) and 100 closed **five-minute**
candles at 09:16:08.495788 UTC (latest close 09:15:00 UTC). The one-minute response
did not include an open bucket; the five-minute response included one, which
was excluded. A separate 15-minute response received at 09:16:08.660902 UTC was
saved only as a temporary local UI-QA input; it is not bundled with the app.

The initial restricted execution sandbox blocked outbound network access with
`Operation not permitted`. The verification succeeded through the environment's
authorized read-only network escalation. A deployment needs ordinary outbound
HTTPS access to `api.coinbase.com`; the Python adapter cannot grant that access.

`python3 -m unittest tests.test_public_market -q` passes 20 contract tests.
Those tests use explicitly invented numeric values and cover the documented
interval mappings, observation chronology, source identity, missing/stale data,
JSON bounds and failure behavior. The real provider checks above exercised the
1-minute, 5-minute and 15-minute paths; the other interval mappings were verified
against the official API contract and controlled tests.
