"""Same-origin owner access and bounded JSON at the HTTP boundary."""
import base64
import hashlib
import hmac
import ipaddress
import json
import math
import re
import secrets
import time
from urllib.parse import urlsplit

from starlette.responses import JSONResponse


MAX_BODY = 2 * 1024 * 1024
COOKIE_NAME = "torinteo_owner"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def is_loopback(value):
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON keys are not allowed.")
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError("JSON numbers must be finite.")

    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Request must be a bounded UTF-8 JSON object.") from exc
    if not isinstance(data, dict):
        raise ValueError("Request must be a JSON object.")
    pending = [(data, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 40 or nodes > 50000:
            raise ValueError("JSON structure exceeds the supported size.")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("JSON numbers must be finite.")
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    return data


def normalized_host(value):
    if not isinstance(value, str) or len(value) > 255 or any(char in value for char in "\\/@?# \t\r\n"):
        raise ValueError("Invalid Host header.")
    try:
        parsed = urlsplit("//" + value)
        host, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise ValueError("Invalid Host header.") from exc
    if not host or parsed.username or parsed.password or parsed.path or port == 0:
        raise ValueError("Invalid Host header.")
    if host != "::1" and not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", host):
        try:
            ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("Invalid Host header.") from exc
    return host.lower()


class OwnerAccess:
    def __init__(self, token=None, *, clock=time.time):
        self.clock = clock
        self.required = token is not None
        if token is not None and (not isinstance(token, str) or not 32 <= len(token) <= 512 or any(c.isspace() for c in token)):
            raise ValueError("TORINTEO_WEB_TOKEN must be 32 to 512 non-whitespace characters.")
        self._token = token
        self._key = hashlib.sha256(("torinteo-owner-session-v1:" + token).encode()).digest() if token else None
        self._failures = {}

    def login(self, candidate, address):
        now = self.clock()
        history = [item for item in self._failures.get(address, []) if now - item < 60]
        if len(history) >= 10:
            return None, 429
        valid = isinstance(candidate, str) and len(candidate) <= 512 and self._token is not None
        valid = valid and hmac.compare_digest(candidate.encode(), self._token.encode())
        if not valid:
            if len(self._failures) > 2048:
                self._failures.clear()
            self._failures[address] = history + [now]
            return None, 401
        self._failures.pop(address, None)
        payload = str(int(now) + 8 * 3600) + ":" + secrets.token_hex(16)
        encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        mac = hmac.new(self._key, encoded.encode(), hashlib.sha256).hexdigest()
        return encoded + "." + mac, 200

    def authorized(self, cookie):
        if not self.required:
            return True
        if not isinstance(cookie, str) or len(cookie) > 256:
            return False
        try:
            payload, supplied = cookie.split(".", 1)
            expected = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(supplied, expected):
                return False
            decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
            expiry, nonce = decoded.split(":", 1)
            return bool(re.fullmatch(r"[a-f0-9]{32}", nonce)) and self.clock() < int(expiry) <= self.clock() + 8 * 3600
        except (ValueError, UnicodeError, OverflowError):
            return False


class BoundaryMiddleware:
    def __init__(self, app, *, allowed_hosts, allowed_origins=()):
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.allowed_origins = set(allowed_origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def reject(status, message):
            return await JSONResponse({"error": message, "mode": "paper"}, status_code=status)(scope, receive, send)

        headers = {}
        for key, value in scope["headers"]:
            key = key.decode("latin-1").lower()
            headers.setdefault(key, []).append(value.decode("latin-1"))
        try:
            if len(headers.get("host", [])) != 1:
                raise ValueError("Exactly one Host header is required.")
            raw_host = headers["host"][0]
            if normalized_host(raw_host) not in self.allowed_hosts:
                raise ValueError("Host is not allowed.")
            origins = headers.get("origin", [])
            if len(origins) > 1:
                raise ValueError("Cross-origin requests are not allowed.")
            if origins:
                expected = scope.get("scheme", "http") + "://" + raw_host
                if origins[0].lower() != expected.lower() and origins[0].lower() not in self.allowed_origins:
                    raise ValueError("Cross-origin requests are not allowed.")
            if headers.get("sec-fetch-site", [""])[0] == "cross-site" and (not origins or origins[0].lower() not in self.allowed_origins):
                raise ValueError("Cross-origin requests are not allowed.")
        except ValueError as exc:
            return await reject(403, str(exc))

        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            content_types = headers.get("content-type", [])
            if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
                return await reject(415, "Mutations require application/json.")
            lengths = headers.get("content-length", [])
            if len(lengths) > 1 or (lengths and (not lengths[0].isdigit() or len(lengths[0]) > 12)):
                return await reject(400, "Invalid request length.")
            if lengths and int(lengths[0]) > MAX_BODY:
                return await reject(413, "Request exceeds 2 MiB.")
            chunks, size = [], 0
            while True:
                item = await receive()
                if item["type"] == "http.disconnect":
                    return
                chunk = item.get("body", b"")
                size += len(chunk)
                if size > MAX_BODY:
                    return await reject(413, "Request exceeds 2 MiB.")
                chunks.append(chunk)
                if not item.get("more_body", False):
                    break
            raw = b"".join(chunks)
            try:
                data = strict_json(raw)
            except ValueError as exc:
                return await reject(400, str(exc))
            scope.setdefault("state", {})["json_body"] = data
            consumed = False

            async def replay():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": raw, "more_body": False}
                return await receive()

            receiver = replay
        else:
            receiver = receive

        async def response_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"),
                ])
                if scope["path"].startswith("/api/"):
                    message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        await self.app(scope, receiver, response_send)
