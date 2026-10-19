"""Optional current-day TradingAgents research; never an order adapter."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
import math
import re
from typing import Any, Callable


UTC = timezone.utc
_RATINGS = {"STRONG BUY": "bullish", "BUY": "bullish", "HOLD": "neutral",
            "SELL": "bearish", "STRONG SELL": "bearish"}
_REPORT_KEYS = (
    "market_report", "sentiment_report", "news_report", "fundamentals_report",
    "investment_debate_state", "investment_plan", "trader_investment_plan",
    "risk_debate_state", "final_trade_decision",
)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of and clock values must be timezone-aware datetimes")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _provider_ticker(ticker: str, asset_type: str) -> str:
    """Translate explicit symbol notation without changing the instrument."""
    if not isinstance(ticker, str) or not ticker or ticker != ticker.strip() or not ticker.isascii():
        raise ValueError("ticker must be an explicitly selected instrument")
    provider_ticker = ticker.upper()
    if asset_type == "crypto":
        if not re.fullmatch(r"[A-Z0-9]{2,16}[-/][A-Z0-9]{2,10}", provider_ticker):
            raise ValueError("crypto ticker must contain an explicit base/quote pair, e.g. BTC/USD")
        return provider_ticker.replace("/", "-")
    if len(provider_ticker) > 32 or not re.fullmatch(r"[A-Z0-9]+(?:[.-][A-Z0-9]+)*", provider_ticker):
        raise ValueError("equity ticker must be an explicit provider symbol with no paths, whitespace or empty segments")
    return provider_ticker


def _report_value(value: Any, depth: int = 0) -> Any:
    """Keep JSON evidence, not arbitrary LangChain/runtime objects."""
    if depth > 6:
        raise ValueError("research report nesting exceeds the evidence contract")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        if len(value) > 1_000_000:
            raise ValueError("research report is too large")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("research contains a non-finite value")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("research report keys must be strings")
        return {key: _report_value(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_report_value(item, depth + 1) for item in value]
    raise ValueError("research contains an unsupported runtime object")


class TradingAgentsAdapter:
    """Lazy wrapper around ``TradingAgentsGraph.propagate``.

    The upstream interface accepts a date, not an intraday cutoff. This wrapper
    therefore refuses replay and stale requests, and stamps successful output
    at *completion*. It never backdates new research to the requested snapshot.
    The consumer must compare that completion timestamp with its evidence
    snapshot before using the research. A rating is research, not a fill, order,
    calibrated win probability or instruction to open a short position.
    """

    def __init__(self, graph: Any = None, config: Mapping | None = None, *,
                 clock: Callable[[], datetime] | None = None,
                 max_request_age_seconds: float = 60.0):
        if (isinstance(max_request_age_seconds, bool)
                or not isinstance(max_request_age_seconds, (int, float))
                or not math.isfinite(max_request_age_seconds)
                or max_request_age_seconds < 0 or max_request_age_seconds > 60):
            raise ValueError("max_request_age_seconds must be between 0 and 60")
        if config is not None and not isinstance(config, Mapping):
            raise ValueError("config must be a mapping")
        self._graph = graph
        self._config = deepcopy(dict(config)) if config is not None else None
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_age = float(max_request_age_seconds)

    def research(self, ticker: str, asset_type: str, as_of: datetime) -> dict:
        result = {"status": "invalid", "provider": "tradingagents", "as_of": None,
                  "symbol": ticker, "rating": None, "stance": "unknown", "reports": {}, "reason": ""}
        try:
            requested = _utc(as_of)
            started = _utc(self._clock())
        except ValueError as exc:
            result["reason"] = str(exc)
            return result
        result.update(as_of=_iso(started), requested_as_of=_iso(requested), started_at=_iso(started))
        if asset_type not in ("stock", "equity", "crypto"):
            result["reason"] = "unsupported research asset_type; choose stock/equity or crypto"
            return result
        try:
            provider_ticker = _provider_ticker(ticker, asset_type)
        except ValueError as exc:
            result["reason"] = str(exc)
            return result
        # Keep symbol as the engine's original canonical identity. The
        # provider spelling is additional provenance, not a replacement key.
        result["provider_symbol"] = provider_ticker
        if requested > started:
            result["reason"] = "future research timestamps are unsupported"
            return result
        if requested.date() != started.date() or (started - requested).total_seconds() > self._max_age:
            result.update(status="unavailable", reason=(
                "TradingAgents accepts a date only; historical/intraday replay cannot enforce the requested cutoff. "
                "Request current research explicitly and use its completion timestamp."
            ))
            return result
        if self._graph is None:
            try:
                from tradingagents.graph.trading_graph import TradingAgentsGraph
                from tradingagents.default_config import DEFAULT_CONFIG
            except ImportError:
                result.update(status="unavailable", reason="TradingAgents is not installed; no research was run")
                return result
            # Never silently activate the upstream's default paid model. The
            # caller must explicitly select provider and both model tiers.
            required = ("llm_provider", "quick_think_llm", "deep_think_llm")
            if self._config is None or any(not self._config.get(key) for key in required):
                result.update(status="unavailable", reason=(
                    "Explicit llm_provider, quick_think_llm and deep_think_llm configuration is required before activation"
                ))
                return result
            settings = deepcopy(DEFAULT_CONFIG)
            settings.update(deepcopy(self._config))
            try:
                self._graph = TradingAgentsGraph(debug=False, config=settings)
            except Exception as exc:
                result.update(status="unavailable", reason=f"TradingAgents initialisation failed ({type(exc).__name__})")
                return result
        try:
            returned = self._graph.propagate(
                provider_ticker, started.date().isoformat(), asset_type="stock" if asset_type == "equity" else asset_type,
            )
            finished = _utc(self._clock())
        except Exception as exc:
            result.update(status="unavailable", reason=f"TradingAgents research failed ({type(exc).__name__})")
            return result
        if finished < started:
            result["reason"] = "research completion clock moved backwards; evidence timestamp is invalid"
            return result
        result["as_of"] = _iso(finished)
        if not isinstance(returned, tuple) or len(returned) != 2 or not isinstance(returned[0], Mapping):
            result["reason"] = "TradingAgents must return (state, rating); no order/prose coercion is accepted"
            return result
        state, rating = returned
        if isinstance(rating, Enum):
            rating = rating.value
        if not isinstance(rating, str) or rating not in _RATINGS:
            result["reason"] = "unrecognised research rating; expected the explicit five-tier rating vocabulary"
            return result
        if not isinstance(state.get("final_trade_decision"), (str, Mapping)) or not state["final_trade_decision"]:
            result["reason"] = "research is missing its final decision report"
            return result
        try:
            reports = {key: _report_value(state[key]) for key in _REPORT_KEYS if key in state}
        except ValueError as exc:
            result["reason"] = str(exc)
            return result
        result.update(status="ok", rating=rating, stance=_RATINGS[rating], reports=reports, reason=(
            "Current-day date-only research, recorded at completion. No historical intraday guarantee; "
            "a rating is research evidence and does not authorise an order."
        ))
        return result
