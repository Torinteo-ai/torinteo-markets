"""HTTP behavior, owner boundary and real persisted paper execution."""
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from torinteo_paper.demo_data import DEMO_TIME, demo_execution_bar, demo_snapshot
from torinteo_paper.models import iso

try:
    from fastapi.testclient import TestClient
    from torinteo_paper.web import create_app
    WEB_AVAILABLE = True
except ImportError:
    WEB_AVAILABLE = False


@unittest.skipUnless(WEB_AVAILABLE, "install the web/test extras to run HTTP tests")
class WebAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"TORINTEO_WEB_TOKEN": "", "VERCEL": ""})
        self.environment.start()
        self.now = DEMO_TIME + timedelta(days=1)
        self.restart()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.environment.stop()
        self.directory.cleanup()

    def restart(self):
        if hasattr(self, "client"):
            self.client.__exit__(None, None, None)
        self.app = create_app(self.directory.name, clock=lambda: self.now)
        self.client = TestClient(self.app, base_url="http://localhost")
        self.client.__enter__()

    def create(self, kind="synthetic", **options):
        response = self.client.post("/api/experiments", json={"name": "Owner experiment", "kind": kind, **options})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def action(self, identifier, step):
        response = self.client.post(f"/api/experiments/{identifier}/demo", json={"step": step})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_synthetic_create_prepares_evidence_without_orders(self):
        result = self.create()
        self.assertEqual(result["demo"]["next_step"], "evaluate")
        self.assertEqual(result["latest_snapshot"]["source"]["kind"], "synthetic")
        self.assertEqual(result["orders"], [])
        self.assertEqual(result["decisions"], [])
        self.assertEqual(result["account"]["equity"], 10000)

    def test_real_ledger_demo_survives_restart_and_settles_stop_first(self):
        identifier = self.create()["experiment"]["id"]
        result = self.action(identifier, "evaluate")
        self.assertEqual(result["decisions"][0]["call"], "LONG")
        self.assertEqual(result["orders"][0]["status"], "PENDING")
        first = result["decisions"][0]["decision_id"]
        repeated = self.action(identifier, "evaluate")
        self.assertEqual(len(repeated["orders"]), 1)
        result = self.action(identifier, "fill")
        self.assertEqual(result["orders"][0]["status"], "OPEN")
        self.restart()
        result = self.client.get(f"/api/experiments/{identifier}").json()
        self.assertEqual(result["demo"]["next_step"], "close")
        self.assertEqual(result["decisions"][0]["decision_id"], first)
        result = self.action(identifier, "close")
        self.assertEqual(result["orders"][0]["status"], "CLOSED")
        self.assertEqual(result["orders"][0]["exit_reason"], "STOP_LOSS")
        closed = next(event for event in result["events"] if event["kind"] == "PAPER_CLOSE")
        self.assertTrue(closed["payload"]["intrabar_ambiguous"])
        self.assertLess(result["account"]["equity"], 10000)
        result = self.action(identifier, "blocked")
        self.assertEqual(result["decisions"][-1]["call"], "NO_TRADE")
        self.assertIsNone(result["demo"]["next_step"])

    def test_demo_cannot_skip_execution_order(self):
        identifier = self.create()["experiment"]["id"]
        response = self.client.post(f"/api/experiments/{identifier}/demo", json={"step": "close"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get(f"/api/experiments/{identifier}").json()["events"], [])

    def test_experiments_are_distinct_and_listed_after_restart(self):
        first, second = self.create(), self.create("market")
        self.assertNotEqual(first["experiment"]["id"], second["experiment"]["id"])
        self.action(first["experiment"]["id"], "evaluate")
        self.restart()
        self.assertEqual(len(self.client.get("/api/experiments").json()["experiments"]), 2)
        market = self.client.get("/api/experiments/" + second["experiment"]["id"]).json()
        self.assertEqual(market["orders"], [])
        self.assertIsNone(market["latest_snapshot"])

    def test_wrong_kind_input_and_demo_leave_market_ledger_untouched(self):
        identifier = self.create("market")["experiment"]["id"]
        for endpoint, body in (("evaluate", {"snapshot": demo_snapshot()}), ("demo", {"step": "prepare"})):
            response = self.client.post(f"/api/experiments/{identifier}/{endpoint}", json=body)
            self.assertEqual(response.status_code, 409)
        result = self.client.get(f"/api/experiments/{identifier}").json()
        self.assertEqual(result["decisions"], [])

    def test_historical_future_rejected(self):
        identifier = self.create("historical")["experiment"]["id"]
        snapshot = demo_snapshot(self.now + timedelta(days=1))
        snapshot["source"]["kind"] = "historical"
        response = self.client.post(f"/api/experiments/{identifier}/evaluate", json={"snapshot": snapshot})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(f"/api/experiments/{identifier}").json()["decisions"], [])

    def test_market_evaluation_uses_wall_clock_not_old_snapshot_time(self):
        identifier = self.create("market")["experiment"]["id"]
        snapshot = demo_snapshot()
        snapshot["source"]["kind"] = "market"
        response = self.client.post(f"/api/experiments/{identifier}/evaluate", json={"snapshot": snapshot})
        self.assertEqual(response.status_code, 200, response.text)
        decision = response.json()["decision"]
        self.assertEqual(decision["call"], "NO_TRADE")
        self.assertEqual(decision["approved_at"], iso(self.now))
        self.assertEqual(response.json()["orders"], [])

    def test_manual_evaluation_and_execution_envelope_use_existing_ledger(self):
        identifier = self.create()["experiment"]["id"]
        response = self.client.post(f"/api/experiments/{identifier}/evaluate", json={"snapshot": demo_snapshot()})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["demo"]["enabled"])
        envelope = {"schema_version": 1, "symbol": "TEST/USD", "provenance": "synthetic", "timeframe": "15m",
                    "observed_at": iso(DEMO_TIME + timedelta(minutes=15)), "bar": demo_execution_bar()}
        response = self.client.post(f"/api/experiments/{identifier}/advance", json=envelope)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["orders"][0]["status"], "OPEN")

    def test_old_idempotent_evaluation_does_not_replace_latest_research(self):
        identifier = self.create("historical")["experiment"]["id"]
        first = demo_snapshot()
        first["source"]["kind"] = "historical"
        latest = demo_snapshot(self.now)
        latest["source"]["kind"] = "historical"
        for snapshot in (first, latest):
            response = self.client.post(f"/api/experiments/{identifier}/evaluate", json={"snapshot": snapshot})
            self.assertEqual(response.status_code, 200, response.text)
        repeated = self.client.post(f"/api/experiments/{identifier}/evaluate", json={"snapshot": first})
        self.assertEqual(repeated.status_code, 200, repeated.text)
        result = repeated.json()
        self.assertEqual(len(result["decisions"]), 2)
        self.assertEqual(result["decision"]["as_of"], first["as_of"])
        self.assertEqual(result["latest_snapshot"], latest)
        self.assertEqual(result["as_of"], iso(self.now))

    def test_equal_observation_times_use_original_decision_order_on_retry(self):
        identifier = self.create("historical")["experiment"]["id"]
        first = demo_snapshot()
        first["source"]["kind"] = "historical"
        latest = deepcopy(first)
        latest["as_of"] = "2026-10-19T10:00:00+01:00"
        latest["source"]["reference"] = "Second reviewed source at the same UTC instant"
        for snapshot in (first, latest):
            response = self.client.post(f"/api/experiments/{identifier}/evaluate", json={"snapshot": snapshot})
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["latest_snapshot"], latest)
        repeated = self.client.post(f"/api/experiments/{identifier}/evaluate", json={"snapshot": first})
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(len(repeated.json()["decisions"]), 2)
        self.assertEqual(repeated.json()["latest_snapshot"], latest)

    def test_new_decision_with_older_market_evidence_does_not_replace_latest_research(self):
        identifier = self.create("market")["experiment"]["id"]
        latest = demo_snapshot(self.now)
        latest["source"]["kind"] = "market"
        older = demo_snapshot(self.now - timedelta(seconds=30))
        older["source"]["kind"] = "market"
        response = self.client.post(f"/api/experiments/{identifier}/evaluate", json={"snapshot": latest})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(f"/api/experiments/{identifier}/evaluate", json={"snapshot": older})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["decisions"]), 2)
        self.assertEqual(response.json()["decision"]["as_of"], older["as_of"])
        self.assertEqual(response.json()["latest_snapshot"], latest)

    def test_wrong_kind_bar_and_future_market_observation_rejected(self):
        identifier = self.create("market")["experiment"]["id"]
        envelope = {"schema_version": 1, "symbol": "TEST/USD", "provenance": "synthetic", "timeframe": "15m",
                    "observed_at": iso(DEMO_TIME + timedelta(minutes=15)), "bar": demo_execution_bar()}
        response = self.client.post(f"/api/experiments/{identifier}/advance", json=envelope)
        self.assertEqual(response.status_code, 409)
        envelope.update(provenance="market", observed_at=iso(self.now + timedelta(minutes=1)))
        response = self.client.post(f"/api/experiments/{identifier}/advance", json=envelope)
        self.assertEqual(response.status_code, 400)

    def test_audit_export_contains_records_without_server_paths(self):
        identifier = self.create()["experiment"]["id"]
        self.action(identifier, "evaluate")
        response = self.client.get(f"/api/experiments/{identifier}/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response.headers["content-disposition"])
        self.assertEqual(response.json()["export"]["kind"], "paper_audit")
        self.assertEqual(len(response.json()["decisions"]), 1)
        self.assertNotIn(self.directory.name, response.text)

    def test_names_configuration_and_paths_are_bounded(self):
        for payload in ({"name": "x" * 81, "kind": "market"}, {"name": "x\ny", "kind": "market"},
                        {"name": "x", "kind": "market", "path": "/tmp/evil"},
                        {"name": "x", "kind": "market", "config": {"mode": "live"}},
                        {"name": "x", "kind": "market", "config": {"secret": "value"}}):
            response = self.client.post("/api/experiments", json=payload)
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.client.get("/api/experiments/not-a-uuid").status_code, 404)
        self.assertEqual(self.client.get("/api/experiments/00000000-0000-0000-0000-000000000000").status_code, 404)

    def test_json_boundary_rejects_duplicates_nan_overflow_and_depth(self):
        for raw in ('{"name":"one","name":"two","kind":"market"}', '{"name":"x","kind":"market","config":{"initial_equity":NaN}}',
                    '{"name":"x","kind":"market","config":{"initial_equity":1e999}}',
                    '{"x":' + '[' * 50 + '0' + ']' * 50 + '}'):
            response = self.client.post("/api/experiments", content=raw, headers={"content-type": "application/json"})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/experiments", json=[]).status_code, 400)

    def test_body_size_and_content_type_enforced(self):
        self.assertEqual(self.client.post("/api/experiments", content='{}').status_code, 415)
        self.assertEqual(self.client.post("/api/experiments", content="x" * (2 * 1024 * 1024 + 1),
                                         headers={"content-type": "application/json"}).status_code, 413)

    def test_host_and_origin_prevent_cross_site_local_mutations(self):
        for headers in ({"host": "attacker.example"}, {"origin": "https://attacker.example"},
                        {"origin": "null"}, {"sec-fetch-site": "cross-site"}):
            self.assertEqual(self.client.post("/api/experiments", json={"name": "x", "kind": "market"}, headers=headers).status_code, 403)
        response = self.client.post("/api/experiments", json={"name": "x", "kind": "market"}, headers={"origin": "http://localhost"})
        self.assertEqual(response.status_code, 201)

    def test_explicit_development_origin_only(self):
        with TestClient(create_app(self.directory.name, allowed_origins=["http://127.0.0.1:5173"]), base_url="http://localhost") as client:
            self.assertEqual(client.get("/api/health", headers={"origin": "http://127.0.0.1:5173"}).status_code, 200)
            self.assertEqual(client.get("/api/health", headers={"origin": "http://127.0.0.1:5174"}).status_code, 403)
        with self.assertRaises(ValueError):
            create_app(self.directory.name, allowed_origins=["https://attacker.example"])

    def test_network_bind_requires_token_and_host_allowlist(self):
        with self.assertRaises(ValueError):
            create_app(self.directory.name, host="0.0.0.0")
        with self.assertRaises(ValueError):
            create_app(self.directory.name, host="0.0.0.0", token="short")
        app = create_app(self.directory.name, host="0.0.0.0", token="A" * 40)
        with TestClient(app, base_url="http://desk.example") as client:
            self.assertEqual(client.get("/api/health").status_code, 403)

    def test_network_login_uses_httponly_secure_cookie_and_never_returns_token(self):
        token = "OwnerSecretToken" + "x" * 32
        app = create_app(self.directory.name, host="0.0.0.0", token=token, allowed_hosts=["desk.example"])
        with TestClient(app, base_url="https://desk.example") as client:
            self.assertEqual(client.get("/api/experiments").status_code, 401)
            self.assertFalse(client.get("/api/session").json()["authenticated"])
            self.assertEqual(client.post("/api/login", json={"token": "wrong"}).status_code, 401)
            response = client.post("/api/login", json={"token": token})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(token, response.text)
            cookie = response.headers["set-cookie"].lower()
            self.assertIn("httponly", cookie)
            self.assertIn("samesite=strict", cookie)
            self.assertIn("secure", cookie)
            self.assertNotIn(token, cookie)
            self.assertEqual(client.get("/api/experiments").status_code, 200)
            self.assertEqual(client.post("/api/logout", json={}).status_code, 200)
            self.assertEqual(client.get("/api/experiments").status_code, 401)

    def test_tampered_owner_cookie_is_rejected(self):
        app = create_app(self.directory.name, host="0.0.0.0", token="A" * 40, allowed_hosts=["desk.example"])
        with TestClient(app, base_url="http://desk.example") as client:
            client.cookies.set("torinteo_owner", "forged.1234")
            self.assertEqual(client.get("/api/experiments").status_code, 401)

    def test_credential_presence_exposes_booleans_only(self):
        secret = "do-not-return-this-typesafe-key"
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": secret}):
            response = self.client.get("/api/integrations")
        self.assertTrue(response.json()["typesafe_key_present"])
        self.assertFalse(response.json()["live_execution_available"])
        self.assertNotIn(secret, response.text)

    def test_vercel_durable_sqlite_misconfiguration_refused(self):
        with patch.dict(os.environ, {"VERCEL": "1"}):
            with self.assertRaises(ValueError):
                create_app(self.directory.name)

    def test_symlink_experiment_database_rejected(self):
        identifier = self.create()["experiment"]["id"]
        path = Path(self.directory.name) / identifier / "ledger.sqlite3"
        outside = Path(self.directory.name) / "unrelated.sqlite3"
        path.rename(outside)
        path.symlink_to(outside)
        self.assertEqual(self.client.get(f"/api/experiments/{identifier}").status_code, 404)

    def test_static_spa_and_assets_cannot_escape_build_directory(self):
        static = Path(self.directory.name) / "dist"
        static.mkdir()
        (static / "index.html").write_text("<h1>Torinteo</h1>")
        (static / "asset.js").write_text("window.ready=true")
        outside = Path(self.directory.name) / "private.txt"
        outside.write_text("do-not-expose")
        (static / "escape.txt").symlink_to(outside)
        with TestClient(create_app(Path(self.directory.name) / "other-data", static), base_url="http://localhost") as client:
            self.assertEqual(client.get("/research").status_code, 200)
            self.assertEqual(client.get("/asset.js").status_code, 200)
            self.assertEqual(client.get("/escape.txt").status_code, 404)
            self.assertEqual(client.get("/%2e%2e/private.txt").status_code, 404)
            self.assertEqual(client.get("/.env").status_code, 404)
            self.assertEqual(client.get("/missing.js").status_code, 404)
            self.assertEqual(client.get("/api/unknown").status_code, 404)

    def test_market_endpoint_calls_explicit_provider_and_not_engine(self):
        class Provider:
            def collect(self, **kwargs):
                self.request = kwargs
                return {"symbol": kwargs["symbol"], "source_kind": "market", "candles": [], "quote": None}
        provider = Provider()
        with TestClient(create_app(self.directory.name, market_client=provider), base_url="http://localhost") as client:
            response = client.get("/api/market", params={"symbol": "BTC/USD", "limit": 50})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(provider.request, {"symbol": "BTC/USD", "limit": 50, "timeframe": "15m"})
            self.assertEqual(client.get("/api/experiments").json()["experiments"], [])
            self.assertEqual(client.get("/api/market?limit=301").status_code, 400)

    def test_sync_route_rejects_synthetic_experiment_without_mutation(self):
        identifier = self.create()["experiment"]["id"]
        before = self.client.get(f"/api/experiments/{identifier}").json()
        response = self.client.post(f"/api/experiments/{identifier}/sync", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("market experiment", response.json()["error"])
        self.assertEqual(self.client.get(f"/api/experiments/{identifier}").json(), before)

    def test_sync_route_market_idle_needs_no_provider_call(self):
        class UnusedProvider:
            def collect(self, **_kwargs):
                raise AssertionError("An idle experiment must not call the provider")
        app = create_app(self.directory.name, clock=lambda: self.now, market_client=UnusedProvider())
        with TestClient(app, base_url="http://localhost") as client:
            identifier = client.post("/api/experiments", json={"name": "Idle market", "kind": "market"}).json()["experiment"]["id"]
            response = client.post(f"/api/experiments/{identifier}/sync", json={})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["sync"]["status"], "idle")
            self.assertEqual(result["sync"]["processed_bars"], 0)
            self.assertEqual(result["sync"]["symbols"], [])
            self.assertFalse(result["sync"]["runs_continuously"])
            self.assertEqual(result["orders"], [])
            self.assertEqual(result["decisions"], [])

    def test_sync_route_maps_provider_unavailable_without_leaking_details(self):
        from torinteo_paper.adapters.public_market import PublicMarketUnavailable
        provider_detail = "provider-private-diagnostic-do-not-return"

        class UnavailableProvider:
            def collect(self, **_kwargs):
                raise PublicMarketUnavailable(provider_detail)

        app = create_app(self.directory.name, clock=lambda: self.now, market_client=UnavailableProvider())
        with TestClient(app, base_url="http://localhost") as client:
            identifier = client.post("/api/experiments", json={"name": "Market contract test", "kind": "market"}).json()["experiment"]["id"]
            # Invented fixture values carry the market contract label solely
            # inside this isolated route test; they are not market observations.
            snapshot = demo_snapshot(self.now)
            snapshot["symbol"] = "BTC/USD"
            snapshot["source"]["kind"] = "market"
            approved = client.post(f"/api/experiments/{identifier}/evaluate", json={"snapshot": snapshot})
            self.assertEqual(approved.status_code, 200, approved.text)
            self.assertEqual(approved.json()["orders"][0]["status"], "PENDING")
            before = client.get(f"/api/experiments/{identifier}").json()
            response = client.post(f"/api/experiments/{identifier}/sync", json={})
            self.assertEqual(response.status_code, 503)
            self.assertIn("Public execution candles are unavailable", response.json()["error"])
            self.assertNotIn(provider_detail, response.text)
            self.assertEqual(client.get(f"/api/experiments/{identifier}").json(), before)

    def test_sync_route_requires_owner_cookie_and_empty_json_body(self):
        app = create_app(self.directory.name, host="0.0.0.0", token="A" * 40,
                         allowed_hosts=["desk.example"], clock=lambda: self.now)
        with TestClient(app, base_url="https://desk.example") as client:
            identifier = self.create("market")["experiment"]["id"]
            self.assertEqual(client.post(f"/api/experiments/{identifier}/sync", json={}).status_code, 401)
            self.assertEqual(client.post("/api/login", json={"token": "A" * 40}).status_code, 200)
            self.assertEqual(client.post(f"/api/experiments/{identifier}/sync", json={"force": True}).status_code, 400)
            self.assertEqual(client.post(f"/api/experiments/{identifier}/sync", content="{}").status_code, 415)
            self.assertEqual(client.post(f"/api/experiments/{identifier}/sync", json={}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
