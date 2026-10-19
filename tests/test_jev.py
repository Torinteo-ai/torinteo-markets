"""Jev contract tests: every transport is local and no inference is requested."""

import io
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from torinteo_paper.adapters import jev


STATE = {
    "candidate": {"symbol": "EXAMPLE", "direction": "long"},
    "evidence": {"provenance": "synthetic", "deterministic_checks_passed": True},
}


def response(choice="pass"):
    probabilities = {"pass": 0.9, "reject": 0.1}
    if choice == "reject":
        probabilities = {"pass": 0.1, "reject": 0.9}
    return {
        "model": "jev-1.13.0",
        "answers": {
            "entry_gate": {
                "type": "choice",
                "choice": choice,
                "confidence": 0.8,
                "probabilities": probabilities,
            }
        },
        "usage": {"input_tokens": 200, "output_tokens": 20},
    }


class FakeHTTPResponse:
    def __init__(self, body, headers=None, url=None, status=200):
        self.body = io.BytesIO(body)
        self.headers = headers if headers is not None else {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        self.url = url or jev.JEV_ENDPOINT
        self.status = status
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.body.close()

    def geturl(self):
        return self.url

    def read(self, size):
        self.read_sizes.append(size)
        return self.body.read(size)


class JevContractTests(unittest.TestCase):
    def client(self, result=None, **kwargs):
        transport = Mock(return_value=response() if result is None else result)
        return jev.JevClient(api_key="unit-test-secret", transport=transport, **kwargs), transport

    def assert_result_shape(self, result):
        self.assertEqual(set(result), {
            "status", "provider", "model", "choice", "confidence",
            "probabilities", "reason", "latency_ms",
        })
        self.assertEqual(result["provider"], "jev")
        self.assertGreaterEqual(result["latency_ms"], 0)
        self.assertNotIn("unit-test-secret", json.dumps(result))

    def test_request_and_response_match_documented_choice_contract(self):
        client, transport = self.client()
        result = client.evaluate(STATE)
        self.assert_result_shape(result)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["choice"], "pass")
        self.assertEqual(result["confidence"], 0.8)
        self.assertEqual(result["probabilities"]["pass"], 0.9)
        url, headers, payload_bytes, timeout = transport.call_args.args
        self.assertEqual(url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(headers["Authorization"], "Bearer unit-test-secret")
        self.assertEqual(timeout, 2.0)
        payload = json.loads(payload_bytes)
        self.assertEqual(set(payload), {"model", "state", "questions"})
        self.assertEqual(payload["state"], STATE)
        self.assertEqual(payload["model"], "jev-1.13.0")
        self.assertEqual(set(payload["questions"]), {"entry_gate"})
        question = payload["questions"]["entry_gate"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual(set(question["criteria"]), {"pass", "reject"})
        self.assertNotIn(b"unit-test-secret", payload_bytes)

    def test_reject_is_preserved(self):
        client, _ = self.client(response("reject"))
        result = client.evaluate(STATE)
        self.assertEqual((result["status"], result["choice"]), ("ok", "reject"))

    def test_uncertain_distribution_is_not_rewritten_as_confident(self):
        fixture = response()
        answer = fixture["answers"]["entry_gate"]
        answer.update(probabilities={"pass": 0.5, "reject": 0.5}, confidence=0.0)
        client, _ = self.client(fixture)
        result = client.evaluate(STATE)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["confidence"], 0.0)

    def test_no_key_never_calls_transport(self):
        with patch.dict(os.environ, {"TYPESAFE_AI_API_KEY": "wrong-variable"}, clear=True):
            transport = Mock()
            result = jev.JevClient(transport=transport).evaluate(STATE)
        self.assert_result_shape(result)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "api_key_not_configured")
        self.assertIsNone(result["choice"])
        transport.assert_not_called()

    def test_canonical_environment_key_and_explicit_override(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "environment-secret"}, clear=True):
            for key, expected in [(None, "environment-secret"), ("argument-secret", "argument-secret")]:
                with self.subTest(key=key):
                    transport = Mock(return_value=response())
                    result = jev.JevClient(api_key=key, transport=transport).evaluate(STATE)
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(transport.call_args.args[1]["Authorization"], f"Bearer {expected}")
            transport = Mock()
            result = jev.JevClient(api_key="", transport=transport).evaluate(STATE)
            self.assertEqual(result["status"], "unavailable")
            transport.assert_not_called()

    def test_invalid_key_is_not_sent(self):
        for key in [True, 12, "secret\r\nInjected: value", "has space", "x" * 5000]:
            with self.subTest(key_type=type(key).__name__):
                transport = Mock()
                result = jev.JevClient(api_key=key, transport=transport).evaluate(STATE)
                self.assertEqual(result["status"], "invalid")
                transport.assert_not_called()

    def test_invalid_configuration_never_calls_transport(self):
        for kwargs in [
            {"model": "jev-latest"}, {"model": "other-model"}, {"model": None},
            {"timeout_seconds": True}, {"timeout_seconds": 0},
            {"timeout_seconds": -1}, {"timeout_seconds": float("nan")},
            {"timeout_seconds": float("inf")}, {"timeout_seconds": 31},
        ]:
            with self.subTest(kwargs=kwargs):
                client, transport = self.client(**kwargs)
                result = client.evaluate(STATE)
                self.assertEqual(result["status"], "invalid")
                transport.assert_not_called()
        result = jev.JevClient(api_key="unit-test-secret", transport="not-callable").evaluate(STATE)
        self.assertEqual(result["status"], "invalid")

    def test_explicit_documented_model_version(self):
        fixture = response()
        fixture["model"] = "jev-1.14.0"
        client, _ = self.client(fixture, model="jev-1.14.0")
        self.assertEqual(client.evaluate(STATE)["status"], "ok")

    def test_invalid_state_never_reaches_transport(self):
        cyclic = {}
        cyclic["self"] = cyclic
        states = [None, [], {1: "non-string-key"}, {"x": object()},
                  {"x": float("nan")}, {"x": float("inf")}, cyclic]
        for state in states:
            with self.subTest(state_type=type(state).__name__):
                client, transport = self.client()
                result = client.evaluate(state)
                self.assertEqual(result["status"], "invalid")
                transport.assert_not_called()

    def test_credentials_are_refused_even_when_nested(self):
        for state in [
            {"api_key": "other-secret"},
            {"nested": [{"Authorization": "Bearer other-secret"}]},
            {"nested": {"broker_private_key": "other-secret"}},
            {"note": "Do not send unit-test-secret to the model"},
        ]:
            with self.subTest(state_keys=list(state)):
                client, transport = self.client()
                result = client.evaluate(state)
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(result["reason"], "state_contains_credentials")
                self.assert_result_shape(result)
                transport.assert_not_called()

    def test_input_size_is_bounded_before_request(self):
        client, transport = self.client()
        result = client.evaluate({"evidence": "x" * (jev.MAX_INPUT_BYTES + 1)})
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reason"], "input_too_large")
        transport.assert_not_called()

    def test_invalid_response_schemas_and_values(self):
        fixtures = [[], {}, {"model": "jev-1.13.0"}]
        for field, value in [("model", "jev-latest"), ("model", "jev-1.14.0"),
                             ("model", None), ("model", 13), ("answers", []), ("usage", {})]:
            fixture = response()
            fixture[field] = value
            fixtures.append(fixture)
        for field, value in [
            ("type", "score"), ("choice", "BUY"), ("choice", True),
            ("confidence", True), ("confidence", float("nan")),
            ("confidence", float("inf")), ("confidence", -0.1),
            ("confidence", 1.1), ("confidence", "0.8"),
            ("probabilities", {"pass": 0.9}),
            ("probabilities", {"pass": 0.9, "reject": 0.1, "hold": 0.0}),
            ("probabilities", {"pass": True, "reject": 0}),
            ("probabilities", {"pass": float("nan"), "reject": 0.1}),
            ("probabilities", {"pass": float("inf"), "reject": 0.1}),
            ("probabilities", {"pass": -0.1, "reject": 1.1}),
            ("probabilities", {"pass": 0.8, "reject": 0.1}),
            ("probabilities", {"pass": 0.1, "reject": 0.9}),
            ("probabilities", {"pass": "0.9", "reject": 0.1}),
        ]:
            fixture = response()
            fixture["answers"]["entry_gate"][field] = value
            fixtures.append(fixture)
        for name, value in [("input_tokens", True), ("output_tokens", -1), ("input_tokens", 1.5)]:
            fixture = response()
            fixture["usage"][name] = value
            fixtures.append(fixture)
        fixture = response()
        fixture["answers"]["unexpected_question"] = fixture["answers"].pop("entry_gate")
        fixtures.append(fixture)
        fixture = response()
        del fixture["answers"]["entry_gate"]["confidence"]
        fixtures.append(fixture)
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                client, _ = self.client(fixture)
                result = client.evaluate(STATE)
                self.assertEqual(result["status"], "invalid")
                self.assertIsNone(result["choice"])
                self.assertIsNone(result["probabilities"])
                self.assert_result_shape(result)

    def test_response_dict_size_is_bounded(self):
        fixture = response()
        fixture["extra"] = "x" * (jev.MAX_RESPONSE_BYTES + 1)
        client, _ = self.client(fixture)
        result = client.evaluate(STATE)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reason"], "response_too_large")

    def test_provider_failures_are_sanitized(self):
        cases = [
            (TimeoutError("unit-test-secret"), "request_timeout"),
            (URLError(TimeoutError("unit-test-secret")), "request_timeout"),
            (URLError("unit-test-secret"), "transport_unavailable"),
            (RuntimeError("unit-test-secret"), "transport_unavailable"),
            (HTTPError(jev.JEV_ENDPOINT, 401, "unit-test-secret", {}, None), "authentication_failed"),
            (HTTPError(jev.JEV_ENDPOINT, 403, "unit-test-secret", {}, None), "authentication_failed"),
            (HTTPError(jev.JEV_ENDPOINT, 429, "unit-test-secret", {}, None), "rate_limited"),
            (HTTPError(jev.JEV_ENDPOINT, 503, "unit-test-secret", {}, None), "provider_http_error"),
            (HTTPError(jev.JEV_ENDPOINT, 302, "unit-test-secret", {}, None), "redirect_blocked"),
        ]
        for error, reason in cases:
            with self.subTest(reason=reason):
                transport = Mock(side_effect=error)
                result = jev.JevClient(api_key="unit-test-secret", transport=transport).evaluate(STATE)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["reason"], reason)
                self.assert_result_shape(result)

    def test_late_response_is_discarded(self):
        client, _ = self.client()
        with patch.object(jev.time, "monotonic", side_effect=[10.0, 13.0, 13.0]):
            result = client.evaluate(STATE)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "request_timeout")


