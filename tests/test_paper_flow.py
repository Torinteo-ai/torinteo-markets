from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tests.support import NOW, snapshot, stamp
from torinteo_paper.engine import PaperEngine
from torinteo_paper.ledger import PaperLedger
from torinteo_paper.models import RiskConfig


def next_bar(open_=100.23, high=100.8, low=100.1, close=100.5, minutes=0):
    start=NOW+timedelta(minutes=minutes)
    return {"open_time":stamp(start),"close_time":stamp(start+timedelta(minutes=15)),
            "open":open_,"high":high,"low":low,"close":close,"volume":1200}


class FakeJev:
    def __init__(self, choice="reject", status="ok"):
        self.choice,self.status,self.calls=choice,status,0
    def evaluate(self, state):
        self.calls+=1
        self.last_state=state
        return {"status":self.status,"provider":"jev","model":"jev-1.13.0","choice":self.choice,
                "confidence":0.9,"probabilities":{self.choice:0.95, "reject" if self.choice=="pass" else "pass":0.05},
                "reason":"fixture","latency_ms":5.0}


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"paper.sqlite"
    def tearDown(self): self.tmp.cleanup()
    def make(self, config=None, jev=None):
        return PaperEngine(PaperLedger(self.path,config or RiskConfig()),jev=jev)

    def test_complete_next_bar_entry_and_stop_first_settlement(self):
        engine=self.make()
        decision=engine.run(snapshot(),now=NOW)
        self.assertEqual(decision["call"],"LONG")
        self.assertEqual(engine.ledger.orders()[0]["status"],"PENDING")
        engine.advance("TEST/USD",next_bar(),observed_at=NOW+timedelta(minutes=15),provenance="synthetic")
        order=engine.ledger.orders()[0]
        self.assertEqual(order["status"],"OPEN")
        self.assertGreater(order["entry"],100.23)
        engine.advance("TEST/USD",next_bar(open_=100.5,high=106,low=99,close=100,minutes=15),
                       observed_at=NOW+timedelta(minutes=30),provenance="synthetic")
        order=engine.ledger.orders()[0]
        self.assertEqual(order["status"],"CLOSED")
        self.assertEqual(order["exit_reason"],"STOP_LOSS")
        self.assertLess(order["net_pnl"],0)
        self.assertGreater(order["fees"],0)

    def test_cannot_fill_with_signal_bar_or_future_bar(self):
        engine=self.make();engine.run(snapshot(),now=NOW)
        engine.advance("TEST/USD",snapshot()["bars"][-1],observed_at=NOW,provenance="synthetic")
        self.assertEqual(engine.ledger.orders()[0]["status"],"PENDING")
        with self.assertRaises(ValueError):
            engine.advance("TEST/USD",next_bar(),observed_at=NOW,provenance="synthetic")

    def test_restart_and_repeat_do_not_duplicate_or_requery_model(self):
        jev=FakeJev("pass");config=RiskConfig(jev_mode="required")
        engine=self.make(config,jev)
        first=engine.run(snapshot(),now=NOW)
        second=self.make(config,jev).run(snapshot(),now=NOW)
        self.assertEqual(first["decision_id"],second["decision_id"])
        self.assertEqual(jev.calls,1)
        self.assertEqual(len(engine.ledger.orders()),1)

    def test_concurrent_duplicate_evaluations_have_one_order(self):
        self.make()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _:self.make().run(snapshot(),now=NOW),range(4)))
        self.assertEqual(len(self.make().ledger.orders()),1)

    def test_required_jev_missing_rejected_but_shadow_rejection_is_recorded(self):
        engine=self.make(RiskConfig(jev_mode="required"))
        result=engine.run(snapshot(),now=NOW)
        self.assertEqual(result["call"],"NO_TRADE")
        self.assertIn("JEV_REQUIRED_UNAVAILABLE",result["reason_codes"])
        self.assertEqual(engine.ledger.orders(),[])
        path=Path(self.tmp.name)/"shadow.sqlite"
        engine=PaperEngine(PaperLedger(path,RiskConfig(jev_mode="shadow")),jev=FakeJev())
        result=engine.run(snapshot(),now=NOW)
        self.assertEqual(result["call"],"LONG")
        self.assertEqual(result["assessments"]["jev"]["choice"],"reject")

    def test_model_cannot_override_resistance_rejection(self):
        jev=FakeJev("pass");engine=self.make(RiskConfig(jev_mode="required"),jev)
        data=snapshot();data["levels"][1]["price"]=100.35
        self.assertEqual(engine.run(data,now=NOW)["call"],"NO_TRADE")
        self.assertEqual(jev.calls,0)

    def test_model_state_excludes_full_source_text_and_account_details(self):
        jev=FakeJev("pass");engine=self.make(RiskConfig(jev_mode="required"),jev)
        engine.run(snapshot(),now=NOW)
        self.assertNotIn("api_key",json.dumps(jev.last_state))
        self.assertNotIn("reviews",jev.last_state)
        self.assertNotIn("equity",jev.last_state)

    def test_required_jev_reject_and_exception_stop_entry(self):
        for index, provider in enumerate([FakeJev("reject"),FakeJev("pass","unavailable")]):
            engine=PaperEngine(PaperLedger(Path(self.tmp.name)/f"{index}.sqlite",RiskConfig(jev_mode="required")),jev=provider)
            self.assertEqual(engine.run(snapshot(),now=NOW)["call"],"NO_TRADE")

    def test_duplicate_bar_and_conflicting_bar(self):
        engine=self.make();engine.run(snapshot(),now=NOW)
        bar=next_bar()
        engine.advance("TEST/USD",bar,observed_at=NOW+timedelta(minutes=15),provenance="synthetic")
        count=len(engine.ledger.events())
        engine.advance("TEST/USD",bar,observed_at=NOW+timedelta(minutes=15),provenance="synthetic")
        self.assertEqual(len(engine.ledger.events()),count)
        bar["high"]+=1
        with self.assertRaises(ValueError):
            engine.advance("TEST/USD",bar,observed_at=NOW+timedelta(minutes=15),provenance="synthetic")

    def test_adverse_entry_gap_cancels_instead_of_chasing(self):
        engine=self.make();engine.run(snapshot(),now=NOW)
        engine.advance("TEST/USD",next_bar(open_=102,high=103,low=101,close=102),
                       observed_at=NOW+timedelta(minutes=15),provenance="synthetic")
        self.assertEqual(engine.ledger.orders()[0]["status"],"CANCELLED")

    def test_pending_order_expires_and_provenance_cannot_be_swapped(self):
        engine=self.make();engine.run(snapshot(),now=NOW)
        with self.assertRaises(ValueError):
            engine.advance("TEST/USD",next_bar(),observed_at=NOW+timedelta(minutes=15),provenance="market")
        engine.advance("TEST/USD",next_bar(minutes=15),observed_at=NOW+timedelta(minutes=30),provenance="synthetic")
        self.assertEqual(engine.ledger.orders()[0]["status"],"CANCELLED")

    def test_gap_through_stop_uses_worse_open(self):
        engine=self.make();engine.run(snapshot(),now=NOW)
        engine.advance("TEST/USD",next_bar(),observed_at=NOW+timedelta(minutes=15),provenance="synthetic")
        engine.advance("TEST/USD",next_bar(open_=98,high=99,low=97,close=98,minutes=15),
                       observed_at=NOW+timedelta(minutes=30),provenance="synthetic")
        order=engine.ledger.orders()[0]
        self.assertLess(order["exit_price"],98)

    def test_ledger_rejects_configuration_drift(self):
        self.make()
        with self.assertRaises(ValueError): self.make(RiskConfig(initial_equity=50000))

    def test_missing_bar_blocks_open_position_settlement(self):
        engine=self.make();engine.run(snapshot(),now=NOW)
        engine.advance("TEST/USD",next_bar(),observed_at=NOW+timedelta(minutes=15),provenance="synthetic")
        with self.assertRaises(ValueError):
            engine.advance("TEST/USD",next_bar(open_=100.5,high=106,low=100.2,close=105,minutes=30),
                           observed_at=NOW+timedelta(minutes=45),provenance="synthetic")
        self.assertEqual(engine.ledger.orders()[0]["status"],"OPEN")

    def test_past_account_excludes_future_outcome_and_entry_fee(self):
        engine=self.make();engine.run(snapshot(),now=NOW)
        before=engine.ledger.account(NOW-timedelta(seconds=1))
        engine.advance("TEST/USD",next_bar(),observed_at=NOW+timedelta(minutes=15),provenance="synthetic")
        engine.advance("TEST/USD",next_bar(open_=100.5,high=106,low=100.2,close=105,minutes=15),
                       observed_at=NOW+timedelta(minutes=30),provenance="synthetic")
        self.assertEqual(engine.ledger.account(NOW-timedelta(seconds=1)),before)
        self.assertLessEqual(engine.ledger.account(NOW)["equity"],10000)
        self.assertGreater(engine.ledger.account(NOW+timedelta(minutes=30))["equity"],10000)

    def test_decisions_cannot_move_experiment_backwards(self):
        engine=self.make();engine.run(snapshot(),now=NOW)
        engine.advance("TEST/USD",next_bar(),observed_at=NOW+timedelta(minutes=15),provenance="synthetic")
        data=snapshot();data["symbol"]="OTHER/USD"
        with self.assertRaises(ValueError):engine.run(data,now=NOW)

    def test_provider_delay_cannot_backdate_fill(self):
        engine=PaperEngine(PaperLedger(self.path),clock=lambda:NOW+timedelta(seconds=30))
        data=snapshot();data["source"]["kind"]="market"
        result=engine.run(data,now=NOW)
        engine.advance("TEST/USD",next_bar(),observed_at=NOW+timedelta(minutes=15),provenance="market")
        self.assertEqual(engine.ledger.orders()[0]["status"],"PENDING")
        self.assertEqual(result["approved_at"],stamp(NOW+timedelta(seconds=30)).replace("Z",".000000Z"))

    def test_one_minute_execution_bar_can_follow_approval(self):
        engine=PaperEngine(PaperLedger(self.path),clock=lambda:NOW+timedelta(seconds=30))
        data=snapshot();data["source"]["kind"]="market"
        engine.run(data,now=NOW)
        bar=next_bar();bar.update(open_time=stamp(NOW+timedelta(minutes=1)),close_time=stamp(NOW+timedelta(minutes=2)))
        engine.advance("TEST/USD",bar,observed_at=NOW+timedelta(minutes=2),provenance="market",timeframe="1m")
        self.assertEqual(engine.ledger.orders()[0]["status"],"OPEN")

    def test_entire_ledger_cannot_mix_executors(self):
        engine=self.make();engine.run(snapshot(),now=NOW,executor="freqtrade_dry_run")
        data=snapshot();data["symbol"]="OTHER/USD"
        with self.assertRaises(ValueError):engine.run(data,now=NOW,executor="native")

    def test_late_bar_cannot_backdate_loss_after_newer_decision(self):
        engine=self.make();engine.run(snapshot(),now=NOW)
        first=next_bar();first["close_time"]=stamp(NOW+timedelta(minutes=1))
        engine.advance("TEST/USD",first,observed_at=NOW+timedelta(minutes=1),provenance="synthetic",timeframe="1m")
        data=snapshot();data.update(symbol="OTHER/USD",as_of=stamp(NOW+timedelta(minutes=3)))
        data["quote"]["as_of"]=data["as_of"]
        engine.run(data,now=NOW+timedelta(minutes=3))
        late=next_bar(open_=70,high=80,low=60,close=75)
        late.update(open_time=stamp(NOW+timedelta(minutes=1)),close_time=stamp(NOW+timedelta(minutes=2)))
        with self.assertRaises(ValueError):
            engine.advance("TEST/USD",late,observed_at=NOW+timedelta(minutes=4),provenance="synthetic",timeframe="1m")

    def test_same_close_time_can_arrive_for_different_symbols(self):
        engine=self.make()
        engine.advance("A/USD",next_bar(),observed_at=NOW+timedelta(minutes=15,seconds=1),provenance="synthetic")
        engine.advance("B/USD",next_bar(),observed_at=NOW+timedelta(minutes=15,seconds=2),provenance="synthetic")

    def test_synthetic_cannot_be_exported_for_a_real_feed_bot(self):
        engine=self.make();decision=engine.run(snapshot(),now=NOW)
        with self.assertRaises(ValueError): engine.export_freqtrade_ticket(decision,Path(self.tmp.name)/"ticket.json",now=NOW)


if __name__=="__main__":unittest.main()
