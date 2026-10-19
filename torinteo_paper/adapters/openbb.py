"""Optional OpenBB market-data adapter with explicit candle-time semantics.

Importing this module does not import OpenBB or contact a provider.  The caller
must opt in by calling ``fetch_closed_candles``.  Returned observations still
require the strategy's independent freshness, coverage and provenance checks.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone, tzinfo
import math
import re
from typing import Any
from zoneinfo import ZoneInfo


UTC = timezone.utc
_ROUTES = {"crypto": "crypto", "equity": "equity", "forex": "currency"}
_DIRECT_INTERVALS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}
_AGGREGATIONS = {"4h": "1h", "1w": "1d", "1M": "1d"}


class DataAdapterError(ValueError):
    """The upstream response cannot establish the requested candle contract."""


class DataAdapterUnavailable(RuntimeError):
    """An optional dependency or upstream service is unavailable."""


def _utc_string(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DataAdapterError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _zone(value: str | tzinfo | None) -> tzinfo | None:
    if value is None or isinstance(value, tzinfo):
        return value
    if isinstance(value, str):
        try:
            return ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise DataAdapterError("timestamp_timezone must name an installed IANA time zone") from exc
    raise DataAdapterError("timestamp_timezone must be an IANA time-zone name or tzinfo")


def _date_argument(value: str | date, name: str) -> str:
    # Datetimes are deliberately not truncated: doing so would lose the
    # caller's time-zone and intraday boundary without their knowledge.
    if isinstance(value, datetime):
        raise DataAdapterError(f"{name} must be a date or an ISO YYYY-MM-DD string")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise DataAdapterError(f"{name} must use YYYY-MM-DD") from exc
        if parsed.isoformat() == value:
            return value
    raise DataAdapterError(f"{name} must use YYYY-MM-DD")


def _symbol_for(symbol: str, asset_class: str) -> str:
    """Translate notation, never substitute instruments or quote currencies."""
    if not isinstance(symbol, str) or not symbol or symbol != symbol.strip():
        raise DataAdapterError("symbol must be a non-empty, explicitly selected instrument")
    if asset_class not in _ROUTES:
        raise DataAdapterError("unsupported asset_class; choose crypto, equity or forex")
    value = symbol.upper()
    if asset_class == "crypto":
        # OpenBB documents Yahoo symbols such as BTC-USD. A slash is only an
        # alternate separator: BTC/USDT becomes BTC-USDT, never BTC-USD.
        value = value.replace("/", "-")
        if not re.fullmatch(r"[A-Z0-9]{2,16}-[A-Z0-9]{2,10}", value):
            raise DataAdapterError("crypto symbols require an explicit pair, e.g. BTC-USD")
    elif asset_class == "forex":
        if value.endswith("=X"):
            value = value[:-2]
        value = value.replace("/", "").replace("-", "")
        if not re.fullmatch(r"[A-Z]{6}", value) or value[:3] == value[3:]:
            raise DataAdapterError("forex symbols require both currencies, e.g. GBPUSD")
    elif not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,31}", value):
        raise DataAdapterError("equity symbols must be explicit provider tickers; indices/futures are unsupported")
    return value


def _timestamp(value: Any, configured_zone: tzinfo | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DataAdapterError("candle date must be an ISO date or datetime") from exc
    else:
        raise DataAdapterError("candle date must be a datetime, date or ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if configured_zone is None:
            raise DataAdapterError("naive candle timestamp: explicitly configure timestamp_timezone")
        candidates = [parsed.replace(tzinfo=configured_zone, fold=fold) for fold in (0, 1)]
        valid = [
            candidate for candidate in candidates
            if candidate.astimezone(UTC).astimezone(configured_zone).replace(tzinfo=None) == parsed
        ]
        if not valid:
            raise DataAdapterError("nonexistent local candle time during a clock change")
        if len({candidate.utcoffset() for candidate in valid}) > 1:
            raise DataAdapterError("ambiguous local candle time: upstream must supply an explicit UTC offset")
        parsed = valid[0]
    elif configured_zone is not None:
        parsed = parsed.astimezone(configured_zone)
    return parsed


def _number(value: Any, field: str, *, positive: bool) -> float:
    if isinstance(value, (str, bool)) or value is None:
        raise DataAdapterError(f"candle {field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataAdapterError(f"candle {field} must be a finite number") from exc
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise DataAdapterError(f"candle {field} must be {'positive' if positive else 'non-negative'} and finite")
    return result


def _rows(response: Any, provider: str) -> list[Any]:
    # OBBject exposes .results and .warnings. Its pydantic dump has the same
    # field names. No fallback to arbitrary HTML/data blobs or fabricated rows.
    if isinstance(response, Mapping):
        rows, warnings = response.get("results"), response.get("warnings")
        actual_provider = response.get("provider")
    else:
        rows = getattr(response, "results", None)
        warnings = getattr(response, "warnings", None)
        actual_provider = getattr(response, "provider", None)
    if warnings:
        raise DataAdapterError("OpenBB returned warnings; resolve them before accepting market evidence")
    if actual_provider is not None and actual_provider != provider:
        raise DataAdapterError("OpenBB returned a different provider than requested")
    if not isinstance(rows, (list, tuple)):
        raise DataAdapterError("OpenBB response must contain a results sequence")
    return list(rows)


def _row_mapping(row: Any) -> Mapping:
    if isinstance(row, Mapping):
        return row
    if hasattr(row, "model_dump"):
        dumped = row.model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    # OpenBB's data models expose these documented attributes. This also
    # permits small dependency-free contract doubles in tests.
    if all(hasattr(row, key) for key in ("date", "open", "high", "low", "close", "volume")):
        keys = ("date", "open", "high", "low", "close", "volume", "symbol",
                "asset_class", "interval", "timeframe", "close_time")
        return {key: getattr(row, key) for key in keys if hasattr(row, key)}
    raise DataAdapterError("OpenBB results must contain OHLCV data models")


def _normalise_rows(rows: list[Any], interval: str, asset_class: str,
                    as_of: datetime, configured_zone: tzinfo | None,
                    start_date: str, end_date: str, provider_symbol: str) -> tuple[list[dict], int]:
    result: list[dict] = []
    previous_open: datetime | None = None
    previous_close: datetime | None = None
    cadence_seen = False
    open_count = 0
    duration = _DIRECT_INTERVALS[interval]
    intraday_anchor = None
    for raw in rows:
        row = _row_mapping(raw)
        if row.get("symbol") is not None and _symbol_for(row["symbol"], asset_class) != provider_symbol:
            raise DataAdapterError("provider returned a candle for a different instrument")
        if row.get("asset_class") is not None and row["asset_class"] != asset_class:
            raise DataAdapterError("provider returned a different asset class")
        if "date" not in row:
            raise DataAdapterError("OpenBB candle is missing its date")
        local_open = _timestamp(row["date"], configured_zone)
        candle_open = local_open.astimezone(UTC)
        if not (start_date <= local_open.date().isoformat() <= end_date):
            raise DataAdapterError("provider returned a candle outside the requested date range")
        if local_open.second or local_open.microsecond:
            raise DataAdapterError("provider timestamps do not match whole-minute candle boundaries")
        if interval == "1d":
            if local_open.timetz().replace(tzinfo=None) != time.min:
                raise DataAdapterError("daily candles must be labelled at calendar-day boundaries")
            if asset_class == "crypto" and candle_open.timetz().replace(tzinfo=None) != time.min:
                raise DataAdapterError("crypto daily candles must use UTC day boundaries")
            # Calendar addition correctly retains an explicitly chosen IANA
            # zone over 23/25-hour DST days. It is a conservative day boundary,
            # not an invented exchange-session closing time.
            candle_close = (local_open + duration).astimezone(UTC)
        else:
            candle_close = candle_open + duration
            anchor = int(candle_open.timestamp()) % int(duration.total_seconds())
            if intraday_anchor is None:
                intraday_anchor = anchor
            if anchor != intraday_anchor or (asset_class == "crypto" and anchor != 0):
                raise DataAdapterError("returned candle boundaries contradict the requested interval")
        declared_interval = row.get("timeframe", row.get("interval"))
        if declared_interval is not None and declared_interval != interval:
            raise DataAdapterError("provider declared a different candle interval")
        if row.get("close_time") is not None:
            declared_close = _timestamp(row["close_time"], configured_zone).astimezone(UTC)
            if declared_close != candle_close:
                raise DataAdapterError("provider close time contradicts the requested interval")
            cadence_seen = True
        if candle_open > as_of:
            raise DataAdapterError("provider returned a future candle")
        if previous_open is not None:
            if candle_open <= previous_open or candle_open < previous_close:
                raise DataAdapterError("candles must be ordered, unique and non-overlapping")
            if candle_open == previous_close:
                cadence_seen = True
            elif asset_class == "crypto":
                raise DataAdapterError("crypto source data has a missing candle or a different interval")
        previous_open, previous_close = candle_open, candle_close
        values = {field: _number(row.get(field), field, positive=(field != "volume"))
                  for field in ("open", "high", "low", "close", "volume")}
        if not (values["low"] <= min(values["open"], values["close"])
                <= max(values["open"], values["close"]) <= values["high"]):
            raise DataAdapterError("candle OHLC values contradict each other")
        if candle_close > as_of:
            open_count += 1
            continue
        result.append({"open_time": candle_open, "close_time": candle_close, **values})
    # Sparse equity/forex series can contain legitimate session gaps, but a
    # single unexplained bar or daily bars masquerading as hourly data cannot
    # establish the interval. A matching explicit close_time is sufficient.
    if result and not cadence_seen:
        raise DataAdapterError("insufficient adjacent observations to verify the returned interval")
    return result, open_count


def _month_end(start: datetime) -> datetime:
    return start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)


def _bucket_bounds(value: datetime, timeframe: str) -> tuple[datetime, datetime]:
    if timeframe == "4h":
        start = value.replace(hour=value.hour // 4 * 4, minute=0, second=0, microsecond=0)
        return start, start + timedelta(hours=4)
    day = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if timeframe == "1w":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    start = day.replace(day=1)
    return start, _month_end(start)


def _aggregate(candles: list[dict], timeframe: str, as_of: datetime) -> tuple[list[dict], int]:
    groups: dict[datetime, list[dict]] = {}
    for candle in candles:
        start, _ = _bucket_bounds(candle["open_time"], timeframe)
        groups.setdefault(start, []).append(candle)
    completed = []
    incomplete = 0
    step = _DIRECT_INTERVALS[_AGGREGATIONS[timeframe]]
    for start, group in groups.items():
        _, end = _bucket_bounds(start, timeframe)
        expected_count = int((end - start) / step)
        if (end > as_of or len(group) != expected_count
                or group[0]["open_time"] != start or group[-1]["close_time"] != end
                or any(candle["open_time"] != start + index * step for index, candle in enumerate(group))):
            incomplete += 1
            continue
        try:
            volume = math.fsum(c["volume"] for c in group)
        except OverflowError as exc:
            raise DataAdapterError("aggregated volume exceeds the finite numeric contract") from exc
        if not math.isfinite(volume):
            raise DataAdapterError("aggregated volume exceeds the finite numeric contract")
        completed.append({
            "open_time": start, "close_time": end,
            "open": group[0]["open"], "high": max(c["high"] for c in group),
            "low": min(c["low"] for c in group), "close": group[-1]["close"],
            "volume": volume,
        })
    return completed, incomplete


class OpenBBAdapter:
    """Fetch explicit instruments through the optional OpenBB Python package.

    ``timestamp_timezone`` is required when upstream timestamps are naive. It
    must describe the provider's actual timestamp convention, not the user's
    display preference. Ambiguous/nonexistent local times are rejected.

    The initial provider contract is yfinance. 4h/weekly/calendar-month bars
    are assembled from complete crypto source bars. Other asset calendars
    require a separately reviewed session calendar and are unsupported here.
    """

    def __init__(self, client: Any = None, *, timestamp_timezone: str | tzinfo | None = None):
        self._client = client
        self._timestamp_timezone = _zone(timestamp_timezone)

    def fetch_closed_candles(self, symbol: str, asset_class: str, timeframe: str,
                             start_date: str | date, end_date: str | date,
                             as_of: datetime, provider: str = "yfinance") -> dict:
        as_of = _aware(as_of, "as_of")
        start, end = _date_argument(start_date, "start_date"), _date_argument(end_date, "end_date")
        if start > end:
            raise DataAdapterError("start_date must not be later than end_date")
        if provider != "yfinance":
            raise DataAdapterError("this adapter currently supports only the verified yfinance contract")
        provider_symbol = _symbol_for(symbol, asset_class)
        if timeframe not in _DIRECT_INTERVALS and timeframe not in _AGGREGATIONS:
            raise DataAdapterError("unsupported timeframe; use 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w or 1M")
        if timeframe in _AGGREGATIONS and asset_class != "crypto":
            raise DataAdapterError("4h/weekly/monthly aggregation requires a reviewed market session calendar; currently crypto only")
        source_interval = _AGGREGATIONS.get(timeframe, timeframe)
        if self._client is None:
            try:
                from openbb import obb
            except ImportError as exc:
                raise DataAdapterUnavailable("OpenBB is not installed; install and verify it explicitly before activation") from exc
            self._client = obb
        try:
            route = getattr(self._client, _ROUTES[asset_class])
            fetch = route.price.historical
        except AttributeError as exc:
            raise DataAdapterUnavailable("the selected OpenBB asset/provider extension is unavailable") from exc
        try:
            response = fetch(symbol=provider_symbol, provider=provider, interval=source_interval,
                             start_date=start, end_date=end)
        except Exception as exc:
            # Provider exception text may contain request URLs/credentials.
            raise DataAdapterUnavailable(f"OpenBB historical request failed ({type(exc).__name__})") from exc
        candles, open_count = _normalise_rows(
            _rows(response, provider), source_interval, asset_class, as_of,
            self._timestamp_timezone, start, end, provider_symbol,
        )
        incomplete = 0
        if timeframe in _AGGREGATIONS:
            candles, incomplete = _aggregate(candles, timeframe, as_of)
        serialised = [{**candle, "open_time": _utc_string(candle["open_time"]),
                       "close_time": _utc_string(candle["close_time"])} for candle in candles]
        return {
            "source": f"openbb:{provider}", "symbol": symbol, "asset_class": asset_class,
            "timeframe": timeframe, "candles": serialised, "as_of": _utc_string(as_of),
            "provider_symbol": provider_symbol, "source_timeframe": source_interval,
            "excluded_open_candles": open_count, "excluded_incomplete_buckets": incomplete,
            "point_in_time_archive": False,
        }
