from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from torinteo_paper.cli import main
from torinteo_paper.demo_data import DEMO_TIME, demo_collection_and_context, demo_execution_bar, demo_snapshot
from torinteo_paper.inputs import assemble_snapshot, read_json, write_json
from torinteo_paper.models import RiskConfig, iso


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, value):
        path = self.root / name
        write_json(path, value)
        return str(path)

    def invoke(self, *args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(list(args))
        return status, json.loads(out.getvalue() or err.getvalue())

    def test_file_workflow_survives_separate_commands_and_audits_stop_first(self):
        collection, context = demo_collection_and_context()
        candles = self.put("candles.json", collection)
        reviews = self.put("context.json", context)
        snapshot_path = str(self.root / "snapshot.json")
        database = str(self.root / "paper.sqlite3")
        code, result = self.invoke("assemble", "--candles", candles, "--context", reviews, "--out", snapshot_path)
        self.assertEqual(code, 0)
        self.assertEqual(result["provenance"], "synthetic")
        code, decision = self.invoke("replay", "--snapshot", snapshot_path, "--db", database)
        self.assertEqual((code, decision["call"]), (0, "LONG"))
        for index, ambiguous in enumerate((False, True)):
            bar = demo_execution_bar(ambiguous=ambiguous)
            path = self.put(f"bar-{index}.json", {"schema_version": 1, "symbol": "TEST/USD",
                            "provenance": "synthetic", "timeframe": "15m", "observed_at": bar["close_time"], "bar": bar})
            code, result = self.invoke("advance", "--bar", path, "--db", database)
            self.assertEqual(code, 0)
        code, result = self.invoke("audit", "--db", database)
        self.assertEqual(code, 0)
        self.assertEqual(len(result["orders"]), 1)
        self.assertEqual(result["orders"][0]["status"], "CLOSED")
        self.assertEqual(result["orders"][0]["exit_reason"], "STOP_LOSS")
        self.assertGreater(result["orders"][0]["fees"], 0)
        self.assertLess(result["account"]["daily_pnl"], 0)
        close = [event for event in result["events"] if event["kind"] == "PAPER_CLOSE"][0]
        self.assertTrue(close["payload"]["intrabar_ambiguous"])

    def test_demo_is_labelled_and_cannot_overwrite_an_existing_experiment(self):
        database = str(self.root / "demo.sqlite3")
        code, result = self.invoke("demo", "--db", database)
        self.assertEqual(code, 0)
        self.assertIn("SYNTHETIC", result["demonstration"])
        self.assertEqual(result["approved_call"], "LONG")
        self.assertEqual(result["blocked_call"], "NO_TRADE")
        self.assertIn("OPPOSING_LEVEL_TOO_CLOSE", result["blocked_reasons"])
        original = Path(database).read_bytes()
        self.assertEqual(self.invoke("demo", "--db", database)[0], 2)
        self.assertEqual(Path(database).read_bytes(), original)

    def test_market_and_replay_commands_cannot_swap_provenance(self):
        synthetic = self.put("synthetic.json", demo_snapshot())
        market = demo_snapshot()
        market["source"]["kind"] = "market"
        market_path = self.put("market.json", market)
        for command, snapshot_path in (("evaluate", synthetic), ("replay", market_path)):
            database = self.root / f"{command}.sqlite3"
            code, result = self.invoke(command, "--snapshot", snapshot_path, "--db", str(database))
            self.assertEqual(code, 2)
            self.assertFalse(database.exists())

    def test_market_evaluation_uses_current_receipt_time(self):
        market = demo_snapshot()
        market["source"]["kind"] = "market"
        path = self.put("stale.json", market)
        with patch("torinteo_paper.cli.current_time", return_value=DEMO_TIME + timedelta(days=1)):
            code, result = self.invoke("evaluate", "--snapshot", path, "--db", str(self.root / "market.sqlite3"))
        self.assertEqual(code, 0)
        self.assertEqual(result["call"], "NO_TRADE")
        self.assertIn("STALE_SNAPSHOT", result["reason_codes"])

    def test_future_historical_replay_is_rejected_before_database_creation(self):
        snapshot = demo_snapshot(DEMO_TIME + timedelta(days=1))
        snapshot["source"]["kind"] = "historical"
        path = self.put("future-history.json", snapshot)
        database = self.root / "future.sqlite3"
        with patch("torinteo_paper.cli.current_time", return_value=DEMO_TIME):
            code, result = self.invoke("replay", "--snapshot", path, "--db", str(database))
        self.assertEqual(code, 2)
        self.assertFalse(database.exists())

    def test_historical_execution_cannot_claim_a_future_observation(self):
        snapshot = demo_snapshot()
        snapshot["source"]["kind"] = "historical"
        path = self.put("history.json", snapshot)
        database = str(self.root / "history.sqlite3")
        with patch("torinteo_paper.cli.current_time", return_value=DEMO_TIME):
            code, _ = self.invoke("replay", "--snapshot", path, "--db", database)
        self.assertEqual(code, 0)
        bar = demo_execution_bar()
        path = self.put("future-history-bar.json", {"schema_version": 1, "symbol": "TEST/USD",
                        "provenance": "historical", "timeframe": "15m", "observed_at": bar["close_time"], "bar": bar})
        with patch("torinteo_paper.cli.current_time", return_value=DEMO_TIME):
            code, _ = self.invoke("advance", "--bar", path, "--db", database)
        self.assertEqual(code, 2)
        _, audit = self.invoke("audit", "--db", database)
        self.assertEqual(audit["orders"][0]["status"], "PENDING")

    def test_replay_does_not_activate_required_online_jev(self):
        config = self.put("required.json", RiskConfig(jev_mode="required").to_dict())
        path = self.put("snapshot.json", demo_snapshot())
        database = str(self.root / "required.sqlite3")
        with patch("torinteo_paper.adapters.jev.JevClient", side_effect=AssertionError("online provider activated")):
            code, result = self.invoke("replay", "--snapshot", path, "--db", database, "--config", config)
        self.assertEqual((code, result["call"]), (0, "NO_TRADE"))
        self.assertIn("JEV_REQUIRED_UNAVAILABLE", result["reason_codes"])
        # audit reuses the persisted nondefault config without requiring a flag.
        code, result = self.invoke("audit", "--db", database)
        self.assertEqual((code, result["orders"]), (0, []))

    def test_doctor_reports_presence_without_disclosing_key(self):
        canary = "local-test-canary-do-not-print"
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": canary}):
            code, result = self.invoke("doctor")
        self.assertEqual(code, 0)
        self.assertTrue(result["typesafe_key_present"])
        self.assertNotIn(canary, json.dumps(result))
        self.assertFalse(result["provider_connectivity_tested"])
        self.assertFalse(result["live_execution_available"])

    def test_audit_and_advance_cannot_create_a_new_database(self):
        for command in ("audit", "advance"):
            path = self.root / f"missing-{command}.sqlite3"
            args = [command, "--db", str(path)]
            if command == "advance":
                args.extend(["--bar", "irrelevant.json"])
            self.assertEqual(self.invoke(*args)[0], 2)
            self.assertFalse(path.exists())

    def test_collect_records_retrieval_time_without_inventing_review_context(self):
        collection, _ = demo_collection_and_context()
        returned = {key: deepcopy(value) for key, value in collection.items()
                    if key not in {"schema_version", "source_kind", "reference", "observed_at"}}
        path = self.root / "collected.json"
        with patch("torinteo_paper.adapters.openbb.OpenBBAdapter") as adapter:
            adapter.return_value.fetch_closed_candles.return_value = returned
            with patch("torinteo_paper.cli.current_time", side_effect=[DEMO_TIME, DEMO_TIME + timedelta(seconds=1)]):
                code, result = self.invoke("collect", "--symbol", "TEST/USD", "--asset-class", "crypto",
                                           "--start-date", "2026-10-18", "--end-date", "2026-10-19", "--out", str(path))
        self.assertEqual(code, 0)
        recorded = read_json(path)
        self.assertEqual(recorded["source_kind"], "market")
        self.assertEqual(recorded["observed_at"], iso(DEMO_TIME + timedelta(seconds=1)))
        self.assertFalse(recorded["point_in_time_archive"])
        for key in ("quote", "reviews", "news", "levels"):
            self.assertNotIn(key, recorded)


class AssemblyBoundaryTests(unittest.TestCase):
    def test_later_context_cannot_make_an_unclosed_collection_candle_known_earlier(self):
        collection, context = demo_collection_and_context()
        # The entire series is moved forward, but its collection still claims
        # that all of it was available at the original collection time.
        from torinteo_paper.models import utc
        for bar in collection["candles"]:
            for key in ("open_time", "close_time"):
                bar[key] = iso(utc(bar[key]) + timedelta(minutes=15))
        context["as_of"] = iso(DEMO_TIME + timedelta(minutes=15))
        with self.assertRaisesRegex(ValueError, "future|unclosed"):
            assemble_snapshot(collection, context)

    def test_cannot_promote_synthetic_context_to_market(self):
        collection, context = demo_collection_and_context()
        collection["source_kind"] = "market"
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            assemble_snapshot(collection, context)

    def test_cannot_backdate_collection_to_an_earlier_decision(self):
        collection, context = demo_collection_and_context()
        collection["observed_at"] = iso(DEMO_TIME + timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, "not available"):
            assemble_snapshot(collection, context)

    def test_historical_data_requires_recorded_point_in_time_archive(self):
        collection, context = demo_collection_and_context()
        collection["source_kind"] = context["source_kind"] = "historical"
        with self.assertRaisesRegex(ValueError, "point-in-time"):
            assemble_snapshot(collection, context)

    def test_missing_quote_reference_or_future_level_is_rejected(self):
        for fault in ("quote", "future"):
            collection, context = demo_collection_and_context()
            if fault == "quote":
                context["quote"]["reference"] = ""
            else:
                context["levels"][0]["known_at"] = iso(DEMO_TIME + timedelta(minutes=1))
            with self.assertRaises(ValueError):
                assemble_snapshot(collection, context)

    def test_conflicting_candle_identity_cannot_be_silently_relabelled(self):
        collection, context = demo_collection_and_context()
        collection["symbol"] = "OTHER/USD"
        with self.assertRaisesRegex(ValueError, "identity"):
            assemble_snapshot(collection, context)

    def test_duplicate_json_keys_and_nonfinite_constants_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            for raw in ('{"source_kind":"synthetic","source_kind":"market"}', '{"bid":NaN}', '{"bid":Infinity}'):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_json(path)

    def test_output_artifacts_are_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.json"
            write_json(path, {"source_kind": "synthetic"})
            with self.assertRaises(FileExistsError):
                write_json(path, {"source_kind": "market"})
            self.assertEqual(read_json(path)["source_kind"], "synthetic")


if __name__ == "__main__":
    unittest.main()
