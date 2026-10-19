"""Producer-to-consumer contract tests, using fabricated evidence and local SQLite.

The market provenance label below exercises the export branch only. None of
these fixtures represents obtained market data or an upstream bot execution.
"""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from tests.support import NOW, snapshot
from torinteo_paper.adapters.freqtrade import TICKET_FIELDS, TicketStore, validate_ticket
from torinteo_paper.engine import PaperEngine
from torinteo_paper.ledger import PaperLedger
from torinteo_paper.models import RiskConfig


def evidence(*, symbol="BTC/USDT", provenance="market", side="LONG"):
    data=snapshot(side)
    data["symbol"]=symbol
    data["source"]={"kind":provenance,"provider":"export_contract_fixture",
                    "reference":"tests/test_export_boundary.py: fabricated test evidence"}
    return data


class ExportBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory=Path(self.temporary.name)
        self.serial=0

    def engine(self,*,ttl=120,now=NOW):
        self.serial+=1
        config=RiskConfig(account_currency="USDT",ticket_ttl_seconds=ttl)
        return PaperEngine(PaperLedger(self.directory/f"ledger-{self.serial}.sqlite3",config),clock=lambda:now)

    def output(self,decision,name="tickets"):
        return self.directory/name/f"{decision['decision_id']}.json"

    def assert_rejected_without_output(self,engine,decision,*,now=NOW,name="rejected"):
        path=self.output(decision,name)
        with self.assertRaises(ValueError):
            engine.export_freqtrade_ticket(decision,path,now=now)
        self.assertFalse(path.parent.exists())

    def test_persisted_approval_exports_exact_consumer_contract_and_no_native_order(self):
        engine=self.engine()
        decision=engine.run(evidence(),now=NOW,executor="freqtrade_dry_run")
        self.assertEqual(decision["call"],"LONG")
        path=self.output(decision)
        ticket=engine.export_freqtrade_ticket(decision,path,now=NOW)
        self.assertEqual(set(ticket),TICKET_FIELDS)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")),ticket)
        self.assertEqual(TicketStore(path.parent).read(decision["decision_id"]),ticket)
        self.assertTrue(validate_ticket(ticket,pair="BTC/USDT",side="long",rate=ticket["entry"],
                                        amount=ticket["quantity"],now=NOW,dry_run=True,runmode="dry_run"))
        self.assertEqual(engine.ledger.orders(),[])
        self.assertEqual(engine.ledger.decision(decision["decision_id"]),decision)

    def test_one_and_120_second_lifetimes_remain_accepted(self):
        for lifetime in (1,120):
            with self.subTest(lifetime=lifetime):
                engine=self.engine(ttl=lifetime)
                decision=engine.run(evidence(),now=NOW,executor="freqtrade_dry_run")
                path=self.output(decision,f"ttl-{lifetime}")
                self.assertTrue(engine.export_freqtrade_ticket(decision,path,now=NOW))

    def test_longer_native_ttl_is_rejected_before_creating_output(self):
        for lifetime in (121,3600):
            with self.subTest(lifetime=lifetime):
                engine=self.engine(ttl=lifetime)
                decision=engine.run(evidence(),now=NOW,executor="freqtrade_dry_run")
                self.assertEqual(decision["call"],"LONG")
                self.assert_rejected_without_output(engine,decision,name=f"long-{lifetime}")

    def test_noncanonical_spot_symbols_accepted_by_native_model_do_not_export(self):
        for index,symbol in enumerate(("BTC-USDT","ETH_V2/USDT","A"*21+"/USDT")):
            with self.subTest(symbol=symbol):
                engine=self.engine()
                decision=engine.run(evidence(symbol=symbol),now=NOW,executor="freqtrade_dry_run")
                self.assertEqual(decision["call"],"LONG",decision["reason_codes"])
                self.assert_rejected_without_output(engine,decision,name=f"symbol-{index}")

    def test_issuance_tracks_persisted_approval_without_refreshing_expiry(self):
        later=NOW+timedelta(seconds=30)
        engine=self.engine(now=later)
        decision=engine.run(evidence(),now=NOW,executor="freqtrade_dry_run")
        ticket=engine.export_freqtrade_ticket(decision,self.output(decision),now=later)
        self.assertEqual(ticket["issued_at"],decision["approved_at"])
        self.assertEqual(ticket["expires_at"],decision["expires_at"])
        self.assertTrue(validate_ticket(ticket,pair="BTC/USDT",side="long",rate=ticket["entry"],
                                        amount=ticket["quantity"],now=later,dry_run=True,runmode="dry_run"))

    def test_synthetic_and_historical_approvals_stay_ineligible(self):
        for provenance in ("synthetic","historical"):
            with self.subTest(provenance=provenance):
                engine=self.engine()
                decision=engine.run(evidence(provenance=provenance),now=NOW,executor="freqtrade_dry_run")
                self.assertEqual(decision["call"],"LONG")
                self.assert_rejected_without_output(engine,decision,name=provenance)

    def test_missing_unknown_and_stale_market_evidence_does_not_export(self):
        cases=[]
        missing=evidence();del missing["source"]
        cases.append(("missing",missing,NOW))
        cases.append(("unknown",evidence(provenance="unknown"),NOW))
        cases.append(("stale",evidence(),NOW+timedelta(seconds=121)))
        for name,data,when in cases:
            with self.subTest(case=name):
                engine=self.engine(now=when)
                decision=engine.run(data,now=when,executor="freqtrade_dry_run")
                self.assertEqual(decision["call"],"NO_TRADE")
                self.assert_rejected_without_output(engine,decision,now=when,name=name)

    def test_short_and_noncrypto_decisions_stay_ineligible(self):
        for name,data in (("short",evidence(side="SHORT")),("equity",evidence())):
            if name=="equity":data["asset_class"]="equity"
            with self.subTest(case=name):
                engine=self.engine()
                decision=engine.run(data,now=NOW,executor="freqtrade_dry_run")
                self.assertIn(decision["call"],("LONG","SHORT"))
                self.assert_rejected_without_output(engine,decision,name=name)

    def test_unpersisted_native_and_modified_decisions_stay_ineligible(self):
        engine=self.engine()
        decision=engine.run(evidence(),now=NOW,executor="freqtrade_dry_run")
        empty=self.engine()
        self.assert_rejected_without_output(empty,decision,name="unpersisted")
        altered=deepcopy(decision)
        altered["intent"]["quantity"]*=2
        self.assert_rejected_without_output(engine,altered,name="altered")
        native=self.engine()
        native_decision=native.run(evidence(),now=NOW)
        self.assert_rejected_without_output(native,native_decision,name="native")

    def test_future_and_expired_export_clock_do_not_refresh_approval(self):
        engine=self.engine()
        decision=engine.run(evidence(),now=NOW,executor="freqtrade_dry_run")
        self.assert_rejected_without_output(engine,decision,now=NOW-timedelta(microseconds=1),name="future")
        self.assert_rejected_without_output(engine,decision,now=NOW+timedelta(seconds=120),name="expired")

    def test_filename_binding_and_existing_ticket_are_preserved(self):
        engine=self.engine()
        decision=engine.run(evidence(),now=NOW,executor="freqtrade_dry_run")
        wrong=self.directory/"wrong"/"ticket.json"
        with self.assertRaises(ValueError):engine.export_freqtrade_ticket(decision,wrong,now=NOW)
        self.assertFalse(wrong.parent.exists())
        path=self.output(decision)
        engine.export_freqtrade_ticket(decision,path,now=NOW)
        original=path.read_bytes()
        with self.assertRaises(FileExistsError):engine.export_freqtrade_ticket(decision,path,now=NOW)
        self.assertEqual(path.read_bytes(),original)


if __name__=="__main__":unittest.main()
