"""On-demand public-candle settlement for existing native paper orders only."""
from datetime import timedelta
import hashlib

from ..adapters.public_market import PublicMarketClient, SUPPORTED_SYMBOLS
from ..engine import PaperEngine
from ..gate import canonical
from ..models import iso, utc, validate_bar


_MINUTE = timedelta(minutes=1)
_WINDOW = 100


def _minute_ceiling(value):
    value = utc(value)
    floor = value.replace(second=0, microsecond=0)
    return floor if value == floor else floor + _MINUTE


def _collection(value, instrument, now):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Public sync returned an invalid collection envelope.")
    expected = {"symbol": instrument, "provider_symbol": instrument.replace("/", "-"),
                "asset_class": "crypto", "quote_currency": "USD", "source": "coinbase-public",
                "source_kind": "market", "timeframe": "1m"}
    if any(value.get(key) != wanted for key, wanted in expected.items()):
        raise ValueError("Public sync observations do not match the requested spot instrument and source.")
    started, received = utc(value["as_of"]), utc(value["observed_at"])
    if not started <= received <= now or (now - started).total_seconds() > 120:
        raise ValueError("Public sync observations have stale, future or inconsistent receipt times.")
    if not isinstance(value.get("reference"), str) or not value["reference"].startswith(
            f"https://api.coinbase.com/api/v3/brokerage/market/products/{expected['provider_symbol']}/candles?"):
        raise ValueError("Public sync candle reference does not match its source.")
    rows = value.get("candles")
    if not isinstance(rows, list) or len(rows) != _WINDOW:
        raise ValueError("Public sync needs a complete 100-minute closed-candle window; missing observations cannot be skipped.")
    bars, previous = [], None
    for row in rows:
        bar = validate_bar(row, as_of=started, interval_seconds=60)
        opened, closed = utc(bar["open_time"]), utc(bar["close_time"])
        if opened.second or opened.microsecond or (previous is not None and opened != previous):
            raise ValueError("Public sync candles must be complete, ordered one-minute intervals.")
        previous = closed
        bars.append(bar)
    if previous != started.replace(second=0, microsecond=0):
        raise ValueError("Public sync is missing the latest closed one-minute candle.")
    return {"bars": bars, "received_at": received, "through": previous}


def _state(ledger, instruments):
    with ledger._connection() as db:
        metadata = dict(db.execute("SELECT key,value FROM metadata"))
        decision = db.execute(
            "SELECT occurred_at FROM events WHERE kind='DECISION' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        latest = {instrument: db.execute(
            "SELECT close_time FROM processed_bars WHERE symbol=? ORDER BY open_time DESC LIMIT 1",
            (instrument,)).fetchone() for instrument in instruments}
    return (metadata, {instrument: utc(row[0]) if row else None for instrument, row in latest.items()},
            utc(decision[0]) if decision else None)


def _check_recorded_bars(ledger, instrument, bars):
    by_open = {bar["open_time"]: bar for bar in bars}
    with ledger._connection() as db:
        recorded = db.execute("""SELECT open_time,close_time,provenance,hash FROM processed_bars
            WHERE symbol=? AND close_time>=? AND open_time<=?""",
            (instrument, bars[0]["open_time"], bars[-1]["open_time"])).fetchall()
    for old in recorded:
        # A previously processed 5m/15m interval is not the same observation
        # as a constituent 1m candle. New work starts after its closing time.
        if utc(old["close_time"]) - utc(old["open_time"]) != _MINUTE:
            continue
        bar = by_open.get(old["open_time"])
        if bar is None:
            continue
        digest = hashlib.sha256(canonical({"bar": bar, "provenance": "market"}).encode()).hexdigest()
        if old["provenance"] != "market" or old["hash"] != digest:
            raise ValueError("An already-recorded market candle changed; its history cannot be rewritten. Use a separate experiment.")


