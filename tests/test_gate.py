import copy
import unittest
from datetime import timedelta

from tests.support import NOW, snapshot, stamp
from torinteo_paper.gate import evaluate, session_scope
from torinteo_paper.models import RiskConfig


class GateTests(unittest.TestCase):
    def decision(self, data=None, **kwargs):
        return evaluate(data or snapshot(), now=NOW, **kwargs)

    def test_accepts_reviewed_breakout_then_later_retest(self):
        result = self.decision()
        self.assertEqual(result["call"], "LONG", result)
        self.assertEqual(result["mode"], "paper")
        self.assertEqual(result["provenance"], "synthetic")
        self.assertGreaterEqual(result["intent"]["reward_risk"], 2)
        self.assertLessEqual(result["intent"]["risk_cash"], 50)
        self.assertLessEqual(result["intent"]["notional"], 1000)

    def test_short_requires_its_own_retest(self):
        result = self.decision(snapshot("SHORT"))
        self.assertEqual(result["call"], "SHORT", result)
        self.assertGreater(result["intent"]["stop"],result["intent"]["entry"])

    def test_nearby_resistance_vetoes_long(self):
        data = snapshot()
        data["levels"][1]["price"] = 100.35
        result = self.decision(data)
        self.assertEqual(result["call"],"NO_TRADE")
        self.assertIn("OPPOSING_LEVEL_TOO_CLOSE", result["reason_codes"])

    def test_nearby_support_vetoes_short(self):
        data = snapshot("SHORT")
        data["levels"][1]["price"] = 99.65
        result = self.decision(data)
        self.assertEqual(result["call"],"NO_TRADE")

    def test_no_retest_no_entry(self):
        data=snapshot()
        data["bars"][-1].update(open=100.5,low=100.4,high=100.9,close=100.7)
        self.assertEqual(self.decision(data)["call"],"NO_TRADE")

    def test_no_acceptance_no_entry(self):
        data=snapshot()
        for bar in data["bars"][:-1]:
            bar.update(open=99.6,high=100.1,low=99.3,close=99.8)
        self.assertEqual(self.decision(data)["call"],"NO_TRADE")

    def test_unknown_news_is_not_clear(self):
        data=snapshot();data["news"]["status"]="unknown"
        self.assertIn("NEWS_UNVERIFIED",self.decision(data)["reason_codes"])

    def test_upcoming_high_impact_event_overrides_clear_label(self):
        data=snapshot()
        data["news"]["events"]=[{"currency":"USD","impact":"high","starts_at":stamp(NOW+timedelta(minutes=5)),
                                   "known_at":stamp(NOW-timedelta(days=1)),"reference":"synthetic event"}]
        self.assertIn("HIGH_IMPACT_NEWS_WINDOW",self.decision(data)["reason_codes"])

    def test_stale_snapshot_rejected(self):
        result=evaluate(snapshot(),now=NOW+timedelta(minutes=5))
        self.assertIn("STALE_SNAPSHOT",result["reason_codes"])

    def test_future_and_unclosed_bars_rejected(self):
        for change in ("snapshot", "bar", "level", "review", "quote"):
            data=snapshot()
            future=stamp(NOW+timedelta(minutes=1))
            if change=="snapshot": data["as_of"]=future
            if change=="bar": data["bars"][-1]["close_time"]=future
            if change=="level": data["levels"][0]["known_at"]=future
            if change=="review": data["reviews"]["1d"]["as_of"]=future
            if change=="quote": data["quote"]["as_of"]=future
            with self.subTest(change=change): self.assertEqual(self.decision(data)["call"],"NO_TRADE")

    def test_duplicate_or_unsorted_bars_rejected(self):
        for edit in (lambda d: d["bars"].reverse(),lambda d: d["bars"].append(d["bars"][-1])):
            data=snapshot();edit(data)
            self.assertEqual(self.decision(data)["call"],"NO_TRADE")

    def test_missing_monthly_review_or_conflicting_bias_rejected(self):
        data=snapshot();del data["reviews"]["1M"]
        self.assertEqual(self.decision(data)["call"],"NO_TRADE")
        data=snapshot();data["reviews"]["4h"]["bias"]="SHORT"
        self.assertIn("TIMEFRAME_CONFLICT",self.decision(data)["reason_codes"])

    def test_nonfinite_and_boolean_prices_rejected(self):
        for invalid in (float("nan"),float("inf"),True,-1,"100"):
            data=snapshot();data["quote"]["ask"]=invalid
            self.assertEqual(self.decision(data)["call"],"NO_TRADE")

    def test_quote_currency_cannot_be_mislabeled_in_provider_notation(self):
        for instrument,asset in (("BTC-GBP","crypto"),("GBPJPY=X","forex"),("GBPJPY","forex")):
            data=snapshot();data.update(symbol=instrument,asset_class=asset,quote_currency="USD")
            self.assertEqual(self.decision(data)["call"],"NO_TRADE")

    def test_risk_configuration_cannot_enable_live_execution(self):
        with self.assertRaises(ValueError): RiskConfig(mode="live")
        with self.assertRaises(ValueError): RiskConfig(risk_fraction=float("nan"))
        with self.assertRaises(ValueError): RiskConfig(jev_mode="fallback")

    def test_no_position_is_inferred_from_missing_account(self):
        result=self.decision()
        self.assertEqual(result["account_kind"],"new_simulated_account")

    def test_daily_loss_and_existing_position_block(self):
        for account in ({"equity":10000,"daily_pnl":-201,"open_symbols":[],"open_notional":0},
                        {"equity":10000,"daily_pnl":0,"open_symbols":["TEST/USD"],"open_notional":100}):
            self.assertEqual(self.decision(account=account)["call"],"NO_TRADE")

    def test_ids_stable_and_payload_sensitive(self):
        one=self.decision();two=self.decision()
        self.assertEqual(one["decision_id"],two["decision_id"])
        data=snapshot();data["source"]["reference"]+=" new"
        self.assertNotEqual(one["decision_id"],self.decision(data)["decision_id"])

    def test_one_am_is_only_aud_nzd_news_gate(self):
        time=NOW.replace(hour=0,minute=0)  # 01:00 Europe/London in October
        self.assertEqual(session_scope(time,"BTC/USD",[]),"NO_SCAN")
        self.assertEqual(session_scope(time,"AUD/USD",[]),"NO_SCAN")
        event={"currency":"AUD","impact":"high","starts_at":stamp(time+timedelta(minutes=15))}
        self.assertEqual(session_scope(time,"AUD/USD",[event]),"AUD_NZD_NEWS_ONLY")
        self.assertEqual(session_scope(time,"EUR/USD",[event]),"NO_SCAN")


if __name__=="__main__": unittest.main()
