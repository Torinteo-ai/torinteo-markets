"""An invented instrument and scenario; never a market price or backtest."""
from datetime import datetime, timedelta, timezone

from .models import iso


DEMO_TIME = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)


def demo_snapshot(at=DEMO_TIME, *, opposing_price=105.0):
    rows = [
        (99.4, 99.8, 99.1, 99.6),
        (99.6, 100.5, 99.5, 100.3),
        (100.3, 100.8, 100.25, 100.6),
        (100.6, 100.9, 100.3, 100.55),
        (100.5, 100.55, 99.95, 100.22),
    ]
    bars = []
    for index, (open_, high, low, close) in enumerate(rows):
        start = at - timedelta(minutes=(len(rows) - index) * 15)
        bars.append({"open_time": iso(start), "close_time": iso(start + timedelta(minutes=15)),
                     "open": open_, "high": high, "low": low, "close": close, "volume": 1000})
    return {
        "schema_version": 1, "symbol": "TEST/USD", "asset_class": "crypto", "quote_currency": "USD",
        "as_of": iso(at), "timeframe": "15m",
        "source": {"provider": "invented_demo", "kind": "synthetic",
                   "reference": "torinteo_paper/demo_data.py; all prices and reviews are invented"},
        "quote": {"bid": 100.21, "ask": 100.23, "as_of": iso(at), "reference": "invented demo quote"},
        "bars": bars,
        "levels": [
            {"id": "invented-monthly-100", "price": 100.0, "kind": "resistance", "timeframe": "1M",
             "known_at": iso(at - timedelta(days=1)), "reference": "invented monthly review"},
            {"id": "invented-opposing-level", "price": opposing_price, "kind": "resistance", "timeframe": "1w",
             "known_at": iso(at - timedelta(days=1)), "reference": "invented weekly review"},
        ],
        "reviews": {timeframe: {"as_of": iso(at - timedelta(minutes=10)),
                                "reference": "invented review: " + timeframe,
                                "bias": "LONG" if timeframe in {"1d", "4h", "1h"} else "NEUTRAL"}
                    for timeframe in ("1M", "1w", "1d", "4h", "1h")},
        "news": {"status": "clear", "as_of": iso(at - timedelta(minutes=2)),
                 "source": "invented calendar review", "events": []},
    }


def demo_execution_bar(*, ambiguous=False):
    start = DEMO_TIME + timedelta(minutes=15 if ambiguous else 0)
    return {"open_time": iso(start), "close_time": iso(start + timedelta(minutes=15)),
            "open": 100.5 if ambiguous else 100.23,
            "high": 106.0 if ambiguous else 100.8,
            "low": 99.0 if ambiguous else 100.1,
            "close": 100.0 if ambiguous else 100.5, "volume": 1200}


def demo_collection_and_context():
    snapshot = demo_snapshot()
    collection = {
        "schema_version": 1, "source": "invented_demo", "source_kind": "synthetic",
        "reference": snapshot["source"]["reference"], "symbol": snapshot["symbol"],
        "asset_class": snapshot["asset_class"], "timeframe": "15m", "as_of": snapshot["as_of"],
        "observed_at": snapshot["as_of"], "point_in_time_archive": False, "candles": snapshot["bars"],
    }
    context = {key: snapshot[key] for key in
               ("schema_version", "symbol", "asset_class", "quote_currency", "timeframe", "as_of",
                "quote", "levels", "reviews", "news")}
    context.update(source_kind="synthetic", reference="invented demo context; not market evidence")
    return collection, context
