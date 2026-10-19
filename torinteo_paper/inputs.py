"""Join explicitly sourced evidence without inventing its missing parts."""
from copy import deepcopy
import json
from pathlib import Path

from .models import iso, label, utc, validate_bar, validate_snapshot


def read_json(path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON keys are not allowed")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError("JSON numbers must be finite")

    with Path(path).open("rb") as handle:
        raw = handle.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("JSON input exceeds 2 MiB")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("input must be a bounded UTF-8 JSON object") from exc
    if not isinstance(result, dict):
        raise ValueError("JSON input must be an object")
    return result


def write_json(path, value):
    # Compute before touching the output; all artifacts use strict finite JSON.
    raw = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        handle.write(raw)


def assemble_snapshot(collection, context, *, limit=1000):
    """Validate recorded identities/times; labels do not certify a data source."""
    if type(limit) is not int or not 5 <= limit <= 1000:
        raise ValueError("setup window must contain 5 to 1000 bars")
    if not isinstance(collection, dict) or not isinstance(context, dict):
        raise ValueError("collection and context must be objects")
    if any(type(item.get("schema_version")) is not int or item["schema_version"] != 1
           for item in (collection, context)):
        raise ValueError("unsupported collection/context schema")
    for key in ("symbol", "asset_class", "timeframe", "source_kind"):
        if collection.get(key) != context.get(key) or key not in collection:
            raise ValueError("collection and context identity/provenance mismatch")
    kind = collection["source_kind"]
    if kind not in {"market", "historical", "synthetic"}:
        raise ValueError("invalid collection provenance")
    if collection["timeframe"] != "15m":
        raise ValueError("setup assembly requires 15m candles")
    label(collection["source"], "collection source", 120)
    label(collection["reference"], "collection reference")
    label(context["reference"], "context reference")
    label(context["quote"]["reference"], "quote reference")
    as_of, collected_at = utc(context["as_of"]), utc(collection["observed_at"])
    if utc(collection["as_of"]) > collected_at or collected_at > as_of:
        raise ValueError("collection was not available at the context decision time")
    if kind == "historical" and collection.get("point_in_time_archive") is not True:
        raise ValueError("historical assembly requires an explicit point-in-time archive")
    candles = collection["candles"]
    if not isinstance(candles, list):
        raise ValueError("candles must be an array")
    for candle in candles:
        validate_bar(candle, as_of=collection["as_of"], interval_seconds=900)
    snapshot = {key: deepcopy(context[key]) for key in
                ("schema_version", "symbol", "asset_class", "quote_currency", "timeframe", "as_of",
                 "quote", "levels", "reviews", "news")}
    snapshot.update(bars=deepcopy(candles[-limit:]), source={
        "provider": collection["source"], "kind": kind, "reference": collection["reference"],
        "collected_at": iso(collected_at), "context_reference": context["reference"],
        "point_in_time_archive": collection.get("point_in_time_archive") is True,
    })
    validate_snapshot(snapshot)
    return snapshot
