"""Owner HTTP API that runs the existing paper engine against local ledgers."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import __version__
from ..demo_data import DEMO_TIME, demo_execution_bar, demo_snapshot
from ..engine import PaperEngine
from ..models import iso, utc, validate_bar, validate_snapshot
from .security import BoundaryMiddleware, COOKIE_NAME, LOCAL_HOSTS, OwnerAccess, is_loopback, normalized_host
from .store import ExperimentNotFound, ExperimentStore


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def fields(data, required, optional=()):
    if not isinstance(data, dict) or not set(required) <= data.keys() or set(data) - set(required) - set(optional):
        raise APIError("Request fields do not match this action.")
    return data


def present(package):
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        return False


def research_settings():
    values = {key: os.environ.get(env, "").strip() for key, env in (
        ("llm_provider", "TORINTEO_RESEARCH_PROVIDER"),
        ("quick_think_llm", "TORINTEO_RESEARCH_QUICK_MODEL"),
        ("deep_think_llm", "TORINTEO_RESEARCH_DEEP_MODEL"),
    )}
    return values if all(value and len(value) <= 120 for value in values.values()) else None


def integration_status():
    packages = {name: present(name) for name in ("openbb", "tradingagents", "freqtrade")}
    key_present = bool(os.environ.get("TYPESAFE_API_KEY", "").strip())
    research_configured = research_settings() is not None
    return {
        "mode": "paper", "live_execution_available": False, "runs_continuously": False,
        "optional_packages_present": packages, "typesafe_key_present": key_present,
        "research_models_configured": research_configured,
        "providers": [
            {"id": "public_market", "name": "Coinbase public data", "status": "available",
             "role": "Public crypto candles and quotes", "credentials_required": False,
             "connection_verified": False, "message": "Connection is checked when a chart is requested."},
            {"id": "native", "name": "Torinteo paper engine", "status": "ready",
             "role": "Paper decisions, simulated fills and audit history", "live_execution": False},
            {"id": "openbb", "name": "OpenBB", "status": "installed" if packages["openbb"] else "not_installed",
             "role": "Optional additional market-data adapter", "connection_verified": False},
            {"id": "tradingagents", "name": "TradingAgents",
             "status": "configured" if packages["tradingagents"] and research_configured else "not_configured",
             "role": "Optional research assessment", "installed": packages["tradingagents"],
             "models_configured": research_configured, "connection_verified": False},
            {"id": "jev", "name": "Jev", "status": "configured" if key_present else "not_configured",
             "role": "Optional typed entry assessment", "key_present": key_present, "connection_verified": False},
            {"id": "freqtrade", "name": "Freqtrade", "status": "host_strategy_required",
             "role": "Prepared dry-run confirmation component", "installed": packages["freqtrade"],
             "execution_available": False},
            {"id": "quantdinger", "name": "QuantDinger", "status": "not_integrated",
             "role": "Alternative host reviewed in the project documentation"},
        ],
    }


def create_app(data_dir, static_dir=None, *, host="127.0.0.1", token=None, allowed_hosts=None, allowed_origins=None,
               clock=None, market_client=None):
    """Create a local/persistent-volume app; no live trading or cloud DB fallback."""
    if os.environ.get("VERCEL") == "1":
        raise ValueError("The SQLite owner service requires persistent storage and cannot run in Vercel Functions.")
    clock = clock or (lambda: datetime.now(timezone.utc))
    if token is None:
        token = os.environ.get("TORINTEO_WEB_TOKEN") or None
    local = is_loopback(host)
    if not local and not token:
        raise ValueError("A network bind requires TORINTEO_WEB_TOKEN.")
    access = OwnerAccess(token)
    hosts = set(LOCAL_HOSTS)
    if host not in {"0.0.0.0", "::"}:
        hosts.add(host.lower())
    for value in allowed_hosts or []:
        # Host allowlists are literal hostnames/IPs, with no wildcard or URL.
        normalized = normalized_host("[" + value + "]" if ":" in value and not value.startswith("[") else value)
        hosts.add(normalized)
    origins = set()
    for value in allowed_origins or []:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("Allowed origins must be complete http(s) origins without a path.")
        origin_host = normalized_host(parsed.netloc)
        if not access.required and not is_loopback(origin_host):
            raise ValueError("Non-loopback origins require owner authentication.")
        origins.add(value.lower())
    store = ExperimentStore(data_dir, clock=clock)
    static = Path(static_dir).resolve() if static_dir else None
    app = FastAPI(title="Torinteo Markets", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.access = store, access
    app.state.clock, app.state.market_client = clock, market_client

    @app.exception_handler(APIError)
    async def api_error(_request, exc):
        return JSONResponse({"error": exc.message, "mode": "paper"}, status_code=exc.status)

    @app.exception_handler(ExperimentNotFound)
    async def missing(_request, _exc):
        return JSONResponse({"error": "Experiment not found.", "mode": "paper"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_request, exc):
        return JSONResponse({"error": str(exc), "mode": "paper"}, status_code=400)

    @app.exception_handler(KeyError)
    @app.exception_handler(TypeError)
    async def malformed(_request, _exc):
        return JSONResponse({"error": "Input is missing a required field or has an invalid type.", "mode": "paper"}, status_code=400)

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    async def storage_error(_request, _exc):
        return JSONResponse({"error": "Paper storage is unavailable; the action was not confirmed.", "mode": "paper"}, status_code=503)

    @app.middleware("http")
    async def owner_only(request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in {"/api/health", "/api/session", "/api/login"}:
            if not access.authorized(request.cookies.get(COOKIE_NAME)):
                return JSONResponse({"error": "Owner sign-in required.", "mode": "paper"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "Torinteo Markets", "version": __version__, "mode": "paper",
                "live_execution_available": False, "storage": "local_sqlite",
                "requires_login": access.required, "runs_continuously": False}

    @app.get("/api/session")
    def session(request: Request):
        return {"authenticated": access.authorized(request.cookies.get(COOKIE_NAME)),
                "requires_login": access.required, "mode": "paper"}

    @app.post("/api/login")
    def login(request: Request):
        data = fields(request.state.json_body, {"token"})
        if not access.required:
            return {"authenticated": True, "requires_login": False, "mode": "paper"}
        address = request.client.host if request.client else "unknown"
        cookie, status = access.login(data["token"], address)
        if not cookie:
            raise APIError("Please wait before trying again." if status == 429 else "Owner token was not accepted.", status)
        response = JSONResponse({"authenticated": True, "requires_login": True, "mode": "paper"})
        response.set_cookie(COOKIE_NAME, cookie, max_age=8 * 3600, httponly=True,
                            secure=request.url.scheme == "https", samesite="strict", path="/")
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        fields(request.state.json_body, set())
        response = JSONResponse({"authenticated": not access.required, "requires_login": access.required, "mode": "paper"})
        response.delete_cookie(COOKIE_NAME, httponly=True, secure=request.url.scheme == "https", samesite="strict", path="/")
        return response

    @app.get("/api/integrations")
    def integrations():
        return integration_status()

    @app.get("/api/experiments")
    def experiments():
        return {"experiments": store.list(), "mode": "paper"}

    @app.post("/api/experiments", status_code=201)
    def create(request: Request):
        data = fields(request.state.json_body, {"name", "kind"}, {"config"})
        experiment = store.create(data["name"], data["kind"], data.get("config"))
        return store.detail(experiment["id"])

    @app.get("/api/experiments/{identifier}")
    def detail(identifier: str):
        with store.locked(identifier):
            return store.detail(identifier)

    @app.get("/api/experiments/{identifier}/export")
    def export(identifier: str):
        with store.locked(identifier):
            result = store.detail(identifier)
        result["export"] = {"schema_version": 1, "created_at": iso(clock()), "kind": "paper_audit"}
        return JSONResponse(result, headers={"Content-Disposition": f'attachment; filename="torinteo-paper-{identifier}.json"'})

    def engine_for(identifier, *, online):
        ledger = store.ledger(identifier)
        jev = research = None
        if online and ledger.config.jev_mode != "disabled":
            from ..adapters.jev import JevClient
            jev = JevClient()
        if online and ledger.config.research_mode != "disabled":
            from ..adapters.tradingagents import TradingAgentsAdapter
            settings = research_settings()
            if settings:
                research = TradingAgentsAdapter(config=settings, clock=clock)
        return PaperEngine(ledger, jev=jev, research=research, clock=clock)

    @app.post("/api/experiments/{identifier}/evaluate")
    def evaluate(identifier: str, request: Request):
        data = fields(request.state.json_body, {"snapshot"})
        snapshot = data["snapshot"]
        validate_snapshot(snapshot)
        with store.locked(identifier):
            experiment = store.metadata(identifier)
            kind = snapshot["source"]["kind"]
            if kind != experiment["kind"]:
                raise APIError("Snapshot provenance does not match this experiment.", 409)
            if kind == "historical" and utc(snapshot["as_of"]) > utc(clock()):
                raise APIError("Historical snapshots cannot be dated in the future.")
            now = utc(clock()) if kind == "market" else utc(snapshot["as_of"])
            decision = engine_for(identifier, online=kind == "market").run(snapshot, now=now)
            store.save_snapshot(identifier, snapshot, custom=True)
            result = store.detail(identifier)
            result["decision"] = decision
            return result

    @app.post("/api/experiments/{identifier}/advance")
    def advance(identifier: str, request: Request):
        data = fields(request.state.json_body, {"schema_version", "symbol", "bar", "observed_at", "provenance", "timeframe"})
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise APIError("Unsupported execution bar envelope.")
        observed = utc(data["observed_at"])
        with store.locked(identifier):
            experiment = store.metadata(identifier)
            if data["provenance"] != experiment["kind"]:
                raise APIError("Execution provenance does not match this experiment.", 409)
            if experiment["kind"] in {"market", "historical"} and observed > utc(clock()):
                raise APIError("Market and historical observations cannot be dated in the future.")
            if experiment["kind"] == "market":
                observed = utc(clock())
            engine = engine_for(identifier, online=False)
            engine.advance(data["symbol"], data["bar"], observed_at=observed,
                           provenance=data["provenance"], timeframe=data["timeframe"])
            store.mark_custom(identifier)
            return store.detail(identifier)

    @app.post("/api/experiments/{identifier}/demo")
    def demo(identifier: str, request: Request):
        data = fields(request.state.json_body, {"step"})
        step = data["step"]
        if step not in {"prepare", "evaluate", "fill", "close", "blocked"}:
            raise APIError("Unknown demonstration step.")
        with store.locked(identifier):
            before = store.detail(identifier)
            if not before["demo"]["enabled"]:
                raise APIError("This action requires a fresh synthetic experiment with the standard paper settings.", 409)
            stage = before["demo"]["stage"]
            already = {"prepare": "prepared", "evaluate": "evaluated", "fill": "filled", "close": "closed", "blocked": "blocked"}
            if stage == already[step]:
                return before
            if before["demo"]["next_step"] != step:
                raise APIError("Complete the next demonstration step first.", 409)
            engine = engine_for(identifier, online=False)
            if step == "prepare":
                store.save_snapshot(identifier, demo_snapshot())
            elif step == "evaluate":
                engine.run(demo_snapshot(), now=DEMO_TIME)
            elif step == "fill":
                engine.advance("TEST/USD", demo_execution_bar(), observed_at=DEMO_TIME + timedelta(minutes=15), provenance="synthetic")
            elif step == "close":
                engine.advance("TEST/USD", demo_execution_bar(ambiguous=True), observed_at=DEMO_TIME + timedelta(minutes=30), provenance="synthetic")
            elif step == "blocked":
                snapshot = demo_snapshot(DEMO_TIME + timedelta(minutes=30), opposing_price=100.35)
                engine.run(snapshot, now=DEMO_TIME + timedelta(minutes=30))
                store.save_snapshot(identifier, snapshot)
            return store.detail(identifier)

    @app.post("/api/experiments/{identifier}/sync")
    def sync(identifier: str, request: Request):
        from ..adapters.public_market import PublicMarketUnavailable
        from .sync import sync_market_experiment
        fields(request.state.json_body, set())
        try:
            return sync_market_experiment(store, identifier, market_client, clock)
        except PublicMarketUnavailable as exc:
            raise APIError("Public execution candles are unavailable. No fallback prices are used; retry to resume any recorded prefix.", 503) from exc

    @app.get("/api/market")
    def market(symbol: str = "BTC/USD", limit: int = 100, timeframe: str = "15m"):
        from ..adapters.public_market import PublicMarketClient, PublicMarketError, PublicMarketUnavailable
        if not 5 <= limit <= 300:
            raise APIError("Chart limit must be between 5 and 300 candles.")
        try:
            client = market_client or PublicMarketClient()
            return client.collect(symbol=symbol, timeframe=timeframe, limit=limit)
        except PublicMarketUnavailable as exc:
            raise APIError("Public market data is unavailable. Try again when the provider is reachable.", 503) from exc
        except PublicMarketError as exc:
            raise APIError("The market request or provider response could not be validated.", 400) from exc

    @app.get("/{path:path}")
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise APIError("API route not found.", 404)
        parts = PurePosixPath(path).parts
        if "\\" in path or "\x00" in path or any(part in {".", ".."} or part.startswith(".") for part in parts):
            raise APIError("File not found.", 404)
        if static is None or not static.is_dir():
            raise APIError("Web assets are not built. Build web/dist and restart with --static-dir web/dist.", 503)
        candidate = (static / path).resolve()
        if not candidate.is_relative_to(static):
            raise APIError("File not found.", 404)
        if candidate.is_file():
            return FileResponse(candidate)
        if Path(path).suffix:
            raise APIError("File not found.", 404)
        index = static / "index.html"
        if not index.is_file() or not index.resolve().is_relative_to(static):
            raise APIError("Web entry file is not available.", 503)
        return FileResponse(index)

    app.add_middleware(BoundaryMiddleware, allowed_hosts=hosts, allowed_origins=origins)
    return app
