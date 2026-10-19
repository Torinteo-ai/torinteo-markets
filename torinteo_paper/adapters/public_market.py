"""Bounded, unauthenticated Coinbase spot data for the paper desk.

This module has no account or order routes. It returns observations, not a
strategy snapshot: reviewed levels, timeframe direction and calendar evidence
must still be supplied independently. Importing it makes no network requests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import re
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..models import iso, utc, validate_bar


_BASE_URL = "https://api.coinbase.com/api/v3/brokerage/market"
_PRODUCTS = {"BTC/USD": "BTC-USD", "ETH/USD": "ETH-USD", "SOL/USD": "SOL-USD"}
_INTERVALS = {
    "1m": (60, "ONE_MINUTE"),
    "5m": (300, "FIVE_MINUTE"),
    "15m": (900, "FIFTEEN_MINUTE"),
    "30m": (1800, "THIRTY_MINUTE"),
    "1h": (3600, "ONE_HOUR"),
    "4h": (14400, "FOUR_HOUR"),
    "1d": (86400, "ONE_DAY"),
}
SUPPORTED_SYMBOLS = tuple(_PRODUCTS)
SUPPORTED_TIMEFRAMES = tuple(_INTERVALS)
_MAX_RESPONSE_BYTES = 1024 * 1024


class PublicMarketError(ValueError):
    """A requested or returned observation cannot establish the data contract."""


class PublicMarketUnavailable(RuntimeError):
    """The public provider cannot be reached or returned an error."""


def _decode_json(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
        raise PublicMarketError("public response exceeds the 1 MiB limit")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PublicMarketError("public response contains duplicate JSON keys")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise PublicMarketError("public response contains a non-finite JSON number")

    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys,
                            parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PublicMarketError("public response must be bounded UTF-8 JSON") from exc
    if not isinstance(result, dict):
        raise PublicMarketError("public response must be an object")
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PublicMarketUnavailable("public market data redirects are not accepted")


def _get_json(url: str, timeout_seconds: float) -> dict:
    request = Request(url, method="GET", headers={
        "User-Agent": "TorinteoMarkets/0.1 public-market-data",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    })
    try:
        # Environment-managed HTTPS proxies remain supported. No credentials
        # are loaded or sent by this adapter, and redirects are rejected.
        with build_opener(_NoRedirect()).open(request, timeout=timeout_seconds) as response:
            if response.geturl() != url:
                raise PublicMarketUnavailable("public market data changed its requested route")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise PublicMarketUnavailable(f"Coinbase public market data is unavailable (HTTP {exc.code})") from None
    except (PublicMarketError, PublicMarketUnavailable):
        raise
    except Exception:
        # Proxy/network exception bodies may contain internal deployment
        # details. The caller only needs a safe operational failure reason.
        raise PublicMarketUnavailable("Coinbase public market data could not be reached") from None
    return _decode_json(raw)


def _positive_number(value, name: str, *, allow_zero=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise PublicMarketError(f"{name} must be a finite number")
    if isinstance(value, str) and (not value or len(value) > 64 or value != value.strip()):
        raise PublicMarketError(f"{name} must be bounded numeric text")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PublicMarketError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or (result < 0 if allow_zero else result <= 0):
        raise PublicMarketError(f"{name} must be {'non-negative' if allow_zero else 'positive'} and finite")
    return result


def _provider_time(value) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise PublicMarketError("quote timestamp must be bounded text with a timezone")
    try:
        return utc(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PublicMarketError("quote timestamp must include a valid timezone") from exc


def _epoch(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise PublicMarketError("candle start must be whole Unix seconds")
    if not re.fullmatch(r"[0-9]{1,12}", str(value)):
        raise PublicMarketError("candle start must be bounded whole Unix seconds")
    return int(value)


def _check_identity(payload: dict, product: str) -> None:
    if "product_id" in payload and payload["product_id"] != product:
        raise PublicMarketError("provider returned a different instrument")


def _candles(payload: dict, *, product: str, started_at: datetime,
             interval: int, limit: int) -> tuple[list[dict], int]:
    _check_identity(payload, product)
    rows = payload.get("candles")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 350:
        raise PublicMarketError("public candles must be a bounded, non-empty array")
    latest_close = int(started_at.timestamp()) // interval * interval
    earliest_open = latest_close - interval * limit
    by_open = {}
    seen = set()
    excluded = 0
    for row in rows:
        if not isinstance(row, dict):
            raise PublicMarketError("each candle must be an object")
        _check_identity(row, product)
        start = _epoch(row.get("start"))
        if start in seen:
            raise PublicMarketError("public candles contain a duplicate timestamp")
        seen.add(start)
        if start % interval:
            raise PublicMarketError("candle timestamp contradicts the requested interval boundary")
        if start < earliest_open or start > started_at.timestamp():
            raise PublicMarketError("provider returned a future or out-of-window candle")
        values = {name: _positive_number(row.get(name), f"candle {name}", allow_zero=name == "volume")
                  for name in ("open", "high", "low", "close", "volume")}
        if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
            raise PublicMarketError("candle OHLC values contradict one another")
        if start + interval > started_at.timestamp():
            excluded += 1
            continue
        try:
            bar = validate_bar({
                "open_time": datetime.fromtimestamp(start, timezone.utc),
                "close_time": datetime.fromtimestamp(start + interval, timezone.utc),
                **values,
            }, as_of=started_at, interval_seconds=interval)
        except (ValueError, OverflowError, OSError) as exc:
            raise PublicMarketError("provider returned invalid candle times or values") from exc
        by_open[start] = bar
    expected = range(earliest_open, latest_close, interval)
    if len(by_open) != limit or any(start not in by_open for start in expected):
        # An interval without a returned candle is missing evidence. Do not
        # invent a flat candle, forward-fill a price, or bridge the gap.
        raise PublicMarketError("provider did not return a complete, current closed-candle window")
    return [by_open[start] for start in expected], excluded


def _quote(payload: dict, *, product: str, observed_at: datetime,
           max_age_seconds: float, reference: str) -> dict:
    book = payload.get("pricebook")
    if not isinstance(book, dict) or book.get("product_id") != product:
        raise PublicMarketError("public book does not match the selected instrument")
    values = {}
    for side in ("bids", "asks"):
        levels = book.get(side)
        if not isinstance(levels, list) or len(levels) != 1 or not isinstance(levels[0], dict):
            raise PublicMarketError("public book must contain the requested single bid and ask")
        values[side] = _positive_number(levels[0].get("price"), f"book {side} price")
        _positive_number(levels[0].get("size"), f"book {side} size")
    if values["asks"] < values["bids"]:
        raise PublicMarketError("public book has crossed bid/ask prices")
    provider_at = _provider_time(book.get("time"))
    age = (observed_at - provider_at).total_seconds()
    if not 0 <= age <= max_age_seconds:
        raise PublicMarketError("public quote timestamp is stale or in the future")
    return {"bid": values["bids"], "ask": values["asks"],
            "as_of": iso(provider_at), "observed_at": iso(observed_at),
            "reference": reference, "provider": "coinbase-public"}


class PublicMarketClient:
    """Collect current spot candles and a public quote without credentials.

    ``transport(url, timeout_seconds) -> dict`` and ``clock() -> aware datetime``
    are trusted application/test dependencies. They are not request parameters.
    The method has no historical ``as_of`` override: observations use the actual
    collection clock. Callers must preserve provider and receipt timestamps.
    """

    def __init__(self, *, timeout_seconds=8, max_quote_age_seconds=30,
                 transport: Callable | None = None, clock: Callable | None = None):
        for value, name, ceiling in ((timeout_seconds, "timeout_seconds", 30),
                                     (max_quote_age_seconds, "max_quote_age_seconds", 60)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= ceiling:
                raise PublicMarketError(f"{name} must be a finite number between zero and {ceiling}")
        self.timeout_seconds = float(timeout_seconds)
        self.max_quote_age_seconds = float(max_quote_age_seconds)
        self._transport = transport or _get_json
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        try:
            return utc(self._clock())
        except (TypeError, ValueError, OverflowError) as exc:
            raise PublicMarketError("collection clock must return an aware datetime") from exc

    def _read(self, url: str) -> dict:
        try:
            result = self._transport(url, self.timeout_seconds)
        except (PublicMarketError, PublicMarketUnavailable):
            raise
        except Exception:
            raise PublicMarketUnavailable("Coinbase public market data could not be reached") from None
        if not isinstance(result, dict):
            raise PublicMarketError("public response must be an object")
        if result.get("error") or result.get("errors"):
            raise PublicMarketUnavailable("Coinbase returned a public market-data error")
        return result

    def collect(self, *, symbol="BTC/USD", timeframe="15m", limit=100) -> dict:
        if not isinstance(symbol, str) or symbol not in _PRODUCTS:
            raise PublicMarketError("select BTC/USD, ETH/USD or SOL/USD; other instruments are not configured")
        if not isinstance(timeframe, str) or timeframe not in _INTERVALS:
            raise PublicMarketError("unsupported public candle timeframe")
        if type(limit) is not int or not 5 <= limit <= 300:
            raise PublicMarketError("public candle limit must be an integer from 5 to 300")
        product = _PRODUCTS[symbol]
        interval, granularity = _INTERVALS[timeframe]
        started_at = self._now()
        end = int(started_at.timestamp())
        latest_close = end // interval * interval
        candle_url = f"{_BASE_URL}/products/{product}/candles?" + urlencode({
            "start": latest_close - interval * limit,
            "end": end,
            "granularity": granularity,
            "limit": limit + 1,
        })
        candle_payload = self._read(candle_url)
        candles_observed_at = self._now()
        if candles_observed_at < started_at:
            raise PublicMarketError("collection clock moved backwards")
        candles, excluded = _candles(candle_payload, product=product, started_at=started_at,
                                      interval=interval, limit=limit)
        # Fetch the book after candles so the quote is the latest observation.
        book_url = f"{_BASE_URL}/product_book?" + urlencode({"product_id": product, "limit": 1})
        book_payload = self._read(book_url)
        observed_at = self._now()
        if observed_at < candles_observed_at:
            raise PublicMarketError("collection clock moved backwards")
        if int(observed_at.timestamp()) // interval * interval != latest_close:
            raise PublicMarketError("collection crossed a candle boundary; refresh to collect the new closed bar")
        quote = _quote(book_payload, product=product, observed_at=observed_at,
                       max_age_seconds=self.max_quote_age_seconds, reference=book_url)
        return {
            "schema_version": 1,
            "symbol": symbol,
            "provider_symbol": product,
            "asset_class": "crypto",
            "base_currency": symbol.split("/")[0],
            "quote_currency": "USD",
            "timeframe": timeframe,
            "source": "coinbase-public",
            "source_kind": "market",
            "reference": candle_url,
            "as_of": iso(started_at),
            "observed_at": iso(observed_at),
            "candles_observed_at": iso(candles_observed_at),
            "point_in_time_archive": False,
            "excluded_open_candles": excluded,
            "candles": candles,
            "quote": quote,
        }
