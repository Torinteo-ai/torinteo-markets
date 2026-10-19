"""Optional TypeSafe Jev evidence review; no strategy, broker, or fallback model.

The caller decides whether an assessment is shadow-only or required. An unavailable
or invalid assessment never contains a usable choice. Only aggregate, deterministic
evidence belongs in ``state``. The HTTP transport sends one bounded request without
retries or redirects; it never logs credentials or raw provider errors.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections.abc import Callable
from urllib import error as urllib_error
from urllib import request as urllib_request


JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
SUPPORTED_MODELS = frozenset({"jev-1.13.0", "jev-1.14.0"})
MAX_INPUT_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_STATE_DEPTH = 16
MAX_STATE_NODES = 4096
PROBABILITY_SUM_TOLERANCE = 0.001

Transport = Callable[[str, dict[str, str], bytes, float], dict]

_ENTRY_GATE_QUESTION = {
    "type": "choice",
    "instructions": (
        "Review only the supplied deterministic evidence for this proposed paper entry. "
        "Use the supplied check results; do not invent market facts, prices, dates, "
        "confirmations, calculations, or profitable outcomes. Strategy selection, "
        "arithmetic, freshness checks and hard risk limits belong to code and must "
        "not be overridden. Reject incomplete, contradictory, or uncertain evidence."
    ),
    "criteria": {
        "pass": (
            "The supplied evidence explicitly supports the candidate, every required "
            "deterministic strategy and risk check is present and passed, and no "
            "material contradiction or uncertainty remains."
        ),
        "reject": (
            "Any required evidence or check is missing, failed, stale, contradictory, "
            "unverified, or unclear; or the entry conflicts with the supplied "
            "strategy or risk constraints."
        ),
    },
}

_CREDENTIAL_KEYS = frozenset({
    "authorization", "authorizationheader", "apikey", "typesafeapikey",
    "typesafeaiapikey", "token", "accesstoken", "refreshtoken", "clientsecret",
    "secret", "password", "privatekey", "seedphrase", "credentials",
    "brokercredentials", "sessiontoken", "jwt",
})


class _InvalidData(ValueError):
    """Contains only an adapter-owned reason code, never provider content."""


class _RejectRedirects(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidData("invalid_json_response")
        result[key] = value
    return result


def _reject_json_constant(_value):
    raise _InvalidData("invalid_json_response")


def _http_transport(url: str, headers: dict[str, str], payload_bytes: bytes,
                    timeout_seconds: float) -> dict:
    # This is intentionally not a configurable provider URL. An API key cannot be
    # redirected to another origin or routed through environment-configured proxies.
    if url != JEV_ENDPOINT:
        raise _InvalidData("invalid_endpoint")
    opener = urllib_request.build_opener(
        urllib_request.ProxyHandler({}), _RejectRedirects(),
    )
    request = urllib_request.Request(url, data=payload_bytes, headers=headers, method="POST")
    with opener.open(request, timeout=timeout_seconds) as response:
        if response.geturl() != JEV_ENDPOINT:
            raise _InvalidData("unexpected_response_url")
        if response.status != 200:
            raise _InvalidData("unexpected_response_status")
        media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
        if media_type != "application/json" or encoding not in {"", "identity"}:
            raise _InvalidData("invalid_response_headers")
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            if not content_length.isascii() or not content_length.isdigit() or len(content_length) > 10:
                raise _InvalidData("invalid_response_headers")
            if int(content_length) > MAX_RESPONSE_BYTES:
                raise _InvalidData("response_too_large")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _InvalidData("response_too_large")
    try:
        return json.loads(
            raw.decode("utf-8"), parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise _InvalidData("invalid_json_response") from None


def _validate_state(state: dict) -> None:
    if type(state) is not dict:
        raise _InvalidData("invalid_state")
    nodes = 0

    def visit(value, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > MAX_STATE_NODES or depth > MAX_STATE_DEPTH:
            raise _InvalidData("invalid_state")
        if value is None or type(value) is bool:
            return
        if type(value) is str:
            if len(value) > MAX_INPUT_BYTES:
                raise _InvalidData("input_too_large")
            return
        if type(value) is int:
            if value.bit_length() > 1024:
                raise _InvalidData("invalid_state")
            return
        if type(value) is float:
            if not math.isfinite(value):
                raise _InvalidData("invalid_state")
            return
        if type(value) is dict:
            if len(value) > MAX_STATE_NODES:
                raise _InvalidData("invalid_state")
            for key, item in value.items():
                if type(key) is not str:
                    raise _InvalidData("invalid_state")
                if len(key) > MAX_INPUT_BYTES:
                    raise _InvalidData("input_too_large")
                normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
                if normalized in _CREDENTIAL_KEYS or normalized.endswith((
                    "apikey", "password", "privatekey", "accesstoken", "refreshtoken", "clientsecret",
                )):
                    raise _InvalidData("state_contains_credentials")
                visit(item, depth + 1)
            return
        if type(value) is list:
            if len(value) > MAX_STATE_NODES:
                raise _InvalidData("invalid_state")
            for item in value:
                visit(item, depth + 1)
            return
        raise _InvalidData("invalid_state")

    visit(state)


def _finite_unit_number(value) -> bool:
    # bool is a subclass of int, but it is not a probability or confidence value.
    return type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value)


def _validate_response(payload: dict, model: str) -> dict:
    if type(payload) is not dict or set(payload) != {"model", "answers", "usage"}:
        raise _InvalidData("invalid_response_schema")
    if type(payload["model"]) is not str or payload["model"] != model:
        raise _InvalidData("unexpected_model")
    answers = payload["answers"]
    if type(answers) is not dict or set(answers) != {"entry_gate"}:
        raise _InvalidData("invalid_answer_schema")
    answer = answers["entry_gate"]
    if type(answer) is not dict or set(answer) != {"type", "choice", "confidence", "probabilities"}:
        raise _InvalidData("invalid_answer_schema")
    if answer["type"] != "choice" or type(answer["choice"]) is not str:
        raise _InvalidData("invalid_choice")
    choice = answer["choice"]
    if choice not in {"pass", "reject"}:
        raise _InvalidData("invalid_choice")
    probabilities = answer["probabilities"]
    if type(probabilities) is not dict or set(probabilities) != {"pass", "reject"}:
        raise _InvalidData("invalid_probabilities")
    if not all(_finite_unit_number(value) for value in probabilities.values()):
        raise _InvalidData("invalid_probabilities")
    if abs(sum(probabilities.values()) - 1.0) > PROBABILITY_SUM_TOLERANCE:
        raise _InvalidData("invalid_probabilities")
    if probabilities[choice] != max(probabilities.values()):
        raise _InvalidData("invalid_choice")
    if not _finite_unit_number(answer["confidence"]):
        raise _InvalidData("invalid_confidence")
    usage = payload["usage"]
    if type(usage) is not dict or set(usage) != {"input_tokens", "output_tokens"}:
        raise _InvalidData("invalid_token_usage")
    if not all(type(value) is int and 0 <= value <= 1_000_000_000 for value in usage.values()):
        raise _InvalidData("invalid_token_usage")
    return answer


class JevClient:
    """Review a candidate through one TypeSafe Choice question.

    ``transport`` is an optional trusted test seam with the signature
    ``(url, headers, payload_bytes, timeout_seconds) -> dict``. A supplied empty key
    disables access even if an environment key exists. No network request occurs
    at construction, on missing credentials, or for invalid input.
    """

    def __init__(self, api_key: str | None = None, model: str = "jev-1.13.0",
                 timeout_seconds: float = 2.0, transport: Transport | None = None):
        self._api_key = os.environ.get("TYPESAFE_API_KEY") if api_key is None else api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._transport = _http_transport if transport is None else transport

    def evaluate(self, state: dict) -> dict:
        started = time.monotonic()
        known_model = self.model if type(self.model) is str and self.model in SUPPORTED_MODELS else None

        def result(status, reason, answer=None):
            return {
                "status": status,
                "provider": "jev",
                "model": known_model,
                "choice": answer["choice"] if answer is not None else None,
                "confidence": float(answer["confidence"]) if answer is not None else None,
                "probabilities": {key: float(value) for key, value in answer["probabilities"].items()}
                if answer is not None else None,
                "reason": reason,
                "latency_ms": max(0.0, (time.monotonic() - started) * 1000),
            }

        if known_model is None:
            return result("invalid", "invalid_model_configuration")
        if type(self.timeout_seconds) not in (int, float) or not 0 < self.timeout_seconds <= 30:
            return result("invalid", "invalid_timeout_configuration")
        if not math.isfinite(self.timeout_seconds):
            return result("invalid", "invalid_timeout_configuration")
        if not callable(self._transport):
            return result("invalid", "invalid_transport_configuration")
        key = self._api_key
        if key is None or (type(key) is str and not key.strip()):
            return result("unavailable", "api_key_not_configured")
        if type(key) is not str or len(key) > 4096 or any(not 33 <= ord(char) <= 126 for char in key):
            return result("invalid", "invalid_api_key")
        try:
            _validate_state(state)
            body = json.dumps({
                "model": known_model, "state": state,
                "questions": {"entry_gate": _ENTRY_GATE_QUESTION},
            }, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if key.encode("ascii") in body:
                raise _InvalidData("state_contains_credentials")
            if len(body) > MAX_INPUT_BYTES:
                raise _InvalidData("input_too_large")
        except _InvalidData as exc:
            return result("invalid", str(exc))
        except (TypeError, ValueError, UnicodeError, RecursionError):
            return result("invalid", "invalid_state")

        try:
            payload = self._transport(JEV_ENDPOINT, {
                "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                "Accept": "application/json", "Accept-Encoding": "identity",
            }, body, float(self.timeout_seconds))
        except _InvalidData as exc:
            return result("invalid", str(exc))
        except urllib_error.HTTPError as exc:
            if exc.code in {401, 403}:
                reason = "authentication_failed"
            elif exc.code == 429:
                reason = "rate_limited"
            elif 300 <= exc.code < 400:
                reason = "redirect_blocked"
            else:
                reason = "provider_http_error"
            exc.close()
            return result("unavailable", reason)
        except TimeoutError:
            return result("unavailable", "request_timeout")
        except urllib_error.URLError as exc:
            reason = "request_timeout" if isinstance(exc.reason, TimeoutError) else "transport_unavailable"
            return result("unavailable", reason)
        except Exception:
            # Transport errors can contain credentials or remote response bodies.
            return result("unavailable", "transport_unavailable")

        if time.monotonic() - started > self.timeout_seconds:
            return result("unavailable", "request_timeout")
        try:
            # The injected transport must respect the same finite JSON and size
            # constraints as the real HTTP response.
            encoded = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_RESPONSE_BYTES:
                raise _InvalidData("response_too_large")
            answer = _validate_response(payload, known_model)
        except _InvalidData as exc:
            return result("invalid", str(exc))
        except (TypeError, ValueError, UnicodeError, RecursionError):
            return result("invalid", "invalid_response_schema")
        return result("ok", "assessment_received", answer)
