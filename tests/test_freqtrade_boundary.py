"""Local contract tests. Prices/tickets here are fabricated fixtures, not market evidence.

No Freqtrade distribution, exchange connection, or upstream bot is exercised.
The callback fixtures model its documented arguments; the validator/files/SQLite
claims are the real implementation rather than a simulated execution product.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from enum import Enum
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from torinteo_paper.adapters.freqtrade import MAX_TICKET_BYTES, TicketStore, validate_ticket
from torinteo_paper.models import iso


NOW = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
DECISION_ID = "a1" * 32


def ticket(**changes):
    result = {
        "schema_version": 1, "mode": "paper", "executor": "freqtrade_dry_run",
        "provenance": "market", "decision_id": DECISION_ID, "symbol": "BTC/USDT",
        "side": "LONG", "issued_at": iso(NOW), "expires_at": iso(NOW + timedelta(seconds=120)),
        "signal_bar_close": iso(NOW), "entry": 100.0, "stop": 98.0, "target": 104.0,
        "quantity": 1.0, "notional": 100.0, "risk_cash": 2.3,
        "fee_bps": 10.0, "slippage_bps": 5.0, "entry_tolerance_bps": 10,
    }
    result.update(changes)
    return result


def context(**changes):
    result = {"pair": "BTC/USDT", "side": "long", "rate": 100.0, "amount": 1.0,
              "now": NOW, "dry_run": True, "runmode": "dry_run"}
    result.update(changes)
    return result


class RunMode(Enum):
    DRY_RUN = "dry_run"
    LIVE = "live"


class ValidateTicketTests(unittest.TestCase):
    def test_valid_exact_budget_and_freqtrade_enum(self):
        self.assertTrue(validate_ticket(ticket(), **context()))
        self.assertTrue(validate_ticket(ticket(), **context(runmode=RunMode.DRY_RUN)))

    def test_live_backtest_and_truthy_configuration_are_denied(self):
        for values in ({"dry_run": False}, {"dry_run": 1}, {"dry_run": "true"},
                       {"runmode": "live"}, {"runmode": RunMode.LIVE},
                       {"runmode": "backtest"}, {"runmode": "hyperopt"},
                       {"runmode": None}, {"runmode": "DRY_RUN"}):
            with self.subTest(values=values):
                self.assertFalse(validate_ticket(ticket(), **context(**values)))

    def test_exact_schema_prevents_unsupported_extensions_or_missing_controls(self):
        for values in (ticket(schema_version=True), ticket(schema_version=2),
                       ticket(live=True), ticket(executor="native"), ticket(mode="live")):
            with self.subTest(values=values):
                self.assertFalse(validate_ticket(values, **context()))
        for key in ticket():
            values = ticket()
            del values[key]
            with self.subTest(missing=key):
                self.assertFalse(validate_ticket(values, **context()))

    def test_synthetic_and_historical_provenance_are_denied(self):
        for source in ("synthetic", "historical", "MARKET", None):
            with self.subTest(source=source):
                self.assertFalse(validate_ticket(ticket(provenance=source), **context()))

    def test_id_and_instrument_binding(self):
        for decision in ("A1" * 32, "a" * 63, "g" * 64, "../ticket", None):
            with self.subTest(decision=decision):
                self.assertFalse(validate_ticket(ticket(decision_id=decision), **context()))
        self.assertFalse(validate_ticket(ticket(), **context(pair="ETH/USDT")))
        for pair in ("btc/usdt", "BTCUSDT", "BTC/USDT:USDT", "BTC/BTC", "BTC/USD/USDT"):
            with self.subTest(pair=pair):
                self.assertFalse(validate_ticket(ticket(symbol=pair), **context(pair=pair)))
        self.assertFalse(validate_ticket(ticket(side="SHORT"), **context(side="short")))
        self.assertFalse(validate_ticket(ticket(), **context(side="short")))

    def test_clock_and_inclusive_issuance_exclusive_expiry(self):
        self.assertTrue(validate_ticket(ticket(), **context(now=NOW)))
        self.assertTrue(validate_ticket(ticket(), **context(now=NOW + timedelta(seconds=119))))
        for moment in (NOW - timedelta(microseconds=1), NOW + timedelta(seconds=120),
                       NOW.replace(tzinfo=None)):
            with self.subTest(now=moment):
                self.assertFalse(validate_ticket(ticket(), **context(now=moment)))

    def test_timezone_offsets_describe_the_same_instant(self):
        other_zone = NOW.astimezone(timezone(timedelta(hours=2)))
        values = ticket(issued_at=other_zone.isoformat(), signal_bar_close=other_zone.isoformat())
        self.assertTrue(validate_ticket(values, **context()))

    def test_ticket_timestamp_schema_and_ttl(self):
        for key in ("issued_at", "expires_at", "signal_bar_close"):
            for value in (NOW.isoformat().split("+")[0], "not-a-time", NOW, None):
                with self.subTest(key=key, value=value):
                    self.assertFalse(validate_ticket(ticket(**{key: value}), **context()))
        for duration in (-1, 0, 121):
            with self.subTest(duration=duration):
                self.assertFalse(validate_ticket(
                    ticket(expires_at=iso(NOW + timedelta(seconds=duration))), **context()))
        self.assertFalse(validate_ticket(ticket(signal_bar_close=iso(NOW + timedelta(seconds=1))), **context()))

    def test_positive_fields_reject_nonfinite_wrong_type_and_zero(self):
        for key in ("entry", "stop", "target", "quantity", "notional", "risk_cash"):
            for value in (float("nan"), float("inf"), -float("inf"), True, "100", 0, -1):
                with self.subTest(key=key, value=value):
                    self.assertFalse(validate_ticket(ticket(**{key: value}), **context()))

    def test_cost_and_price_tolerance_bounds(self):
        for key in ("fee_bps", "slippage_bps"):
            for value in (-1, 1001, float("nan"), float("inf"), True, "0"):
                with self.subTest(key=key, value=value):
                    self.assertFalse(validate_ticket(ticket(**{key: value}), **context()))
        self.assertTrue(validate_ticket(ticket(fee_bps=0, slippage_bps=0, risk_cash=2), **context()))
        for value in (0, 9, 11, True, "10", float("nan")):
            with self.subTest(tolerance=value):
                self.assertFalse(validate_ticket(ticket(entry_tolerance_bps=value), **context()))

    def test_actual_order_values_are_validated_before_arithmetic(self):
        for key in ("rate", "amount"):
            for value in (float("nan"), float("inf"), True, "100", 0, -1, None):
                with self.subTest(key=key, value=value):
                    self.assertFalse(validate_ticket(ticket(), **context(**{key: value})))

    def test_rate_can_move_within_tolerance_only_when_both_budgets_allow(self):
        self.assertTrue(validate_ticket(ticket(), **context(rate=100.05, amount=0.5)))
        self.assertTrue(validate_ticket(ticket(), **context(rate=99.95)))
        self.assertFalse(validate_ticket(ticket(), **context(rate=100.05)))
        # Notional is below 100 but the new stop distance exceeds the risk cap.
        self.assertFalse(validate_ticket(ticket(), **context(rate=100.05, amount=0.99)))
        # Isolate the notional cap while leaving ample permitted cash risk.
        self.assertFalse(validate_ticket(ticket(risk_cash=10), **context(rate=100.05)))
        self.assertFalse(validate_ticket(ticket(), **context(amount=1.00000001)))

    def test_exact_ten_basis_point_boundary_and_values_beyond_it(self):
        self.assertTrue(validate_ticket(ticket(), **context(rate=100.1, amount=0.5)))
        self.assertTrue(validate_ticket(ticket(), **context(rate=99.9)))
        self.assertFalse(validate_ticket(ticket(), **context(rate=100.10000001, amount=0.5)))
        self.assertFalse(validate_ticket(ticket(), **context(rate=99.89999999)))

    def test_stop_target_ordering_and_repriced_crossing(self):
        for values in ({"stop": 100}, {"stop": 101}, {"target": 100}, {"target": 99}):
            with self.subTest(values=values):
                self.assertFalse(validate_ticket(ticket(**values), **context()))
        self.assertFalse(validate_ticket(ticket(stop=99.99), **context(rate=99.95, amount=0.1)))
        self.assertFalse(validate_ticket(ticket(target=100.01), **context(rate=100.05, amount=0.1)))


class TicketStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.store = TicketStore(self.directory)
        self.path = self.directory / f"{DECISION_ID}.json"

    def write(self, values=None):
        self.path.write_text(json.dumps(ticket() if values is None else values), encoding="utf-8")

    def test_exact_file_is_read_and_missing_or_traversal_ids_are_rejected(self):
        self.write()
        self.assertEqual(self.store.read(DECISION_ID), ticket())
        self.assertIsNone(self.store.read("b" * 64))
        self.assertIsNone(self.store.read("../" + DECISION_ID))

    def test_file_identity_and_duplicate_json_keys_are_rejected(self):
        self.write(ticket(decision_id="b" * 64))
        self.assertIsNone(self.store.read(DECISION_ID))
        raw = json.dumps(ticket())[:-1] + ', "mode": "paper"}'
        self.path.write_text(raw, encoding="utf-8")
        self.assertIsNone(self.store.read(DECISION_ID))

    def test_oversized_invalid_and_non_object_files_are_rejected(self):
        for raw in (" " * (MAX_TICKET_BYTES + 1), "{broken", "[]", "null"):
            with self.subTest(kind=raw[:20]):
                self.path.write_text(raw, encoding="utf-8")
                self.assertIsNone(self.store.read(DECISION_ID))

    def test_ticket_symlink_and_other_user_writable_file_are_rejected(self):
        target = self.directory / "target.json"
        target.write_text(json.dumps(ticket()), encoding="utf-8")
        self.path.symlink_to(target)
        self.assertIsNone(self.store.read(DECISION_ID))
        self.path.unlink()
        self.write()
        self.path.chmod(0o666)
        self.assertIsNone(self.store.read(DECISION_ID))

    def test_other_user_writable_directory_and_missing_directory_are_rejected(self):
        self.directory.chmod(0o777)
        with self.assertRaises(ValueError):
            TicketStore(self.directory)
        self.directory.chmod(0o700)
        with self.assertRaises((OSError, ValueError)):
            TicketStore(self.directory / "missing")
        with self.assertRaises(ValueError):
            TicketStore("")

    def test_invalid_entry_does_not_claim_or_create_database(self):
        self.assertFalse(self.store.claim(ticket(), **context(dry_run=False)))
        self.assertFalse(self.store.claim(ticket(), **context(amount=2)))
        self.assertFalse(self.store.claims_path.exists())

    def test_claim_is_durable_and_cannot_be_reconfirmed_after_restart(self):
        self.assertTrue(self.store.claim(ticket(), **context()))
        restarted = TicketStore(self.directory)
        self.assertFalse(restarted.claim(ticket(), **context()))
        self.assertEqual(restarted.claimed_ticket(DECISION_ID), ticket())
        self.assertEqual(restarted.claims_path.stat().st_mode & 0o777, 0o600)

    def test_concurrent_claimants_grant_exactly_one_confirmation(self):
        barrier = Barrier(4)

        def attempt(_):
            store = TicketStore(self.directory)
            barrier.wait(timeout=5)
            return store.claim(ticket(), **context())

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(attempt, range(4)))
        self.assertEqual(outcomes.count(True), 1)
        with sqlite3.connect(self.store.claims_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM confirmation_claims").fetchone()[0], 1)

    def test_expiry_or_ticket_file_replacement_does_not_erase_protective_terms(self):
        self.write()
        self.assertTrue(self.store.claim(self.store.read(DECISION_ID), **context()))
        self.write(ticket(stop=97, target=108))
        self.assertFalse(self.store.claim(ticket(), **context(now=NOW + timedelta(hours=1))))
        self.assertEqual(self.store.claimed_ticket(DECISION_ID)["stop"], 98)
        self.assertEqual(self.store.claimed_ticket(DECISION_ID)["target"], 104)
        self.path.unlink()
        self.assertEqual(self.store.claimed_ticket(DECISION_ID), ticket())
        self.assertIsNone(self.store.claimed_ticket("b" * 64))

    def test_corrupt_database_denies_entry_and_missing_terms(self):
        self.store.claims_path.write_text("not a sqlite database", encoding="utf-8")
        self.assertFalse(self.store.claim(ticket(), **context()))
        self.assertIsNone(self.store.claimed_ticket(DECISION_ID))

    def test_claim_database_symlink_and_unsafe_permissions_fail_closed(self):
        target = self.directory / "other.sqlite3"
        target.touch()
        self.store.claims_path.symlink_to(target)
        self.assertFalse(self.store.claim(ticket(), **context()))
        self.assertIsNone(self.store.claimed_ticket(DECISION_ID))
        self.store.claims_path.unlink()
        self.assertTrue(self.store.claim(ticket(), **context()))
        self.store.claims_path.chmod(0o666)
        self.assertFalse(self.store.claim(ticket(decision_id="b" * 64), **context()))
        self.assertIsNone(self.store.claimed_ticket(DECISION_ID))


_strategy_path = Path(__file__).resolve().parents[1] / "integrations/freqtrade/TorinteoPaperStrategy.py"
_spec = importlib.util.spec_from_file_location("torinteo_freqtrade_mixin", _strategy_path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


class Host(_module.TorinteoPaperConfirmationMixin):
    """Minimal documented callback data fixture, not a Freqtrade strategy."""
    timeframe = "15m"
    can_short = False
    position_adjustment_enable = False

    def __init__(self):
        self.config = {"dry_run": True, "runmode": RunMode.DRY_RUN, "trading_mode": "spot",
                       "timeframe": "15m", "max_open_trades": 1, "api_server": {"enabled": False}}
        self.candle = {"date": NOW - timedelta(minutes=15), "enter_long": 1,
                       "enter_tag": "tm:" + DECISION_ID, "exit_long": 0}
        self.dp = SimpleNamespace(get_analyzed_dataframe=self.dataframe)

    def dataframe(self, pair, timeframe):
        return SimpleNamespace(empty=False, iloc=[self.candle]), None


class ConfirmationMixinTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        (self.directory / f"{DECISION_ID}.json").write_text(json.dumps(ticket()), encoding="utf-8")
        environment = patch.dict("os.environ", {"TORINTEO_TICKET_DIR": str(self.directory)})
        environment.start()
        self.addCleanup(environment.stop)
        self.host = Host()

    def confirm(self, **changes):
        arguments = {"pair": "BTC/USDT", "order_type": "limit", "amount": 1, "rate": 100,
                     "time_in_force": "GTC", "current_time": NOW,
                     "entry_tag": "tm:" + DECISION_ID, "side": "long"}
        arguments.update(changes)
        return self.host.confirm_trade_entry(**arguments)

    def test_latest_closed_candle_confirmation_is_durable_and_not_a_fill(self):
        # Some upstream configuration surfaces expose modes as enum values.
        self.host.config["trading_mode"] = SimpleNamespace(value="spot")
        self.assertTrue(self.confirm())
        self.assertFalse(self.confirm())
        self.host = Host()
        self.assertFalse(self.confirm())
        self.assertEqual(self.host.torinteo_approved_terms("tm:" + DECISION_ID), ticket())

    def test_no_tag_no_matching_latest_signal_and_exit_conflict_deny(self):
        self.assertFalse(self.confirm(entry_tag=None))
        self.assertFalse(self.confirm(entry_tag="tm:" + "b" * 64))
        for changes in ({"enter_long": 0}, {"enter_tag": "other"}, {"exit_long": 1}):
            with self.subTest(changes=changes):
                host = Host()
                host.candle.update(changes)
                self.host = host
                self.assertFalse(self.confirm())
        self.assertFalse((self.directory / "claims.sqlite3").exists())

    def test_historical_future_or_mismatched_candle_cannot_receive_current_ticket(self):
        for offset in (-15, 15):
            self.host = Host()
            self.host.candle["date"] += timedelta(minutes=offset)
            with self.subTest(offset=offset):
                self.assertFalse(self.confirm())
        self.host = Host()
        self.assertFalse(self.confirm(current_time=NOW + timedelta(minutes=15)))

    def test_mode_and_unreviewed_entry_features_are_disabled(self):
        for changes in ({"dry_run": False}, {"runmode": "backtest"}, {"trading_mode": "futures"},
                        {"timeframe": "5m"}, {"force_entry_enable": True},
                        {"position_adjustment_enable": True}, {"api_server": {"enabled": True}},
                        {"max_open_trades": 2}, {"max_open_trades": True}):
            self.host = Host()
            self.host.config.update(changes)
            with self.subTest(changes=changes):
                self.assertFalse(self.confirm())
        self.host = Host()
        self.assertFalse(self.confirm(order_type="market"))
        self.host.can_short = True
        self.assertFalse(self.confirm())

    def test_pending_entry_expires_and_retained_stop_and_target_remain_available(self):
        trade = SimpleNamespace(enter_tag="tm:" + DECISION_ID)
        self.assertTrue(self.confirm())
        self.assertFalse(self.host.check_entry_timeout("BTC/USDT", trade, None, NOW))
        self.assertTrue(self.host.check_entry_timeout("BTC/USDT", trade, None, NOW + timedelta(seconds=120)))
        self.assertTrue(self.host.check_entry_timeout("BTC/USDT", SimpleNamespace(), None, NOW))
        terms = self.host.torinteo_approved_terms(trade.enter_tag)
        self.assertEqual((terms["stop"], terms["target"]), (98, 104))

    def test_exit_is_never_blocked_by_missing_entry_ticket_or_live_setting(self):
        self.host.config["dry_run"] = False
        (self.directory / f"{DECISION_ID}.json").unlink()
        for reason in ("stop_loss", "stoploss_on_exchange", "emergency_exit", "exit_signal", "roi"):
            with self.subTest(reason=reason):
                self.assertTrue(self.host.confirm_trade_exit(
                    "BTC/USDT", None, "market", 1, 98, "GTC", reason, NOW + timedelta(hours=1)))

    def test_example_configuration_is_intentionally_not_a_runnable_strategy(self):
        path = _strategy_path.parent / "config.example.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(config["dry_run"])
        self.assertFalse(config["force_entry_enable"])
        self.assertFalse(config["api_server"]["enabled"])
        self.assertEqual(config["max_open_trades"], 1)
        self.assertEqual(config["initial_state"], "stopped")
        self.assertEqual(config["exchange"]["key"], "")
        self.assertEqual(config["exchange"]["secret"], "")
        self.assertIn("REPLACE_WITH", config["strategy"])
        self.assertFalse(hasattr(_module, "TorinteoPaperStrategy"))


if __name__ == "__main__":
    unittest.main()
