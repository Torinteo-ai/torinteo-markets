# Optional Jev assessment

Verified against TypeSafe documentation and the cited repository code on **19 October 2026**. This module uses Python's standard library and does not require a TypeSafe SDK dependency.

## Interface

```python
from torinteo_paper.adapters.jev import JevClient

client = JevClient(
    api_key=None,                  # Reads only TYPESAFE_API_KEY when None.
    model="jev-1.13.0",           # Explicit, versioned model.
    timeout_seconds=2.0,
    transport=None,               # Optional trusted test transport.
)
# Calling evaluate with valid configuration makes a paid provider request.
# assessment = client.evaluate(deterministic_evidence)
```

`evaluate(state: dict) -> dict` always returns the following fields for normal input, provider and validation failures:

| Field | Meaning |
| --- | --- |
| `status` | `ok`, `unavailable`, or `invalid` |
| `provider` | Always `jev` |
| `model` | The configured and verified explicit model, or `None` for invalid configuration |
| `choice` | `pass` or `reject` on a valid response; otherwise `None` |
| `confidence` | Provider Choice confidence in `[0, 1]`; otherwise `None` |
| `probabilities` | Exactly `pass` and `reject` on a valid response; otherwise `None` |
| `reason` | A fixed local reason code, without raw remote errors or credentials |
| `latency_ms` | Measured adapter elapsed time |

The request is `POST https://api.typesafe.ai/v1/systemone`, authenticated through `Authorization: Bearer ...`. It contains the explicit model, the supplied state and one Choice question named `entry_gate`. The two criteria are application-defined `pass` and `reject`. There is no invented native BUY/SELL/HOLD API contract. The full provider response must contain `model`, `answers`, and `usage`; the returned version must match the requested version exactly. [Official HTTP schema](https://api.typesafe.ai/openapi.json).

The optional test transport has the signature `(url, headers, payload_bytes, timeout_seconds) -> dict`. It is trusted application code and receives the authorization header; do not log its arguments or accept transport code from a user. Every automated adapter test replaces network access with this seam or a simulated urllib response.

## Entry-policy boundary

The adapter reports an assessment. The engine retains all entry authority and its existing deterministic vetoes. It chooses whether Jev is disabled, shadow-only, or required. An unavailable or invalid response has no usable choice and cannot approve an entry. A valid `pass` is still subject to the engine's confidence threshold, freshness rules and hard risk checks. Risk-reducing exits do not depend on Jev.

Only aggregate deterministic evidence belongs in state. Do not send personal account records, broker keys or credentials. The adapter rejects common credential-field names recursively and refuses a payload containing its own API key, but that safeguard is not a general detector for every possible secret. The calling engine owns the evidence allowlist.

## Transport and validation

* The HTTPS origin and path are fixed. Redirects and environment-configured proxies are disabled; the effective response URL is checked before reading a body. Certificate verification remains enabled through urllib's standard HTTPS handler.
* Requests are limited to **32 KiB**, including question text. Responses are limited to **64 KiB**, both by declared length and a bounded read. Input nesting and node counts are bounded. Compressed or non-JSON responses are rejected.
* There are no retries. The configured socket timeout must be finite, positive and at most 30 seconds. Any response that arrives after the elapsed timeout is discarded. urllib's socket timeout is not a guarantee that a slow peer cannot keep the call occupied longer; the caller should also expire candidates independently.
* Duplicate JSON fields, non-finite values, boolean probabilities, unknown labels, missing fields, unexpected models and malformed usage counts are rejected. Probabilities must have exactly the two requested choices, lie in `[0,1]`, sum to one within `0.001`, and select a maximum-probability choice.
* No missing-key fallback fabricates a result. HTTP authentication errors, rate limits, timeouts and provider failures produce fixed reason codes. The module never prints provider exceptions or credentials.

## What confidence means

TypeSafe defines Choice confidence as `(max_probability - 1/n) / (1 - 1/n)` for `n` options. With two choices, a top probability of `0.9` corresponds to confidence `0.8`. The adapter preserves the returned confidence separately from the probability distribution. Neither number has been validated here as a probability of a profitable trade. The engine must evaluate any thresholds using its own held-out, time-correct observations. [TypeSafe confidence documentation](https://docs.typesafe.ai/confidence).

The provider's documented weaknesses include numerical precision and date comparisons. Keep prices, calculations, news windows, monthly levels, retest confirmation, position sizing and protective-order rules in code. [Documented Jev 1.13 limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13).

## Versions, cost and upstream references

At the verification date, TypeSafe listed `jev-1.13.0` and `jev-1.14.0` at **$0.042 per million input tokens**, with output tokens free. Its aliases pointed `jev-latest` to 1.13.0 and `jev-preview` to 1.14.0. This adapter accepts those two explicit version IDs and rejects moving aliases. Actual account access and rates must be checked before activation. [Official models and pricing](https://docs.typesafe.ai/models).

The published 70–500 ms range is a vendor latency claim, with launch measurements generally run near its US West Coast service. It is not a measured guarantee for this worker. [TypeSafe launch description](https://typesafe.ai/blog/introducing-system-one-models-and-jev).

QuantDinger provides a useful implementation reference for typed pre-entry checks. At inspected commit `f5da57a0e01220ec87b7bc3badc2c6871bfcce22`, its gate combines five Choice assessments and fails open on provider failure. This worker does not inherit that entry policy. [Inspected QuantDinger filter](https://github.com/OpenByteInc/QuantDinger/blob/f5da57a0e01220ec87b7bc3badc2c6871bfcce22/backend_api_python/app/services/ai_decision_filter.py).

The separate `jarrodwatts/jev-trader` example asks buy/sell for Kuru MON-USDC, defaults to a 100-block horizon, and switches to a labelled mock without an API key. Its market-making strategy is not this worker's monthly-level/retest strategy. [Inspected example model](https://github.com/jarrodwatts/jev-trader/blob/d6254bf36a7875e009533fb722c1af8977b96e2d/src/model.ts).

## Verification command

```sh
python -m unittest discover -s tests -p test_jev.py -v
```

These tests establish local request construction, schema validation and failure behaviour. They do not establish real Jev access, live latency or trading performance. No inference endpoint was called during this implementation.