def _plan(order, bars, latest, through, event_clock, decision_clock):
    instrument = order["symbol"]
    if order["status"] == "OPEN":
        needed = latest or utc(order["opened_at"])
    else:
        needed = max(_minute_ceiling(order["approved_at"]), latest or utc(order["approved_at"]))
    if needed.second or needed.microsecond:
        raise ValueError(f"{instrument} has an execution boundary that does not align with one-minute data. Supply its original interval observations.")
    available = utc(bars[0]["open_time"])
    expires = utc(order["expires_at"])
    if needed < available:
        if order["status"] == "PENDING" and available >= expires:
            # No fill is invented in an unavailable entry window. A genuinely
            # observed later bar lets the core cancel the expired approval.
            needed = available
        else:
            raise ValueError(f"{instrument} is missing execution history older than the available 100-minute window. Supply complete observations in chronological order; the gap cannot be skipped.")
    if order["status"] == "PENDING" and needed >= expires and decision_clock:
        # Cancellation must not retrospectively remove exposure used by a
        # later recorded decision. It can wait for a later observed bar.
        needed = max(needed, _minute_ceiling(decision_clock))
    selected = [bar for bar in bars if utc(bar["open_time"]) >= needed and utc(bar["close_time"]) <= through]
    if not selected:
        return []
    if utc(selected[0]["open_time"]) != needed:
        raise ValueError(f"{instrument} is missing the next required execution candle.")
    if event_clock:
        if utc(selected[0]["close_time"]) < event_clock:
            raise ValueError(f"{instrument} has unprocessed candles before a newer recorded event. Sync cannot rewrite that history; use a separate chronological experiment.")
    # A committed candle for another symbol may share this candle's close.
    # Its event clock must not masquerade as a newer account/risk decision
    # when recovering an interrupted group. Use actual DECISION receipt time,
    # including NO_TRADE decisions, rather than snapshot time or candle close.
    if decision_clock and order["status"] == "PENDING" and needed < expires and needed < decision_clock:
        raise ValueError(f"{instrument} would create an entry before a newer recorded decision. Sync the outstanding evidence before recording another decision.")
    return [(bar["close_time"], instrument, order["id"], bar) for bar in selected]


def sync_market_experiment(store, identifier, client, clock):
    """Fetch all active pairs, preflight history, then apply a chronological prefix.

    The owner file lock spans collection and processing so another web action
    cannot insert a decision in between. Each core advance remains an atomic
    SQLite transaction; a process interruption can leave a committed prefix,
    which a subsequent sync resumes without duplicate fills or fees.
    """
    with store.locked(identifier):
        experiment = store.metadata(identifier)
        if experiment["kind"] != "market":
            raise ValueError("Market sync requires a market experiment.")
        ledger = store.ledger(identifier)
        active = [order for order in ledger.orders() if order["status"] in {"PENDING", "OPEN"}]
        instruments = sorted({order["symbol"] for order in active})
        if any(instrument not in SUPPORTED_SYMBOLS for instrument in instruments):
            raise ValueError("Market sync supports existing BTC/USD, ETH/USD and SOL/USD spot paper orders only.")
        if any(order["provenance"] != "market" for order in active):
            raise ValueError("Market sync cannot mix paper evidence provenance.")
        metadata, latest, decision_clock = _state(ledger, instruments)
        if active and metadata.get("executor") != "native":
            raise ValueError("Market sync requires the native paper executor.")
        event_clock = utc(metadata["event_clock"]) if metadata.get("event_clock") else None
        logical_clock = utc(metadata["logical_clock"]) if metadata.get("logical_clock") else None
        collections, work = {}, []
        if active:
            client = client or PublicMarketClient()
            # The small fixed universe is deliberately collected sequentially.
            # No state changes occur until every required feed validates.
            for instrument in instruments:
                raw = client.collect(symbol=instrument, timeframe="1m", limit=_WINDOW)
                collections[instrument] = _collection(raw, instrument, utc(clock()))
            through = min(item["through"] for item in collections.values())
            for order in active:
                instrument = order["symbol"]
                bars = collections[instrument]["bars"]
                _check_recorded_bars(ledger, instrument, bars)
                work.extend(_plan(order, bars, latest[instrument], through, event_clock, decision_clock))
            work.sort(key=lambda item: (item[0], item[1]))
        else:
            through = None
        processed, processed_through = 0, None
        now = utc(clock())
        if logical_clock and now < logical_clock:
            raise ValueError("Market sync cannot move the experiment receipt clock backwards.")
        engine = PaperEngine(ledger, clock=clock)
        for _, instrument, order_id, bar in work:
            with ledger._connection() as db:
                row = db.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()
            if not row or row[0] not in {"PENDING", "OPEN"}:
                continue
            observed = utc(clock())
            if observed < now or observed < collections[instrument]["received_at"]:
                raise ValueError("Market sync receipt time moved backwards; no observation time is invented.")
            engine.advance(instrument, bar, observed_at=observed, provenance="market", timeframe="1m")
            processed += 1
            processed_through = bar["close_time"]
            now = observed
        result = store.detail(identifier)
        result["sync"] = {
            "status": "updated" if processed else "waiting" if active else "idle",
            "processed_bars": processed,
            "symbols": instruments,
            "source": "Coinbase public data",
            "timeframe": "1m",
            "observed_at": iso(now),
            "through": iso(through) if through else None,
            "last_processed_close": processed_through,
            "runs_continuously": False,
        }
        return result
