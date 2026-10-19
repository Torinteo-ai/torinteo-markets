"""Contract tests with synthetic doubles; NOT real upstream/API validation.

No test installs optional packages, reads a credential or contacts a provider.
All prices and reports below are deliberately synthetic test observations.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from torinteo_paper.adapters.openbb import DataAdapterError, DataAdapterUnavailable, OpenBBAdapter
from torinteo_paper.adapters.tradingagents import TradingAgentsAdapter


UTC = timezone.utc
START = datetime(2026, 10, 19, tzinfo=UTC)


def synthetic_rows(start=START, count=4, step=timedelta(hours=1)):
    return [{"date": start + index * step, "open": 100.0 + index,
             "high": 102.0 + index, "low": 99.0 + index,
             "close": 101.0 + index, "volume": 10.0}
            for index in range(count)]


class HistoricalDouble:
    """Inject the documented OpenBB keyword signature and OBBject fields."""

    def __init__(self, rows, *, warnings=None, provider="yfinance", error=None):
        self.rows, self.warnings, self.provider, self.error = rows, warnings, provider, error
        self.calls = []

    def __call__(self, *, symbol, provider, interval, start_date, end_date):
        self.calls.append({"symbol": symbol, "provider": provider, "interval": interval,
                           "start_date": start_date, "end_date": end_date})
        if self.error:
            raise self.error
        return SimpleNamespace(results=self.rows, warnings=self.warnings, provider=self.provider)


def client_double(rows, **kwargs):
    endpoints = {name: HistoricalDouble(deepcopy(rows), **kwargs) for name in ("crypto", "equity", "currency")}
    client = SimpleNamespace(**{name: SimpleNamespace(price=SimpleNamespace(historical=endpoint))
                                for name, endpoint in endpoints.items()})
    return client, endpoints


class OpenBBContractTests(unittest.TestCase):
    def fetch(self, rows, *, symbol="BTC/USD", asset_class="crypto", timeframe="1h", as_of=None,
              timestamp_timezone=None, start_date="2026-10-19", end_date="2026-10-19", **response_options):
        client, calls = client_double(rows, **response_options)
        adapter = OpenBBAdapter(client, timestamp_timezone=timestamp_timezone)
        result = adapter.fetch_closed_candles(symbol, asset_class, timeframe, start_date, end_date,
                                               as_of or START + timedelta(hours=4))
        return result, calls

    def test_documented_request_preserves_original_symbol_and_source(self):
        result, calls = self.fetch(synthetic_rows())
        self.assertEqual(calls["crypto"].calls, [{"symbol": "BTC-USD", "provider": "yfinance",
                                               "interval": "1h", "start_date": "2026-10-19",
                                               "end_date": "2026-10-19"}])
        self.assertEqual(result["symbol"], "BTC/USD")
        self.assertEqual(result["source"], "openbb:yfinance")
        self.assertFalse(result["point_in_time_archive"])
        self.assertEqual(result["candles"][0]["close_time"], "2026-10-19T01:00:00Z")
        self.assertEqual(len(result["candles"]), 4)

    def test_crypto_quote_currency_is_never_substituted(self):
        result, calls = self.fetch(synthetic_rows(), symbol="BTC/USDT")
        self.assertEqual(calls["crypto"].calls[0]["symbol"], "BTC-USDT")
        self.assertEqual(result["symbol"], "BTC/USDT")

    def test_returned_row_identity_must_match_requested_instrument(self):
        for extra in ({"symbol": "ETH-USD"}, {"asset_class": "equity"}):
            rows = synthetic_rows()
            rows[0].update(extra)
            with self.subTest(extra=extra), self.assertRaisesRegex(DataAdapterError, "different"):
                self.fetch(rows)

    def test_forex_uses_explicit_currency_route_and_both_currencies(self):
        _, calls = self.fetch(synthetic_rows(), symbol="GBP/USD", asset_class="forex")
        self.assertEqual(calls["currency"].calls[0]["symbol"], "GBPUSD")
        self.assertEqual(calls["crypto"].calls, [])
        self.assertEqual(calls["equity"].calls, [])

    def test_equity_suffix_is_preserved(self):
        _, calls = self.fetch(synthetic_rows(), symbol="AZN.L", asset_class="equity")
        self.assertEqual(calls["equity"].calls[0]["symbol"], "AZN.L")

    def test_rejects_unsupported_assets_and_ambiguous_symbols_before_request(self):
        client, endpoints = client_double(synthetic_rows())
        adapter = OpenBBAdapter(client)
        for symbol, asset in (("BTCUSD", "crypto"), ("USD=X", "forex"), ("^NDX", "index"),
                              ("GC=F", "commodity"), ("^NDX", "equity"), ("", "equity")):
            with self.subTest(symbol=symbol, asset=asset), self.assertRaises(DataAdapterError):
                adapter.fetch_closed_candles(symbol, asset, "1h", "2026-10-19", "2026-10-19", START)
        self.assertTrue(all(not endpoint.calls for endpoint in endpoints.values()))

    def test_unknown_provider_and_timeframe_are_rejected(self):
        client, endpoints = client_double(synthetic_rows())
        adapter = OpenBBAdapter(client)
        for provider, timeframe in (("unknown", "1h"), ("yfinance", "1mo"), ("yfinance", "24h")):
            with self.subTest(provider=provider, timeframe=timeframe), self.assertRaises(DataAdapterError):
                adapter.fetch_closed_candles("BTC-USD", "crypto", timeframe, "2026-10-19", "2026-10-19",
                                               START, provider=provider)
        self.assertTrue(all(not endpoint.calls for endpoint in endpoints.values()))

    def test_asof_must_be_aware(self):
        with self.assertRaisesRegex(DataAdapterError, "timezone-aware"):
            self.fetch(synthetic_rows(), as_of=datetime(2026, 10, 19, 4))

    def test_naive_provider_time_requires_explicit_timezone(self):
        rows = synthetic_rows(start=START.replace(tzinfo=None))
        with self.assertRaisesRegex(DataAdapterError, "timestamp_timezone"):
            self.fetch(rows)
        result, _ = self.fetch(rows, timestamp_timezone="UTC")
        self.assertEqual(result["candles"][0]["open_time"], "2026-10-19T00:00:00Z")

    def test_ambiguous_and_nonexistent_local_times_are_rejected(self):
        for local in (datetime(2026, 10, 25, 1, 30), datetime(2026, 3, 29, 1, 30)):
            with self.subTest(local=local), self.assertRaisesRegex(DataAdapterError, "ambiguous|nonexistent"):
                self.fetch(synthetic_rows(start=local), asset_class="forex", symbol="GBPUSD",
                           timestamp_timezone="Europe/London", start_date=local.date().isoformat(),
                           end_date=local.date().isoformat(), as_of=local.replace(tzinfo=UTC) + timedelta(hours=6))

    def test_daily_calendar_boundary_respects_explicit_timezone_dst(self):
        start = datetime(2026, 3, 28)
        result, _ = self.fetch(synthetic_rows(start, 3, timedelta(days=1)),
                               asset_class="equity", symbol="AZN.L", timeframe="1d",
                               timestamp_timezone="Europe/London", start_date="2026-03-28",
                               end_date="2026-03-30", as_of=datetime(2026, 3, 31, tzinfo=UTC))
        candle = result["candles"][1]
        self.assertEqual(candle["open_time"], "2026-03-29T00:00:00Z")
        self.assertEqual(candle["close_time"], "2026-03-29T23:00:00Z")

    def test_still_open_candle_is_excluded_and_not_fabricated(self):
        result, _ = self.fetch(synthetic_rows(), as_of=START + timedelta(hours=3, minutes=30))
        self.assertEqual(len(result["candles"]), 3)
        self.assertEqual(result["excluded_open_candles"], 1)
        self.assertEqual(result["candles"][-1]["close_time"], "2026-10-19T03:00:00Z")

    def test_future_candles_are_rejected(self):
        with self.assertRaisesRegex(DataAdapterError, "future"):
            self.fetch(synthetic_rows(count=5), as_of=START + timedelta(hours=3, minutes=30))

    def test_duplicates_and_unsorted_responses_are_rejected(self):
        rows = synthetic_rows()
        for invalid in ((rows[0], rows[0], rows[1]), tuple(reversed(rows))):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(DataAdapterError, "ordered|future"):
                self.fetch(list(invalid))

    def test_wrong_returned_interval_is_detected_for_all_supported_assets(self):
        daily = synthetic_rows(count=3, step=timedelta(days=1))
        for asset, symbol in (("crypto", "BTC-USD"), ("equity", "AAPL"), ("forex", "GBPUSD")):
            with self.subTest(asset=asset), self.assertRaisesRegex(DataAdapterError, "interval"):
                self.fetch(daily, asset_class=asset, symbol=symbol, end_date="2026-10-21",
                           as_of=START + timedelta(days=3))

    def test_declared_interval_or_close_time_mismatch_is_rejected(self):
        for extra in ({"interval": "1d"}, {"close_time": START + timedelta(days=1)}):
            rows = synthetic_rows()
            rows[0].update(extra)
            with self.subTest(extra=extra), self.assertRaisesRegex(DataAdapterError, "interval"):
                self.fetch(rows)

    def test_single_bar_requires_explicit_duration_evidence(self):
        rows = synthetic_rows(count=1)
        with self.assertRaisesRegex(DataAdapterError, "interval"):
            self.fetch(rows)
        rows[0]["close_time"] = START + timedelta(hours=1)
        result, _ = self.fetch(rows)
        self.assertEqual(len(result["candles"]), 1)

    def test_invalid_prices_volumes_and_ohlc_are_rejected(self):
        for field, value in (("open", float("nan")), ("close", float("inf")), ("volume", -1),
                             ("open", True), ("volume", "10"), ("high", 90), ("low", 105)):
            rows = synthetic_rows()
            rows[1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(DataAdapterError):
                self.fetch(rows)
        rows = synthetic_rows()
        rows[0]["volume"] = 0
        result, _ = self.fetch(rows)
        self.assertEqual(result["candles"][0]["volume"], 0.0)

    def test_unsupported_parameter_warning_and_provider_mismatch_are_rejected(self):
        with self.assertRaisesRegex(DataAdapterError, "warnings"):
            self.fetch(synthetic_rows(), warnings=[{"message": "interval ignored"}])
        with self.assertRaisesRegex(DataAdapterError, "different provider"):
            self.fetch(synthetic_rows(), provider="other")

    def test_empty_data_is_empty_and_malformed_results_are_rejected(self):
        result, _ = self.fetch([])
        self.assertEqual(result["candles"], [])
        with self.assertRaisesRegex(DataAdapterError, "results"):
            self.fetch(None)

    def test_outside_requested_date_range_is_rejected(self):
        with self.assertRaisesRegex(DataAdapterError, "date range"):
            self.fetch(synthetic_rows(start=START - timedelta(days=1)))

    def test_missing_crypto_source_bar_is_not_filled_in(self):
        rows = synthetic_rows()
        del rows[1]
        with self.assertRaisesRegex(DataAdapterError, "missing candle"):
            self.fetch(rows)

    def test_provider_exception_text_is_not_exposed(self):
        with self.assertRaises(DataAdapterUnavailable) as caught:
            self.fetch(synthetic_rows(), error=RuntimeError("synthetic-secret=do-not-return"))
        self.assertNotIn("synthetic-secret", str(caught.exception))

    def test_four_hour_aggregation_uses_only_complete_hourly_buckets(self):
        result, calls = self.fetch(synthetic_rows(count=9), timeframe="4h", as_of=START + timedelta(hours=9))
        self.assertEqual(calls["crypto"].calls[0]["interval"], "1h")
        self.assertEqual(len(result["candles"]), 2)
        self.assertEqual(result["excluded_incomplete_buckets"], 1)
        first = result["candles"][0]
        self.assertEqual((first["open"], first["high"], first["low"], first["close"], first["volume"]),
                         (100.0, 105.0, 99.0, 104.0, 40.0))
        self.assertEqual(first["close_time"], "2026-10-19T04:00:00Z")

    def test_aggregation_cannot_produce_infinite_volume(self):
        rows = synthetic_rows()
        for row in rows:
            row["volume"] = 1e308
        with self.assertRaisesRegex(DataAdapterError, "finite"):
            self.fetch(rows, timeframe="4h")

    def test_weekly_crypto_buckets_start_monday_and_need_seven_days(self):
        start = datetime(2026, 10, 5, tzinfo=UTC)
        result, calls = self.fetch(synthetic_rows(start, 15, timedelta(days=1)), timeframe="1w",
                                   start_date="2026-10-05", end_date="2026-10-19",
                                   as_of=datetime(2026, 10, 20, tzinfo=UTC))
        self.assertEqual(calls["crypto"].calls[0]["interval"], "1d")
        self.assertEqual(len(result["candles"]), 2)
        self.assertEqual(result["candles"][0]["open_time"], "2026-10-05T00:00:00Z")
        self.assertEqual(result["candles"][0]["close_time"], "2026-10-12T00:00:00Z")
        self.assertEqual(result["excluded_incomplete_buckets"], 1)

    def test_calendar_months_use_real_month_lengths_and_leap_year(self):
        for start, days, end in ((datetime(2024, 2, 1, tzinfo=UTC), 29, datetime(2024, 3, 1, tzinfo=UTC)),
                                 (datetime(2026, 1, 1, tzinfo=UTC), 31, datetime(2026, 2, 1, tzinfo=UTC)),
                                 (datetime(2026, 12, 1, tzinfo=UTC), 31, datetime(2027, 1, 1, tzinfo=UTC))):
            with self.subTest(start=start):
                result, calls = self.fetch(synthetic_rows(start, days, timedelta(days=1)), timeframe="1M",
                                           start_date=start.date().isoformat(), end_date=end.date().isoformat(),
                                           as_of=end)
                self.assertEqual(calls["crypto"].calls[0]["interval"], "1d")
                self.assertEqual(len(result["candles"]), 1)
                self.assertEqual(result["candles"][0]["close_time"], end.isoformat().replace("+00:00", "Z"))
                self.assertEqual(result["candles"][0]["volume"], days * 10)

    def test_partial_first_month_is_not_a_monthly_candle(self):
        result, _ = self.fetch(synthetic_rows(datetime(2024, 2, 2, tzinfo=UTC), 28, timedelta(days=1)),
                               timeframe="1M", start_date="2024-02-02", end_date="2024-03-01",
                               as_of=datetime(2024, 3, 1, tzinfo=UTC))
        self.assertEqual(result["candles"], [])
        self.assertEqual(result["excluded_incomplete_buckets"], 1)

    def test_noncrypto_aggregation_does_not_guess_session_calendars(self):
        for timeframe in ("4h", "1w", "1M"):
            with self.subTest(timeframe=timeframe), self.assertRaisesRegex(DataAdapterError, "calendar"):
                self.fetch(synthetic_rows(), asset_class="equity", symbol="AAPL", timeframe=timeframe)


class GraphDouble:
    def __init__(self, *, rating="BUY", state=None, error=None):
        self.rating = rating
        self.state = state if state is not None else {"market_report": "Synthetic contract fixture only.",
                                                      "final_trade_decision": "Synthetic research conclusion."}
        self.error = error
        self.calls = []

    def propagate(self, company_name, trade_date, asset_type="stock", portfolio=None):
        self.calls.append((company_name, trade_date, asset_type, portfolio))
        if self.error:
            raise self.error
        return self.state, self.rating


def clock_double(*values):
    sequence = iter(values)
    return lambda: next(sequence)


def upstream_modules(graph, constructed):
    graph_module = ModuleType("tradingagents.graph.trading_graph")
    def factory(*, debug, config):
        constructed.append({"debug": debug, "config": deepcopy(config)})
        return graph
    graph_module.TradingAgentsGraph = factory
    config_module = ModuleType("tradingagents.default_config")
    config_module.DEFAULT_CONFIG = {"llm_provider": "upstream-default", "nested": {"keep": True}}
    return {"tradingagents": ModuleType("tradingagents"), "tradingagents.graph": ModuleType("tradingagents.graph"),
            "tradingagents.graph.trading_graph": graph_module, "tradingagents.default_config": config_module}


class TradingAgentsContractTests(unittest.TestCase):
    def test_actual_signature_completion_timestamp_and_research_only_stance(self):
        graph = GraphDouble(rating="SELL")
        started = START + timedelta(hours=12)
        finish = started + timedelta(seconds=20)
        adapter = TradingAgentsAdapter(graph, clock=clock_double(started, finish))
        result = adapter.research("BTC-USD", "crypto", started - timedelta(seconds=10))
        self.assertEqual(graph.calls, [("BTC-USD", "2026-10-19", "crypto", None)])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["as_of"], "2026-10-19T12:00:20Z")
        self.assertEqual(result["rating"], "SELL")
        self.assertEqual(result["stance"], "bearish")
        self.assertNotIn("side", result)
        self.assertNotIn("order", result)

    def test_known_rating_vocabulary_is_explicit(self):
        for rating, stance in (("STRONG BUY", "bullish"), ("BUY", "bullish"), ("HOLD", "neutral"),
                               ("SELL", "bearish"), ("STRONG SELL", "bearish")):
            with self.subTest(rating=rating):
                result = TradingAgentsAdapter(GraphDouble(rating=rating), clock=lambda: START).research("AAPL", "stock", START)
                self.assertEqual(result["stance"], stance)
                self.assertEqual(result["status"], "ok")

    def test_equity_is_explicitly_mapped_to_upstream_stock(self):
        graph = GraphDouble()
        TradingAgentsAdapter(graph, clock=lambda: START).research("AAPL", "equity", START)
        self.assertEqual(graph.calls[0][2], "stock")

    def test_crypto_slash_mapping_preserves_quote_and_returned_engine_identity(self):
        for canonical, provider in (("BTC/USD", "BTC-USD"), ("BTC/USDT", "BTC-USDT"), ("ETH/EUR", "ETH-EUR")):
            with self.subTest(canonical=canonical):
                graph = GraphDouble()
                result = TradingAgentsAdapter(graph, clock=lambda: START).research(canonical, "crypto", START)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(graph.calls, [(provider, "2026-10-19", "crypto", None)])
                self.assertEqual(result["symbol"], canonical)
                self.assertEqual(result["provider_symbol"], provider)

    def test_equity_provider_symbols_and_exchange_suffixes_are_preserved(self):
        for ticker in ("BRK-B", "AZN.L", "0700.HK"):
            with self.subTest(ticker=ticker):
                graph = GraphDouble()
                result = TradingAgentsAdapter(graph, clock=lambda: START).research(ticker, "equity", START)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(graph.calls[0][0], ticker)
                self.assertEqual(result["symbol"], ticker)

    def test_invalid_tickers_are_rejected_before_optional_import_or_graph_activation(self):
        real_import = __import__
        def guarded_import(name, *args, **kwargs):
            if name == "tradingagents" or name.startswith("tradingagents."):
                raise AssertionError("invalid ticker activated an optional package")
            return real_import(name, *args, **kwargs)
        invalid = (("BTCUSD", "crypto"), ("BTC//USD", "crypto"), ("BTC/../../secret", "crypto"),
                   ("BTC/USD\nDO", "crypto"), ("../AAPL", "equity"), ("AAPL/../../", "stock"),
                   ("AAPL; rm", "stock"), ("/AAPL", "stock"), ("AAPL\n", "stock"),
                   ("A..B", "stock"), ("", "stock"), ("BTC/USD", "equity"), ("A" * 33, "stock"),
                   ("ß/USDT", "crypto"), ("ＡＡＰＬ", "equity"))
        with patch("builtins.__import__", side_effect=guarded_import):
            for ticker, asset in invalid:
                with self.subTest(ticker=ticker, asset=asset):
                    result = TradingAgentsAdapter(clock=lambda: START).research(ticker, asset, START)
                    self.assertEqual(result["status"], "invalid")
                    self.assertEqual(result["reports"], {})

    def test_historical_date_and_earlier_same_day_intraday_are_unavailable(self):
        now = START + timedelta(hours=12)
        for requested in (START - timedelta(days=1), START, now - timedelta(seconds=61)):
            with self.subTest(requested=requested):
                graph = GraphDouble()
                result = TradingAgentsAdapter(graph, clock=lambda: now).research("AAPL", "stock", requested)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(graph.calls, [])
                self.assertIn("date only", result["reason"])

    def test_timezone_conversion_does_not_turn_prior_utc_day_into_today(self):
        now = START + timedelta(minutes=5)
        requested = datetime(2026, 10, 19, 0, 30, tzinfo=timezone(timedelta(hours=1)))
        graph = GraphDouble()
        result = TradingAgentsAdapter(graph, clock=lambda: now).research("AAPL", "stock", requested)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(graph.calls, [])

    def test_naive_and_future_asof_are_invalid_without_upstream_calls(self):
        for requested in (START.replace(tzinfo=None), START + timedelta(seconds=1)):
            graph = GraphDouble()
            result = TradingAgentsAdapter(graph, clock=lambda: START).research("AAPL", "stock", requested)
            self.assertEqual(result["status"], "invalid")
            self.assertEqual(graph.calls, [])

    def test_no_prose_numeric_dict_or_order_direction_rating_coercion(self):
        for rating in ("BUY because it will go up", "SHORT", "buy", 0.95, {"BUY": 0.9}, None):
            with self.subTest(rating=rating):
                result = TradingAgentsAdapter(GraphDouble(rating=rating), clock=lambda: START).research("AAPL", "stock", START)
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(result["stance"], "unknown")
                self.assertIsNone(result["rating"])

    def test_missing_final_report_and_runtime_objects_are_invalid(self):
        for state in ({"market_report": "Synthetic only"}, {"final_trade_decision": "Synthetic", "news_report": object()},
                      {"final_trade_decision": "Synthetic", "risk_debate_state": {"score": float("nan")}}):
            with self.subTest(state=state):
                result = TradingAgentsAdapter(GraphDouble(state=state), clock=lambda: START).research("AAPL", "stock", START)
                self.assertEqual(result["status"], "invalid")

    def test_provider_error_returns_unavailable_without_secret_error_text(self):
        graph = GraphDouble(error=RuntimeError("synthetic-key=never-log-this"))
        result = TradingAgentsAdapter(graph, clock=lambda: START).research("AAPL", "stock", START)
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("synthetic-key", result["reason"])

    def test_backwards_completion_clock_rejects_evidence(self):
        graph = GraphDouble()
        result = TradingAgentsAdapter(graph, clock=clock_double(START, START - timedelta(seconds=1))).research("AAPL", "stock", START)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("backwards", result["reason"])

    def test_unconfigured_provider_does_not_instantiate_upstream_defaults(self):
        constructed = []
        with patch.dict(sys.modules, upstream_modules(GraphDouble(), constructed)):
            result = TradingAgentsAdapter(clock=lambda: START).research("AAPL", "stock", START)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(constructed, [])
        self.assertIn("Explicit", result["reason"])

    def test_explicit_configuration_is_copied_and_lazy(self):
        constructed = []
        config = {"llm_provider": "ollama", "quick_think_llm": "chosen-test-model", "deep_think_llm": "chosen-test-model"}
        original = deepcopy(config)
        with patch.dict(sys.modules, upstream_modules(GraphDouble(), constructed)):
            adapter = TradingAgentsAdapter(config=config, clock=lambda: START)
            self.assertEqual(constructed, [])
            config["llm_provider"] = "changed-after-construction"
            result = adapter.research("AAPL", "stock", START)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(constructed[0]["config"]["llm_provider"], original["llm_provider"])

    def test_constructing_adapters_does_not_import_optional_packages(self):
        real_import = __import__
        def guarded_import(name, *args, **kwargs):
            if name == "openbb" or name == "tradingagents" or name.startswith("tradingagents."):
                raise AssertionError("unexpected optional package import")
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded_import):
            OpenBBAdapter()
            TradingAgentsAdapter()

    def test_missing_optional_packages_are_reported_on_explicit_call(self):
        real_import = __import__
        def missing_import(name, *args, **kwargs):
            if name == "openbb" or name.startswith("tradingagents."):
                raise ImportError("synthetic optional package absent")
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=missing_import):
            with self.assertRaises(DataAdapterUnavailable):
                OpenBBAdapter().fetch_closed_candles("BTC-USD", "crypto", "1h", "2026-10-19", "2026-10-19", START)
            result = TradingAgentsAdapter(clock=lambda: START).research("AAPL", "stock", START)
        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
