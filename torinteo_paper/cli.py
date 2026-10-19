"""Explicit, one-shot paper experiments; no daemon or live exchange client."""
import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys

from . import __version__
from .demo_data import DEMO_TIME, demo_execution_bar, demo_snapshot
from .engine import PaperEngine
from .inputs import assemble_snapshot, read_json, write_json
from .ledger import PaperLedger
from .models import RiskConfig, iso, utc


def current_time():
    return datetime.now(timezone.utc)


def stored_metadata(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("paper database does not exist")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return dict(connection.execute("SELECT key,value FROM metadata"))
    finally:
        connection.close()


def load_config(args):
    if getattr(args, "config", None):
        data = read_json(args.config)
    elif Path(args.db).is_file():
        data = json.loads(stored_metadata(args.db)["config"])
    else:
        data = {}
    return RiskConfig(**data)


def open_engine(args, *, online=False):
    config = load_config(args)
    jev = research = None
    if online and config.jev_mode != "disabled":
        from .adapters.jev import JevClient
        jev = JevClient()
    if online and config.research_mode != "disabled":
        from .adapters.tradingagents import TradingAgentsAdapter
        research_config = read_json(args.research_config) if args.research_config else None
        research = TradingAgentsAdapter(config=research_config)
    return PaperEngine(PaperLedger(args.db, config), jev=jev, research=research)


def audit(ledger, *, at=None):
    metadata = stored_metadata(ledger.path)
    at = utc(at or metadata.get("event_clock") or current_time())
    return {"mode": "paper", "as_of": iso(at), "executor": metadata.get("executor"),
            "provenance": metadata.get("provenance"), "account_currency": ledger.config.account_currency,
            "account": ledger.account(at), "orders": ledger.orders(), "events": ledger.events()}


def run_demo(path):
    if Path(path).exists():
        raise ValueError("demo requires a new database path; choose another --db")
    engine = PaperEngine(PaperLedger(path))
    approved = engine.run(demo_snapshot(), now=DEMO_TIME)
    engine.advance("TEST/USD", demo_execution_bar(), observed_at=DEMO_TIME + timedelta(minutes=15),
                   provenance="synthetic")
    # Restart from disk before the ambiguous candle. Both its stop and target
    # are crossed; conservative OHLC settlement chooses the stop first.
    engine = PaperEngine(PaperLedger(path))
    engine.advance("TEST/USD", demo_execution_bar(ambiguous=True),
                   observed_at=DEMO_TIME + timedelta(minutes=30), provenance="synthetic")
    blocked = engine.run(demo_snapshot(DEMO_TIME + timedelta(minutes=30), opposing_price=100.35),
                         now=DEMO_TIME + timedelta(minutes=30))
    result = audit(engine.ledger)
    result.update(demonstration="SYNTHETIC SOFTWARE DEMONSTRATION: invented TEST/USD prices and reviews; not a market backtest",
                  approved_call=approved["call"], blocked_call=blocked["call"],
                  blocked_reasons=blocked["reason_codes"], database=str(Path(path).resolve()))
    return result


def build_parser():
    parser = argparse.ArgumentParser(description="Torinteo Markets: explicit paper-trading experiments")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run an invented scenario using a fresh database")
    demo.add_argument("--db", default="runtime/demo.sqlite3")
    commands.add_parser("doctor", help="report local prerequisites without network requests")
    collect = commands.add_parser("collect", help="retrieve closed candles through an explicitly installed OpenBB")
    collect.add_argument("--symbol", required=True)
    collect.add_argument("--asset-class", choices=("crypto", "forex", "equity"), required=True)
    collect.add_argument("--timeframe", default="15m")
    collect.add_argument("--start-date", required=True)
    collect.add_argument("--end-date", required=True)
    collect.add_argument("--provider", choices=("yfinance",), default="yfinance")
    collect.add_argument("--timestamp-timezone", help="provider timestamp convention, only if independently verified")
    collect.add_argument("--out", required=True)
    assemble = commands.add_parser("assemble", help="join candles with explicit quote/review/calendar context")
    assemble.add_argument("--candles", required=True)
    assemble.add_argument("--context", required=True)
    assemble.add_argument("--limit", type=int, default=1000)
    assemble.add_argument("--out", required=True)
    for command in ("evaluate", "replay", "advance", "audit"):
        item = commands.add_parser(command)
        item.add_argument("--db", required=True)
        item.add_argument("--config", help="RiskConfig JSON; existing databases otherwise use their stored settings")
        if command in {"evaluate", "replay"}:
            item.add_argument("--snapshot", required=True)
        if command == "evaluate":
            item.add_argument("--research-config", help="explicit TradingAgents provider/model settings JSON")
        if command == "advance":
            item.add_argument("--bar", required=True, help="execution bar envelope JSON")
        if command == "audit":
            item.add_argument("--at", help="account cashflow cutoff; orders/events always show the full recorded history")
    return parser


def dispatch(args):
    if args.command == "doctor":
        packages = {name: importlib.util.find_spec(name) is not None for name in ("openbb", "tradingagents", "freqtrade")}
        return {"mode": "paper", "version": __version__, "python": sys.version.split()[0],
                "optional_packages_present": packages,
                "typesafe_key_present": bool(os.environ.get("TYPESAFE_API_KEY", "").strip()),
                "provider_connectivity_tested": False, "live_execution_available": False,
                "runs_continuously": False,
                "next_requirements": ["fresh sourced candles, quote, timeframe reviews, levels and calendar",
                                      "explicit optional provider installation and configuration",
                                      "a complete Freqtrade strategy/lifecycle before using its prepared mixin"]}
    if args.command == "demo":
        return run_demo(args.db)
    if args.command == "collect":
        from .adapters.openbb import OpenBBAdapter
        requested_at = current_time()
        result = OpenBBAdapter(timestamp_timezone=args.timestamp_timezone).fetch_closed_candles(
            symbol=args.symbol, asset_class=args.asset_class, timeframe=args.timeframe,
            start_date=args.start_date, end_date=args.end_date, as_of=requested_at, provider=args.provider)
        observed_at = current_time()
        if observed_at < requested_at:
            raise ValueError("collection clock moved backwards")
        result.update(schema_version=1, source_kind="market", observed_at=iso(observed_at),
                      reference=f"OpenBB {args.provider} {args.symbol} {args.timeframe}; retrieved {iso(observed_at)}")
        write_json(args.out, result)
        return {"output": str(Path(args.out).resolve()), "source_kind": "market", "candles": len(result["candles"]),
                "point_in_time_archive": False, "observed_at": iso(observed_at)}
    if args.command == "assemble":
        result = assemble_snapshot(read_json(args.candles), read_json(args.context), limit=args.limit)
        write_json(args.out, result)
        return {"output": str(Path(args.out).resolve()), "symbol": result["symbol"],
                "provenance": result["source"]["kind"], "bars": len(result["bars"])}
    if args.command in {"evaluate", "replay"}:
        snapshot = read_json(args.snapshot)
        kind = snapshot.get("source", {}).get("kind")
        if args.command == "evaluate" and kind != "market":
            raise ValueError("evaluate requires market evidence; use replay for synthetic/historical inputs")
        if args.command == "replay" and kind not in {"synthetic", "historical"}:
            raise ValueError("replay requires synthetic/historical evidence; never relabel a current collection as historical")
        if kind == "historical" and utc(snapshot["as_of"]) > current_time():
            raise ValueError("historical snapshots cannot be dated in the future")
        now = current_time() if args.command == "evaluate" else utc(snapshot["as_of"])
        return open_engine(args, online=args.command == "evaluate").run(snapshot, now=now)
    if args.command in {"advance", "audit"}:
        stored_metadata(args.db)  # These commands cannot silently create a new account.
        engine = open_engine(args)
        if args.command == "audit":
            return audit(engine.ledger, at=args.at)
        envelope = read_json(args.bar)
        if type(envelope.get("schema_version")) is not int or envelope["schema_version"] != 1:
            raise ValueError("unsupported execution bar envelope")
        observed_at = utc(envelope["observed_at"])
        if envelope["provenance"] == "historical" and observed_at > current_time():
            raise ValueError("historical observations cannot be dated in the future")
        if envelope["provenance"] == "market":
            receipt = current_time()
            if observed_at > receipt:
                raise ValueError("market bar observation is in the future")
            observed_at = receipt
        engine.advance(envelope["symbol"], envelope["bar"], observed_at=observed_at,
                       provenance=envelope["provenance"], timeframe=envelope["timeframe"])
        return audit(engine.ledger)
    raise ValueError("unknown command")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = dispatch(args)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError, sqlite3.Error, RuntimeError) as exc:
        # Provider exceptions can contain credential-bearing URLs. Only local
        # validation text is surfaced; unexpected/provider error text is hidden.
        message = str(exc) if type(exc) is ValueError else "Command failed; check the input contract and local provider prerequisites."
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": message}), file=sys.stderr)
        return 2
