"""Exercise the built owner container and durable volume; Docker is required.

This is a CI smoke check against invented paper data. It makes no market-data
or trading-account calls and never prints the temporary owner credential.
"""
from http.cookiejar import CookieJar
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


IMAGE = "torinteo-markets:check"
HEALTH_TIMEOUT = 35


class SmokeFailure(RuntimeError):
    """A deliberately credential-free diagnostic for the CI log."""


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def docker(arguments, operation, *, environment=None, timeout=30):
    try:
        result = subprocess.run(
            ["docker", *arguments], env=environment, capture_output=True,
            text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeFailure(f"{operation} timed out.") from exc
    except OSError as exc:
        raise SmokeFailure(f"{operation} could not start.") from exc
    if result.returncode:
        # Docker errors can echo command arguments or environment diagnostics.
        # Keep them captured; print only the operation and exit status.
        raise SmokeFailure(f"{operation} failed with exit status {result.returncode}.")
    return result.stdout.strip()


def request_json(opener, base_url, path, *, body=None, expected=200):
    data = None if body is None else json.dumps(body, allow_nan=False).encode()
    request = urllib.request.Request(
        base_url + path, data=data,
        headers={} if data is None else {"Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    try:
        with opener.open(request, timeout=3) as response:
            if response.status != expected:
                raise SmokeFailure("The owner API returned an unexpected HTTP status.")
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise SmokeFailure("The owner API response exceeded the smoke-test size bound.")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise SmokeFailure("The owner API returned an invalid JSON response.")
            return payload, response.headers
    except urllib.error.HTTPError as exc:
        raise SmokeFailure(f"The owner API returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SmokeFailure("The local owner API could not be reached.") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SmokeFailure("The owner API returned invalid JSON.") from exc


def healthy(opener, base_url):
    deadline = time.monotonic() + HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        try:
            payload, _ = request_json(opener, base_url, "/api/health")
            if (payload.get("status") == "ok" and payload.get("mode") == "paper"
                    and payload.get("storage") == "local_sqlite"
                    and payload.get("requires_login") is True
                    and payload.get("live_execution_available") is False):
                return
        except SmokeFailure:
            pass
        time.sleep(0.25)
    raise SmokeFailure("The authenticated paper container did not become healthy within 35 seconds.")


def expect(condition, message):
    if not condition:
        raise SmokeFailure(message)


def loopback_url(container):
    binding = docker(["port", container, "8787/tcp"], "Loopback port inspection")
    match = re.fullmatch(r"127\.0\.0\.1:(\d{1,5})", binding)
    expect(match is not None and 1 <= int(match[1]) <= 65535,
           "The smoke container was not published exclusively on loopback.")
    return "http://" + binding


def check_persistence(opener, base_url, owner_token, container):
    session, _ = request_json(opener, base_url, "/api/session")
    expect(session.get("requires_login") is True and session.get("authenticated") is False,
           "The fresh container did not require owner authentication.")
    session, headers = request_json(opener, base_url, "/api/login", body={"token": owner_token})
    expect(session.get("authenticated") is True, "The generated owner token was not accepted.")
    cookie = headers.get("Set-Cookie", "")
    expect("httponly" in cookie.lower() and "samesite=strict" in cookie.lower(),
           "Owner login did not issue the expected protected cookie.")
    expect(owner_token not in cookie and owner_token not in json.dumps(session),
           "Owner login exposed the temporary token.")

    created, _ = request_json(opener, base_url, "/api/experiments",
                              body={"name": "Container persistence smoke", "kind": "synthetic"}, expected=201)
    identifier = created.get("experiment", {}).get("id")
    expect(isinstance(identifier, str) and re.fullmatch(r"[0-9a-f-]{36}", identifier),
           "Experiment creation did not return an opaque identifier.")
    expect(created.get("orders") == [] and created.get("decisions") == [],
           "Experiment creation unexpectedly placed a paper order.")
    evaluated, _ = request_json(opener, base_url, f"/api/experiments/{identifier}/demo", body={"step": "evaluate"})
    orders, decisions = evaluated.get("orders", []), evaluated.get("decisions", [])
    expect(len(orders) == 1 and orders[0].get("status") == "PENDING",
           "The synthetic setup did not create exactly one pending paper order.")
    expect(len(decisions) == 1 and decisions[0].get("call") == "LONG",
           "The synthetic setup did not persist exactly one LONG decision.")
    before = {
        "experiment": evaluated["experiment"], "orders": orders,
        "decisions": decisions, "events": evaluated["events"], "account": evaluated["account"],
    }

    docker(["restart", "--time", "5", container], "Container restart")
    # Docker may assign a different dynamic host port when restarting. Cookies
    # remain scoped to the same loopback host; discover the actual binding.
    base_url = loopback_url(container)
    healthy(opener, base_url)
    restored, _ = request_json(opener, base_url, f"/api/experiments/{identifier}")
    expect(all(restored.get(key) == value for key, value in before.items()),
           "The experiment, pending order, decision or account changed across container restart.")
    expect(restored.get("demo", {}).get("next_step") == "fill",
           "The restarted container lost the pending demonstration state.")


def main():
    if shutil.which("docker") is None:
        print("Docker is required for the container smoke check.", file=sys.stderr)
        return 2
    suffix = uuid.uuid4().hex[:16]
    container, volume = f"torinteo-smoke-{suffix}", f"torinteo-smoke-data-{suffix}"
    owner_token = secrets.token_urlsafe(48)
    environment = os.environ.copy()
    environment["TORINTEO_WEB_TOKEN"] = owner_token
    volume_attempted, container_attempted = False, False
    failure = None
    try:
        volume_attempted = True
        docker(["volume", "create", volume], "Disposable volume creation")
        container_attempted = True
        docker([
            "run", "--detach", "--pull=never", "--name", container,
            "--read-only", "--tmpfs", "/tmp:size=32m,mode=1777",
            "--security-opt", "no-new-privileges:true", "--cap-drop", "ALL",
            "--env", "TORINTEO_WEB_TOKEN", "--mount", f"type=volume,src={volume},dst=/data",
            "--publish", "127.0.0.1::8787", IMAGE,
        ], "Owner container launch", environment=environment)
        base_url = loopback_url(container)
        # Never route a local token-bearing request through ambient proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectRedirects(),
                                            urllib.request.HTTPCookieProcessor(CookieJar()))
        healthy(opener, base_url)
        check_persistence(opener, base_url, owner_token, container)
    except SmokeFailure as exc:
        failure = str(exc)
    except Exception as exc:
        # Do not let a traceback print subprocess arguments or response bodies.
        failure = f"Container smoke check encountered {type(exc).__name__}."
    finally:
        if container_attempted:
            try:
                docker(["rm", "--force", container], "Disposable container cleanup", timeout=20)
            except SmokeFailure:
                failure = failure or "Disposable container cleanup failed."
        if volume_attempted:
            try:
                docker(["volume", "rm", "--force", volume], "Disposable volume cleanup", timeout=20)
            except SmokeFailure:
                failure = failure or "Disposable volume cleanup failed."
        environment.pop("TORINTEO_WEB_TOKEN", None)
    if failure:
        print(failure, file=sys.stderr)
        return 1
    print(json.dumps({"status": "passed", "authenticated": True, "restarted": True,
                      "persisted_order": "PENDING", "decisions": 1, "storage": "disposable_named_volume"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
