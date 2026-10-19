"""Sync tests use invented provider responses and actual temporary SQLite ledgers."""
from datetime import timedelta
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.support import NOW, snapshot
from torinteo_paper.adapters.public_market import PublicMarketUnavailable
from torinteo_paper.engine import PaperEngine
from torinteo_paper.models import iso, utc
from torinteo_paper.web.store import ExperimentStore
from torinteo_paper.web.sync import sync_market_experiment


def execution_bar(start, **changes):
    return {"open_time": iso(start), "close_time": iso(start + timedelta(minutes=1)),
            "open": 100.23, "high": 100.8, "low": 100.1, "close": 100.5, "volume": 1000,
            **changes}


class PublicFeedDouble:
    def __init__(self, clock):
        self.clock, self.calls = clock, []
        self.windows, self.edits = {}, {}
        self.fail_symbol = None

    def collect(self, *, symbol, timeframe, limit):
        self.calls.append((symbol, timeframe, limit))
        if symbol == self.fail_symbol:
            raise PublicMarketUnavailable("test provider unavailable")
        now = self.windows.get(symbol, self.clock())
        started = now - timedelta(seconds=1)
        end = started.replace(second=0, microsecond=0)
        bars = [execution_bar(end - timedelta(minutes=limit - index)) for index in range(limit)]
        result = {"schema_version": 1, "symbol": symbol, "provider_symbol": symbol.replace("/", "-"),
                  "asset_class": "crypto", "quote_currency": "USD", "source": "coinbase-public",
                  "source_kind": "market", "timeframe": timeframe, "as_of": iso(started),
                  "observed_at": iso(now), "point_in_time_archive": False,
                  "reference": f"https://api.coinbase.com/api/v3/brokerage/market/products/{symbol.replace('/', '-')}/candles?test_only=invented",
                  "candles": bars, "quote": {"bid": 100.22, "ask": 100.23, "as_of": iso(started)}}
        if symbol in self.edits:
            self.edits[symbol](result)
        return result


class WebSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.now = NOW + timedelta(seconds=30)
        self.store = ExperimentStore(self.directory.name, clock=lambda: self.now)
        self.identifier = self.store.create("Invented market-provider contract", "market")["id"]
        self.feed = PublicFeedDouble(lambda: self.now)

    def tearDown(self):
        self.directory.cleanup()

    def approve(self, instrument="BTC/USD"):
        data = snapshot()
        data.update(symbol=instrument, as_of=iso(self.now))
        data["source"]["kind"] = "market"
        data["source"]["reference"] = "Invented provider-contract test; not market evidence"
        data["quote"]["as_of"] = iso(self.now)
        result = PaperEngine(self.store.ledger(self.identifier), clock=lambda: self.now).run(data, now=self.now)
        self.assertEqual(result["call"], "LONG", result["reason_codes"])
        return result

    def sync(self):
        return sync_market_experiment(self.store, self.identifier, self.feed, lambda: self.now)

    def events(self):
        return self.store.ledger(self.identifier).events()

    def processed(self):
        with self.store.ledger(self.identifier)._connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM processed_bars ORDER BY rowid")]

    def test_empty_market_sync_does_not_fetch_or_create_evidence(self):
        result = self.sync()
        self.assertEqual(result["sync"]["status"], "idle")
        self.assertEqual(result["sync"]["processed_bars"], 0)
        self.assertEqual(self.feed.calls, [])
        self.assertEqual(result["orders"], [])
        self.assertIsNone(result["latest_snapshot"])

    def test_nonmarket_experiment_rejected_without_network(self):
        identifier = self.store.create("Synthetic", "synthetic")["id"]
        with self.assertRaisesRegex(ValueError, "market experiment"):
            sync_market_experiment(self.store, identifier, self.feed, lambda: self.now)
        self.assertEqual(self.feed.calls, [])

    def test_first_eligible_closed_bar_never_precedes_approval(self):
        decision = self.approve()
        self.now = NOW + timedelta(minutes=1, seconds=10)
        waiting = self.sync()
        self.assertEqual(waiting["sync"]["processed_bars"], 0)
        self.assertEqual(waiting["orders"][0]["status"], "PENDING")
        self.now = NOW + timedelta(minutes=2, seconds=10)
        filled = self.sync()
        self.assertEqual(filled["sync"]["processed_bars"], 1)
        self.assertEqual(filled["orders"][0]["status"], "OPEN")
        self.assertEqual(filled["orders"][0]["opened_at"], iso(NOW + timedelta(minutes=1)))
        self.assertGreaterEqual(utc(filled["orders"][0]["opened_at"]), utc(decision["approved_at"]))
        self.assertEqual(self.feed.calls[-1], ("BTC/USD", "1m", 100))

    def test_repeat_and_restart_are_idempotent(self):
        self.approve(); self.now = NOW + timedelta(minutes=2, seconds=10)
        first = self.sync()
        original_events = self.events()
        self.store = ExperimentStore(self.directory.name, clock=lambda: self.now)
        repeated = self.sync()
        self.assertEqual(repeated["sync"]["processed_bars"], 0)
        self.assertEqual(first["orders"], repeated["orders"])
        self.assertEqual(original_events, self.events())
        self.assertEqual(len(self.processed()), 1)

    def test_open_position_cannot_jump_older_than_available_window(self):
        self.approve(); self.now = NOW + timedelta(minutes=2, seconds=10); self.sync()
        original_events = self.events()
        self.now = NOW + timedelta(hours=3, seconds=10)
        with self.assertRaisesRegex(ValueError, "100-minute window"):
            self.sync()
        self.assertEqual(self.events(), original_events)
        self.assertEqual(self.store.ledger(self.identifier).orders()[0]["status"], "OPEN")

    def test_incomplete_provider_window_is_rejected_before_any_write(self):
        self.approve(); self.now = NOW + timedelta(minutes=3, seconds=10)
        self.feed.edits["BTC/USD"] = lambda data: data["candles"].pop(-2)
        original_events = self.events()
        with self.assertRaisesRegex(ValueError, "complete"):
            self.sync()
        self.assertEqual(self.events(), original_events)
        self.assertEqual(self.processed(), [])

    def test_two_symbols_are_processed_in_global_candle_order(self):
        self.approve("BTC/USD"); self.now = NOW + timedelta(seconds=40); self.approve("ETH/USD")
        self.now = NOW + timedelta(minutes=3, seconds=10)
        def stop_at_three(data):
            data["candles"][-1].update(high=106, low=99, close=100)
        self.feed.edits["BTC/USD"] = stop_at_three
        result = self.sync()
        self.assertEqual(result["sync"]["processed_bars"], 4)
        rows = self.processed()
        self.assertEqual([(row["close_time"], row["symbol"]) for row in rows], sorted((row["close_time"], row["symbol"]) for row in rows))
        statuses = {order["symbol"]: order["status"] for order in result["orders"]}
        self.assertEqual(statuses, {"BTC/USD": "CLOSED", "ETH/USD": "OPEN"})
        self.assertEqual(result["orders"][0]["exit_reason"], "STOP_LOSS")
        self.assertTrue(next(event for event in result["events"] if event["kind"] == "PAPER_CLOSE")["payload"]["intrabar_ambiguous"])

    def test_different_capture_boundaries_use_common_closed_cutoff(self):
        self.approve("BTC/USD"); self.now = NOW + timedelta(seconds=40); self.approve("ETH/USD")
        self.now = NOW + timedelta(minutes=3, seconds=10)
        self.feed.windows["BTC/USD"] = NOW + timedelta(minutes=2, seconds=55)
        result = self.sync()
        self.assertEqual(result["sync"]["through"], iso(NOW + timedelta(minutes=2)))
        self.assertEqual(result["sync"]["processed_bars"], 2)
        self.assertEqual({row["close_time"] for row in self.processed()}, {iso(NOW + timedelta(minutes=2))})
        self.feed.windows.clear()
        result = self.sync()
        self.assertEqual(result["sync"]["processed_bars"], 2)
        self.assertEqual(result["sync"]["through"], iso(NOW + timedelta(minutes=3)))

    def test_second_provider_failure_leaves_both_orders_unchanged(self):
        self.approve("BTC/USD"); self.now = NOW + timedelta(seconds=40); self.approve("ETH/USD")
        self.now = NOW + timedelta(minutes=3, seconds=10)
        self.feed.fail_symbol = "ETH/USD"
        original_events = self.events()
        with self.assertRaises(PublicMarketUnavailable): self.sync()
        self.assertEqual(self.events(), original_events)
        self.assertEqual(self.processed(), [])

    def test_newer_decision_prevents_retroactive_entry_and_fee(self):
        self.approve("BTC/USD")
        self.now = NOW + timedelta(minutes=2, seconds=30)
        self.approve("ETH/USD")
        self.now = NOW + timedelta(minutes=4, seconds=10)
        original_events = self.events()
        with self.assertRaisesRegex(ValueError, "newer recorded event"):
            self.sync()
        self.assertEqual(self.events(), original_events)
        self.assertEqual(self.processed(), [])

    def test_decision_inside_execution_candle_still_blocks_retroactive_entry(self):
        self.approve("BTC/USD")
        self.now = NOW + timedelta(minutes=1, seconds=30)
        self.approve("ETH/USD")
        self.now = NOW + timedelta(minutes=2, seconds=10)
        original_events = self.events()
        # The candle closes after the newer decision, but its entry and fee
        # would occur before that decision's account/risk checks.
        with self.assertRaisesRegex(ValueError, "entry before a newer recorded"):
            self.sync()
        self.assertEqual(self.events(), original_events)
        self.assertEqual(self.processed(), [])

    def test_no_trade_decision_uses_receipt_time_instead_of_snapshot_time(self):
        self.approve("BTC/USD")
        data = snapshot()
        data.update(symbol="BTC/USD", as_of=iso(NOW + timedelta(seconds=50)))
        data["source"].update(kind="market", reference="Invented provider-contract test; not market evidence")
        data["quote"]["as_of"] = data["as_of"]
        self.now = NOW + timedelta(minutes=1, seconds=30)
        decision = PaperEngine(self.store.ledger(self.identifier), clock=lambda: self.now).run(data, now=self.now)
        self.assertEqual(decision["call"], "NO_TRADE")
        self.now = NOW + timedelta(minutes=2, seconds=10)
        original_events = self.events()
        # Even a rejected decision used the account as of its actual receipt,
        # after this outstanding candle's entry, despite its older snapshot.
        with self.assertRaisesRegex(ValueError, "entry before a newer recorded decision"):
            self.sync()
        self.assertEqual(self.events(), original_events)
        self.assertEqual(self.processed(), [])

    def test_expired_pending_without_entry_window_is_cancelled_without_fill(self):
        self.approve(); self.now = NOW + timedelta(hours=3, seconds=10)
        result = self.sync()
        self.assertEqual(result["orders"][0]["status"], "CANCELLED")
        self.assertEqual(result["orders"][0]["exit_reason"], "APPROVAL_EXPIRED")
        self.assertIsNone(result["orders"][0]["opened_at"])
        self.assertEqual(result["orders"][0]["fees"], 0)
        self.assertEqual(result["sync"]["processed_bars"], 1)

    def test_conflicting_already_recorded_minute_bar_is_rejected(self):
        self.approve(); self.now = NOW + timedelta(minutes=2, seconds=10); self.sync()
        self.now = NOW + timedelta(minutes=3, seconds=10)
        def change_previous(data): data["candles"][-2]["high"] = 101.8
        self.feed.edits["BTC/USD"] = change_previous
        original_events = self.events()
        with self.assertRaisesRegex(ValueError, "already-recorded market candle changed"):
            self.sync()
        self.assertEqual(self.events(), original_events)
        self.assertEqual(len(self.processed()), 1)

    def test_one_minute_sync_follows_processed_fifteen_minute_boundary(self):
        self.now = NOW
        self.approve()
        bar = execution_bar(NOW, close_time=iso(NOW + timedelta(minutes=15)))
        engine = PaperEngine(self.store.ledger(self.identifier), clock=lambda: self.now)
        engine.advance("BTC/USD", bar, observed_at=NOW + timedelta(minutes=15), provenance="market", timeframe="15m")
        self.now = NOW + timedelta(minutes=16, seconds=10)
        result = self.sync()
        self.assertEqual(result["orders"][0]["status"], "OPEN")
        self.assertEqual(result["sync"]["processed_bars"], 1)
        self.assertEqual(self.processed()[-1]["open_time"], iso(NOW + timedelta(minutes=15)))

    def test_late_pending_after_coarse_bar_expires_without_retroactive_fill(self):
        self.approve()
        bar = execution_bar(NOW, close_time=iso(NOW + timedelta(minutes=15)))
        engine = PaperEngine(self.store.ledger(self.identifier), clock=lambda: self.now)
        engine.advance("BTC/USD", bar, observed_at=NOW + timedelta(minutes=15), provenance="market", timeframe="15m")
        self.now = NOW + timedelta(minutes=16, seconds=10)
        result = self.sync()
        self.assertEqual(result["orders"][0]["status"], "CANCELLED")
        self.assertIsNone(result["orders"][0]["opened_at"])

    def test_unsupported_pair_rejected_before_network(self):
        self.approve("TEST/USD")
        with self.assertRaisesRegex(ValueError, "BTC/USD"):
            self.sync()
        self.assertEqual(self.feed.calls, [])

    def test_invalid_collection_identity_or_future_receipt_rejected(self):
        self.approve(); self.now = NOW + timedelta(minutes=2, seconds=10)
        for key, value in (("symbol", "ETH/USD"), ("observed_at", iso(self.now + timedelta(minutes=1)))):
            with self.subTest(key=key):
                self.feed.edits["BTC/USD"] = lambda data, key=key, value=value: data.update({key: value})
                with self.assertRaises(ValueError): self.sync()
                self.assertEqual(self.processed(), [])

    def test_partial_interruption_resumes_without_a_second_entry_fee(self):
        self.approve(); self.now = NOW + timedelta(minutes=3, seconds=10)
        original = PaperEngine.advance
        calls = 0
        def interrupted(engine, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: raise RuntimeError("simulated process interruption")
            return original(engine, *args, **kwargs)
        with patch("torinteo_paper.web.sync.PaperEngine.advance", interrupted):
            with self.assertRaisesRegex(RuntimeError, "simulated process"):
                self.sync()
        fee = self.store.ledger(self.identifier).orders()[0]["fees"]
        self.store = ExperimentStore(self.directory.name, clock=lambda: self.now)
        resumed = self.sync()
        self.assertEqual(resumed["sync"]["processed_bars"], 1)
        self.assertEqual(resumed["orders"][0]["fees"], fee)
        self.assertEqual(len([event for event in resumed["events"] if event["kind"] == "PAPER_FILL"]), 1)
        self.assertEqual(len(self.processed()), 2)

    def test_restart_resumes_other_symbol_at_same_candle_close(self):
        self.approve("BTC/USD")
        self.now = NOW + timedelta(seconds=40)
        self.approve("ETH/USD")
        self.now = NOW + timedelta(minutes=3, seconds=10)
        original = PaperEngine.advance
        calls = 0
        def interrupted(engine, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated cross-symbol interruption")
            return original(engine, *args, **kwargs)
        with patch("torinteo_paper.web.sync.PaperEngine.advance", interrupted):
            with self.assertRaisesRegex(RuntimeError, "cross-symbol interruption"):
                self.sync()
        prefix = self.store.ledger(self.identifier).orders()
        self.assertEqual([order["status"] for order in prefix], ["OPEN", "PENDING"])
        first_fee = prefix[0]["fees"]
        self.assertGreater(first_fee, 0)
        self.store = ExperimentStore(self.directory.name, clock=lambda: self.now)
        resumed = self.sync()
        self.assertEqual(resumed["sync"]["processed_bars"], 3)
        self.assertEqual([order["status"] for order in resumed["orders"]], ["OPEN", "OPEN"])
        self.assertEqual({order["opened_at"] for order in resumed["orders"]}, {iso(NOW + timedelta(minutes=1))})
        self.assertEqual(resumed["orders"][0]["fees"], first_fee)
        self.assertEqual(len([event for event in resumed["events"] if event["kind"] == "PAPER_FILL"]), 2)
        expected = [(iso(NOW + timedelta(minutes=minute)), instrument)
                    for minute in (2, 3) for instrument in ("BTC/USD", "ETH/USD")]
        self.assertEqual([(row["close_time"], row["symbol"]) for row in self.processed()], expected)
        repeated = self.sync()
        self.assertEqual(repeated["sync"]["processed_bars"], 0)
        self.assertEqual(repeated["orders"], resumed["orders"])
        self.assertEqual(repeated["events"], resumed["events"])

    def test_restart_preserves_same_candle_expiry_cancellation_time(self):
        self.identifier = self.store.create("Invented short-lived approvals", "market",
                                            {"ticket_ttl_seconds": 10})["id"]
        self.approve("BTC/USD")
        self.now = NOW + timedelta(seconds=40)
        self.approve("ETH/USD")
        self.now = NOW + timedelta(minutes=2, seconds=10)
        original = PaperEngine.advance
        calls = 0
        def interrupted(engine, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated expiry interruption")
            return original(engine, *args, **kwargs)
        with patch("torinteo_paper.web.sync.PaperEngine.advance", interrupted):
            with self.assertRaisesRegex(RuntimeError, "expiry interruption"):
                self.sync()
        self.store = ExperimentStore(self.directory.name, clock=lambda: self.now)
        resumed = self.sync()
        self.assertEqual(resumed["sync"]["processed_bars"], 1)
        self.assertEqual([order["status"] for order in resumed["orders"]], ["CANCELLED", "CANCELLED"])
        self.assertEqual({order["closed_at"] for order in resumed["orders"]}, {iso(NOW + timedelta(minutes=1))})
        self.assertTrue(all(order["opened_at"] is None and order["fees"] == 0 for order in resumed["orders"]))
        self.assertEqual(len(self.processed()), 2)