class JevHTTPTransportTests(unittest.TestCase):
    def evaluate_http(self, http_response):
        opener = Mock()
        opener.open.return_value = http_response
        with patch.object(jev.urllib_request, "build_opener", return_value=opener) as builder:
            result = jev.JevClient(api_key="unit-test-secret").evaluate(STATE)
        return result, opener, builder

    def test_default_transport_posts_only_to_fixed_https_endpoint(self):
        raw = json.dumps(response()).encode()
        result, opener, builder = self.evaluate_http(FakeHTTPResponse(raw))
        self.assertEqual(result["status"], "ok")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, jev.JEV_ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 2.0)
        handlers = builder.call_args.args
        self.assertTrue(any(isinstance(h, jev.urllib_request.ProxyHandler) and h.proxies == {} for h in handlers))
        redirect_handler = next(h for h in handlers if isinstance(h, jev.urllib_request.HTTPRedirectHandler))
        self.assertIsNone(redirect_handler.redirect_request(request, None, 307, "redirect", {}, "https://other.invalid"))

    def test_raw_json_validation(self):
        valid = json.dumps(response()).encode()
        cases = [b"not-json", b"\xff", b"[]", b"{}", b'{"model":"x","model":"y"}',
                 valid.replace(b'"confidence": 0.8', b'"confidence": NaN')]
        for raw in cases:
            with self.subTest(raw_length=len(raw)):
                result, _, _ = self.evaluate_http(FakeHTTPResponse(raw))
                self.assertEqual(result["status"], "invalid")

    def test_response_headers_and_body_limit(self):
        valid = json.dumps(response()).encode()
        for headers in [
            {"Content-Type": "text/html"},
            {"Content-Type": "application/json", "Content-Encoding": "gzip"},
            {"Content-Type": "application/json", "Content-Length": "invalid"},
            {"Content-Type": "application/json", "Content-Length": "-1"},
            {"Content-Type": "application/json", "Content-Length": str(jev.MAX_RESPONSE_BYTES + 1)},
        ]:
            with self.subTest(headers=headers):
                fake = FakeHTTPResponse(valid, headers=headers)
                result, _, _ = self.evaluate_http(fake)
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(fake.read_sizes, [])
        fake = FakeHTTPResponse(b"x" * (jev.MAX_RESPONSE_BYTES + 1), headers={"Content-Type": "application/json"})
        result, _, _ = self.evaluate_http(fake)
        self.assertEqual(result["reason"], "response_too_large")
        self.assertEqual(fake.read_sizes, [jev.MAX_RESPONSE_BYTES + 1])

    def test_unexpected_effective_url_or_status_is_rejected(self):
        for kwargs in [{"url": "https://other.invalid"}, {"status": 204}]:
            with self.subTest(kwargs=kwargs):
                fake = FakeHTTPResponse(json.dumps(response()).encode(), **kwargs)
                result, _, _ = self.evaluate_http(fake)
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(fake.read_sizes, [])


if __name__ == "__main__":
    unittest.main()
