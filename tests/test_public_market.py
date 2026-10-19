"""Public-data contract tests use invented prices, never recorded live quotes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from torinteo_paper.adapters.public_market import (
    PublicMarketClient, PublicMarketError, PublicMarketUnavailable,
    _decode_json,
)
from torinteo_paper.models import iso, validate_bar


NOW = datetime(2026, 10, 19, 9, 8, tzinfo=timezone.utc)


def payloads(*, interval=900, count=6, product="BTC-USD"):
    boundary = int(NOW.timestamp()) // interval * interval
    candles = [{"start": str(boundary - interval * index),
                "open": "100", "high": "102", "low": "99", "close": "101", "volume": "10"}
               for index in range(count + 1)]
    book = {"pricebook": {"product_id": product,
                          "bids": [{"price": "100", "size": "1"}],
                          "asks": [{"price": "100.02", "size": "1"}],
                          "time": iso(NOW + timedelta(seconds=1))}}
    return {"candles": candles}, book


class Feed:
    def __init__(self, *, interval=900, count=6, product="BTC-USD"):
        self.candles, self.book = payloads(interval=interval, count=count, product=product)
        self.calls = []

    def __call__(self, url, timeout_seconds):
        self.calls.append((url, timeout_seconds))
        return deepcopy(self.candles if "/candles?" in url else self.book)


def client(feed=None, *, times=None, **kwargs):
    feed = feed or Feed()
    clock = iter(times or [NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)])
    return PublicMarketClient(transport=feed, clock=lambda: next(clock), **kwargs), feed


class PublicMarketTests(TestCase):
    def test_collection_has_closed_ordered_candles_and_real_quote_timestamp_contract(self):
        market, feed = client()
        result = market.collect(symbol="BTC/USD", timeframe="15m", limit=6)
        self.assertEqual(result["symbol"], "BTC/USD")
        self.assertEqual(result["provider_symbol"], "BTC-USD")
        self.assertEqual(result["quote_currency"], "USD")
        self.assertEqual(result["source_kind"], "market")
        self.assertEqual(result["source"], "coinbase-public")
        self.assertFalse(result["point_in_time_archive"])
        self.assertEqual(result["as_of"], iso(NOW))
        self.assertEqual(result["observed_at"], iso(NOW + timedelta(seconds=2)))
        self.assertEqual(result["quote"]["as_of"], iso(NOW + timedelta(seconds=1)))
        self.assertEqual(result["quote"]["observed_at"], result["observed_at"])
        self.assertEqual(result["excluded_open_candles"], 1)
        self.assertEqual(len(result["candles"]), 6)
        self.assertNotIn("levels", result)
        self.assertNotIn("news", result)
        self.assertNotIn("reviews", result)
        for bar in result["candles"]:
            validate_bar(bar, as_of=result["as_of"], interval_seconds=900)
        self.assertEqual(result["candles"][-1]["close_time"], "2026-10-19T09:00:00.000000Z")
        self.assertEqual(len(feed.calls), 2)
        parsed = urlparse(feed.calls[0][0])
        self.assertEqual(parsed.netloc, "api.coinbase.com")
        self.assertEqual(parse_qs(parsed.query)["granularity"], ["FIFTEEN_MINUTE"])
        self.assertEqual(parse_qs(parsed.query)["limit"], ["7"])

    def test_pair_mapping_does_not_substitute_quote_currency(self):
        for symbol in ("BTC/USDT", "BTC/GBP", "BTC", "../accounts", "https://evil.test"):
            market, feed = client()
            with self.subTest(symbol=symbol), self.assertRaises(PublicMarketError):
                market.collect(symbol=symbol, limit=6)
            self.assertEqual(feed.calls, [])
        market, _ = client(Feed(product="ETH-USD"))
        self.assertEqual(market.collect(symbol="ETH/USD", limit=6)["provider_symbol"], "ETH-USD")

    def test_all_advertised_intervals_use_documented_granularity(self):
        for timeframe, seconds, granularity in (
            ("1m", 60, "ONE_MINUTE"), ("5m", 300, "FIVE_MINUTE"),
            ("15m", 900, "FIFTEEN_MINUTE"), ("30m", 1800, "THIRTY_MINUTE"),
            ("1h", 3600, "ONE_HOUR"), ("4h", 14400, "FOUR_HOUR"),
            ("1d", 86400, "ONE_DAY"),
        ):
            with self.subTest(timeframe=timeframe):
                market, feed = client(Feed(interval=seconds))
                result = market.collect(symbol="BTC/USD", timeframe=timeframe, limit=6)
                self.assertEqual(parse_qs(urlparse(feed.calls[0][0]).query)["granularity"], [granularity])
                for bar in result["candles"]:
                    validate_bar(bar, as_of=result["as_of"], interval_seconds=seconds)

    def test_input_bounds_reject_before_network(self):
        for args in ({"limit": True}, {"limit": 4}, {"limit": 301}, {"limit": "6"},
                     {"timeframe": "1M"}, {"symbol": "btc/usd"}):
            market, feed = client()
            with self.subTest(args=args), self.assertRaises(PublicMarketError):
                market.collect(**args)
            self.assertEqual(feed.calls, [])

    def test_duplicate_bars_rejected(self):
        feed = Feed()
        feed.candles["candles"][-1] = deepcopy(feed.candles["candles"][-2])
        market, _ = client(feed)
        with self.assertRaisesRegex(PublicMarketError, "duplicate"):
            market.collect(limit=6)

    def test_missing_or_stale_closed_bar_rejected(self):
        for index in (2, 1):
            feed = Feed()
            del feed.candles["candles"][index]
            market, _ = client(feed)
            with self.subTest(index=index), self.assertRaisesRegex(PublicMarketError, "complete"):
                market.collect(limit=6)

    def test_misaligned_bar_rejected(self):
        feed = Feed()
        feed.candles["candles"][1]["start"] = str(int(feed.candles["candles"][1]["start"]) + 60)
        market, _ = client(feed)
        with self.assertRaisesRegex(PublicMarketError, "boundar"):
            market.collect(limit=6)

    def test_future_bar_and_out_of_window_bar_rejected(self):
        for delta in (900, -900 * 7):
            feed = Feed()
            boundary = int(NOW.timestamp()) // 900 * 900
            feed.candles["candles"][0]["start"] = str(boundary + delta)
            market, _ = client(feed)
            with self.subTest(delta=delta), self.assertRaises(PublicMarketError):
                market.collect(limit=6)

    def test_nonfinite_negative_boolean_and_contradictory_prices_rejected(self):
        for field, value in (("close", "NaN"), ("close", "inf"), ("open", True),
                             ("open", "0"), ("low", "110"), ("volume", "-1"),
                             ("high", "1e309")):
            feed = Feed()
            feed.candles["candles"][1][field] = value
            market, _ = client(feed)
            with self.subTest(field=field, value=value), self.assertRaises(PublicMarketError):
                market.collect(limit=6)

    def test_book_identity_and_optional_candle_identity_are_checked(self):
        for target in ("book", "candles", "row"):
            feed = Feed()
            if target == "book": feed.book["pricebook"]["product_id"] = "ETH-USD"
            elif target == "candles": feed.candles["product_id"] = "ETH-USD"
            else: feed.candles["candles"][1]["product_id"] = "ETH-USD"
            market, _ = client(feed)
            with self.subTest(target=target), self.assertRaisesRegex(PublicMarketError, "instrument"):
                market.collect(limit=6)

    def test_empty_crossed_and_nonfinite_book_rejected(self):
        for kind in ("empty", "crossed", "nan", "zero_size", "too_many"):
            feed = Feed()
            book = feed.book["pricebook"]
            if kind == "empty": book["bids"] = []
            elif kind == "crossed": book["asks"][0]["price"] = "99"
            elif kind == "nan": book["bids"][0]["price"] = "NaN"
            elif kind == "zero_size": book["asks"][0]["size"] = "0"
            else: book["bids"] *= 2
            market, _ = client(feed)
            with self.subTest(kind=kind), self.assertRaises(PublicMarketError):
                market.collect(limit=6)

    def test_stale_future_or_naive_quote_time_rejected(self):
        for value in (iso(NOW - timedelta(seconds=90)), iso(NOW + timedelta(seconds=10)),
                      "2026-10-19T09:08:00", "bad"):
            feed = Feed()
            feed.book["pricebook"]["time"] = value
            market, _ = client(feed)
            with self.subTest(value=value), self.assertRaises(PublicMarketError):
                market.collect(limit=6)

    def test_backward_observation_clock_rejected(self):
        market, _ = client(times=[NOW, NOW - timedelta(seconds=1)])
        with self.assertRaisesRegex(PublicMarketError, "clock"):
            market.collect(limit=6)

    def test_request_crossing_candle_boundary_rejected_for_retry(self):
        market, _ = client(times=[NOW, NOW + timedelta(minutes=8), NOW + timedelta(minutes=8, seconds=1)])
        with self.assertRaisesRegex(PublicMarketError, "boundary"):
            market.collect(limit=6)

    def test_network_failure_has_no_fallback_and_hides_upstream_exception(self):
        def unavailable(url, timeout_seconds):
            raise OSError("internal credential proxy hint")
        market, _ = client(unavailable)
        with self.assertRaises(PublicMarketUnavailable) as result:
            market.collect(limit=6)
        self.assertNotIn("credential", str(result.exception))

    def test_upstream_error_object_has_no_fallback(self):
        feed = Feed()
        feed.candles = {"error": "something", "message": "private upstream detail"}
        market, _ = client(feed)
        with self.assertRaises(PublicMarketUnavailable) as result:
            market.collect(limit=6)
        self.assertNotIn("private", str(result.exception))

    def test_invalid_configuration_rejected(self):
        for kwargs in ({"timeout_seconds": True}, {"timeout_seconds": 0},
                       {"timeout_seconds": 31}, {"max_quote_age_seconds": 61},
                       {"max_quote_age_seconds": "10"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(PublicMarketError):
                PublicMarketClient(**kwargs)

    def test_json_boundary_is_strict_and_bounded(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{',
                    b'\xff', b' ' * (1024 * 1024 + 1)):
            with self.subTest(raw=raw[:20]), self.assertRaises(PublicMarketError):
                _decode_json(raw)
        self.assertEqual(_decode_json(json.dumps({"candles": []}).encode()), {"candles": []})

    def test_network_uses_get_public_routes_and_cache_control_without_auth(self):
        feed = Feed()

        class Response:
            def __init__(self, request): self.request = request
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return self.request.full_url
            def read(self, _limit): return json.dumps(feed(self.request.full_url, 8)).encode()

        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.assertions(request)
                return Response(request)
            assertions = staticmethod(lambda _: None)

        def assertions(request):
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("Cache-control"), "no-cache")
            self.assertIsNone(request.get_header("Authorization"))
            self.assertTrue(request.full_url.startswith("https://api.coinbase.com/api/v3/brokerage/market/"))

        opener = Opener()
        opener.assertions = assertions
        clock = iter([NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)])
        with patch("torinteo_paper.adapters.public_market.build_opener", return_value=opener):
            result = PublicMarketClient(clock=lambda: next(clock)).collect(limit=6)
        self.assertEqual(len(result["candles"]), 6)

    def test_redirected_final_response_is_rejected(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return "https://other.test/anything"
        class Opener:
            def open(self, *args, **kwargs): return Response()
        with patch("torinteo_paper.adapters.public_market.build_opener", return_value=Opener()):
            market = PublicMarketClient(clock=lambda: NOW)
            with self.assertRaises(PublicMarketUnavailable):
                market.collect(limit=6)
