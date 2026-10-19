"""Validation at the market-evidence boundary; no financial inference."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
import re
from zoneinfo import ZoneInfo


def utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid UTC timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def iso(value):
    return utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def number(value, name, *, minimum=0.0, strictly_positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if result < minimum or (strictly_positive and result <= 0):
        raise ValueError(f"{name} outside allowed range")
    return result


def label(value, name, limit=2048):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be nonempty bounded text")
    return value


def symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9./:=_-]{0,39}", value):
        raise ValueError("invalid canonical symbol")
    return value


@dataclass(frozen=True)
class RiskConfig:
    mode: str = "paper"
    account_currency: str = "USD"
    risk_day_timezone: str = "Europe/London"
    initial_equity: float = 10000.0
    risk_fraction: float = 0.005
    daily_loss_fraction: float = 0.02
    max_notional_fraction: float = 0.10
    max_total_notional_fraction: float = 0.30
    max_open_positions: int = 3
    min_reward_risk: float = 2.0
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    max_spread_bps: float = 25.0
    level_tolerance_bps: float = 20.0
    stop_buffer_bps: float = 10.0
    max_chase_tolerances: float = 3.0
    max_snapshot_age_seconds: int = 120
    max_quote_age_seconds: int = 60
    max_news_age_seconds: int = 1800
    news_window_minutes: int = 30
    ticket_ttl_seconds: int = 120
    jev_mode: str = "disabled"
    jev_min_confidence: float = 0.60
    research_mode: str = "disabled"
    enforce_session: bool = True

    def __post_init__(self):
        if self.mode != "paper":
            raise ValueError("this worker has no live mode")
        if not isinstance(self.account_currency,str) or not re.fullmatch(r"[A-Z]{3,6}",self.account_currency):
            raise ValueError("invalid paper account currency")
        try: ZoneInfo(self.risk_day_timezone)
        except (TypeError,ValueError,KeyError) as exc:raise ValueError("invalid risk-day timezone") from exc
        if self.jev_mode not in {"disabled", "shadow", "required"} or self.research_mode not in {"disabled", "shadow", "required"}:
            raise ValueError("invalid provider mode")
        number(self.initial_equity, "initial_equity", strictly_positive=True)
        for name in ("risk_fraction", "daily_loss_fraction", "max_notional_fraction", "max_total_notional_fraction"):
            if not 0 < number(getattr(self, name), name) <= 1:
                raise ValueError(f"invalid {name}")
        if self.risk_fraction > self.daily_loss_fraction or self.max_notional_fraction > self.max_total_notional_fraction:
            raise ValueError("inconsistent risk limits")
        for name in ("min_reward_risk", "level_tolerance_bps", "stop_buffer_bps", "max_chase_tolerances", "max_spread_bps"):
            number(getattr(self, name), name, strictly_positive=True)
        for name in ("fee_bps", "slippage_bps"):
            if number(getattr(self, name), name) > 1000:
                raise ValueError(f"invalid {name}")
        for name in ("max_open_positions", "max_snapshot_age_seconds", "max_quote_age_seconds", "max_news_age_seconds", "news_window_minutes", "ticket_ttl_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86400:
                raise ValueError(f"invalid {name}")
        if not 0 <= number(self.jev_min_confidence, "jev_min_confidence") <= 1:
            raise ValueError("invalid confidence threshold")
        if not isinstance(self.enforce_session, bool):
            raise ValueError("enforce_session must be a boolean")

    def to_dict(self):
        return asdict(self)


def quote_currency(data):
    instrument=data["symbol"]
    implied=None
    if data["asset_class"]=="crypto":
        pair=re.fullmatch(r"[A-Z0-9_]{2,24}[-/]([A-Z]{3,6})",instrument)
        if not pair:raise ValueError("crypto requires an explicit base/quote pair")
        implied=pair.group(1)
    elif data["asset_class"]=="forex":
        pair=re.fullmatch(r"[A-Z]{3}/?([A-Z]{3})(?:=X)?",instrument)
        if not pair:raise ValueError("forex requires an explicit currency pair")
        implied=pair.group(1)
    elif "/" in instrument:
        implied=instrument.split("/",1)[1]
    stated=data.get("quote_currency",implied)
    if not isinstance(stated,str) or not re.fullmatch(r"[A-Z]{3,6}",stated):
        raise ValueError("explicit quote currency required")
    if implied and stated!=implied:raise ValueError("quote currency conflicts with instrument")
    return stated


def validate_bar(bar, *, as_of=None, interval_seconds=None):
    if not isinstance(bar, dict):
        raise ValueError("bar must be an object")
    start, end = utc(bar["open_time"]), utc(bar["close_time"])
    if start >= end or (as_of is not None and end > utc(as_of)):
        raise ValueError("bar is future, unclosed or has invalid bounds")
    if interval_seconds and (end - start).total_seconds() != interval_seconds:
        raise ValueError("bar interval mismatch")
    values = {key: number(bar[key], key, strictly_positive=True) for key in ("open", "high", "low", "close")}
    if values["low"] > min(values["open"], values["close"]) or values["high"] < max(values["open"], values["close"]) or values["low"] > values["high"]:
        raise ValueError("OHLC prices contradict one another")
    values.update(open_time=iso(start), close_time=iso(end), volume=number(bar["volume"], "volume"))
    return values


def validate_snapshot(data):
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported snapshot schema")
    symbol(data["symbol"])
    if data["asset_class"] not in {"crypto", "forex", "equity", "index", "commodity"}:
        raise ValueError("unsupported asset class")
    quote_currency(data)
    if data["timeframe"] != "15m":
        raise ValueError("initial setup detector requires 15m closed bars")
    as_of = utc(data["as_of"])
    source = data["source"]
    if source["kind"] not in {"market", "historical", "synthetic"}:
        raise ValueError("invalid source kind")
    label(source["provider"], "provider", 120)
    label(source["reference"], "source reference")
    quote = data["quote"]
    bid = number(quote["bid"], "bid", strictly_positive=True)
    ask = number(quote["ask"], "ask", strictly_positive=True)
    if ask < bid or utc(quote["as_of"]) > as_of:
        raise ValueError("crossed or future quote")
    bars = data["bars"]
    if not isinstance(bars, list) or not 5 <= len(bars) <= 1000:
        raise ValueError("need 5 to 1000 setup bars")
    previous_end = None
    for bar in bars:
        validate_bar(bar, as_of=as_of, interval_seconds=900)
        if previous_end is not None and utc(bar["open_time"]) != previous_end:
            raise ValueError("setup bars must be contiguous, unique and ordered")
        previous_end = utc(bar["close_time"])
    levels = data["levels"]
    if not isinstance(levels, list) or not 2 <= len(levels) <= 200:
        raise ValueError("reviewed major and opposing levels required")
    ids = set()
    for level in levels:
        key = label(level["id"], "level id", 120)
        if key in ids:
            raise ValueError("duplicate level id")
        ids.add(key)
        number(level["price"], "level price", strictly_positive=True)
        if level["kind"] not in {"support", "resistance"} or level["timeframe"] not in {"1M", "1w", "historical"}:
            raise ValueError("unsupported level classification")
        if utc(level["known_at"]) > as_of:
            raise ValueError("level was not known at decision time")
        label(level["reference"], "level reference")
    reviews = data["reviews"]
    for timeframe in ("1M", "1w", "1d", "4h", "1h"):
        review = reviews[timeframe]
        if utc(review["as_of"]) > as_of:
            raise ValueError("future timeframe review")
        if review["bias"] not in {"LONG", "SHORT", "NEUTRAL"}:
            raise ValueError("invalid reviewed direction")
        label(review["reference"], "review reference")
    news = data["news"]
    if news["status"] not in {"clear", "blocked", "unknown"} or utc(news["as_of"]) > as_of:
        raise ValueError("invalid or future news review")
    label(news["source"], "news source")
    if not isinstance(news["events"], list) or len(news["events"]) > 500:
        raise ValueError("news events must be a bounded array")
    for event in news["events"]:
        if not re.fullmatch(r"[A-Z]{3}", event["currency"]) or event["impact"] not in {"high", "medium", "low"}:
            raise ValueError("invalid calendar event")
        utc(event["starts_at"])
        if utc(event["known_at"]) > as_of:
            raise ValueError("future knowledge of a news event")
        label(event["reference"], "news reference")
    return data
