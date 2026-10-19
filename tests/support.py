"""Invented test data, never a quotation or trading recommendation."""
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def snapshot(side="LONG"):
    rows = [
        (99.4, 99.8, 99.1, 99.6),
        (99.6, 100.5, 99.5, 100.3),
        (100.3, 100.8, 100.25, 100.6),
        (100.6, 100.9, 100.3, 100.55),
        (100.5, 100.55, 99.95, 100.22),
    ]
    if side == "SHORT":
        rows = [(200-o, 200-l, 200-h, 200-c) for o,h,l,c in rows]
    bars = []
    for i, (o,h,l,c) in enumerate(rows):
        start = NOW - timedelta(minutes=(len(rows)-i)*15)
        bars.append({"open_time":stamp(start),"close_time":stamp(start+timedelta(minutes=15)),
                     "open":o,"high":h,"low":l,"close":c,"volume":1000})
    price = 100.22 if side == "LONG" else 99.78
    return {
        "schema_version":1,"symbol":"TEST/USD","asset_class":"crypto",
        "as_of":stamp(NOW),"timeframe":"15m",
        "source":{"provider":"synthetic_fixture","kind":"synthetic","reference":"tests/support.py: invented scenario"},
        "quote":{"bid":price-0.01,"ask":price+0.01,"as_of":stamp(NOW)},
        "bars":bars,
        "levels":[
            {"id":"reviewed-monthly-100","price":100,"kind":"resistance" if side=="LONG" else "support",
             "timeframe":"1M","known_at":stamp(NOW-timedelta(days=1)),"reference":"synthetic monthly review"},
            {"id":"opposing-level","price":105 if side=="LONG" else 95,"kind":"resistance" if side=="LONG" else "support",
             "timeframe":"1w","known_at":stamp(NOW-timedelta(days=1)),"reference":"synthetic weekly review"}
        ],
        "reviews":{tf:{"as_of":stamp(NOW-timedelta(minutes=10)),"reference":"synthetic review: "+tf,
                       "bias":side if tf in ("1d","4h","1h") else "NEUTRAL"}
                   for tf in ("1M","1w","1d","4h","1h")},
        "news":{"status":"clear","as_of":stamp(NOW-timedelta(minutes=2)),
                "source":"synthetic calendar review","events":[]},
    }
