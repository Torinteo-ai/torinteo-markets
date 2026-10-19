"""Deterministic entry checks for a sourced, reviewed breakout/retest setup."""
from datetime import timedelta
import hashlib
import json
from zoneinfo import ZoneInfo

from .models import RiskConfig, iso, number, quote_currency, utc, validate_snapshot


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def session_scope(now, instrument, events):
    """Scheduling scope only; a permitted session never constitutes a signal."""
    now = utc(now)
    local = now.astimezone(ZoneInfo("Europe/London"))
    if local.hour == 1:
        for event in events:
            currency = event.get("currency")
            if currency in {"AUD", "NZD"} and currency in instrument and event.get("impact") == "high":
                if abs((utc(event["starts_at"]) - now).total_seconds()) <= 3600:
                    return "AUD_NZD_NEWS_ONLY"
        return "NO_SCAN"
    if local.hour < 5:
        return "NO_SCAN"
    if local.hour == 5:
        return "PREOPEN_REVIEW"
    return "SETUPS"


def account_state(account, config):
    if account is None:
        return {"equity":config.initial_equity,"daily_pnl":0.0,"open_symbols":[],"open_notional":0.0}, "new_simulated_account"
    if not isinstance(account, dict):
        raise ValueError("invalid paper account")
    number(account["equity"], "equity", strictly_positive=True)
    number(account["daily_pnl"], "daily_pnl", minimum=-1e100)
    number(account["open_notional"], "open_notional")
    if not isinstance(account["open_symbols"], list) or any(not isinstance(x, str) for x in account["open_symbols"]):
        raise ValueError("invalid open symbols")
    return account, "persisted_simulated_account"


def evaluate(snapshot, *, now, config=None, account=None):
    config = config or RiskConfig()
    reasons = []
    result = {"schema_version":1,"mode":"paper","call":"NO_TRADE","reason_codes":reasons,
              "symbol":None,"asset_class":None,"as_of":None,"expires_at":None,
              "provenance":None,"account_kind":None,"intent":None,"evidence":{},"decision_id":None}
    try:
        now = utc(now)
        data = validate_snapshot(snapshot)
        account, account_kind = account_state(account, config)
        result.update(symbol=data["symbol"], asset_class=data["asset_class"], as_of=iso(data["as_of"]),
                      provenance=data["source"]["kind"], account_kind=account_kind)
        result["quote_currency"]=quote_currency(data)
        if result["quote_currency"]!=config.account_currency:
            reasons.append("ACCOUNT_CURRENCY_MISMATCH")
        result["decision_id"] = hashlib.sha256(canonical({"snapshot":data,"config":config.to_dict()}).encode()).hexdigest()
        as_of = utc(data["as_of"])
        result["expires_at"] = iso(as_of+timedelta(seconds=config.ticket_ttl_seconds))
        if as_of > now:
            reasons.append("FUTURE_SNAPSHOT")
        elif (now-as_of).total_seconds() > config.max_snapshot_age_seconds:
            reasons.append("STALE_SNAPSHOT")
        if (as_of-utc(data["quote"]["as_of"])).total_seconds() > config.max_quote_age_seconds:
            reasons.append("STALE_QUOTE")
        if (as_of-utc(data["bars"][-1]["close_time"])).total_seconds() > 900+config.max_snapshot_age_seconds:
            reasons.append("STALE_SETUP_BARS")
        spread = (data["quote"]["ask"]-data["quote"]["bid"])/data["quote"]["bid"]*10000
        if spread > config.max_spread_bps:
            reasons.append("SPREAD_TOO_WIDE")
        review_limits = {"1M":40*86400,"1w":14*86400,"1d":2*86400,"4h":8*3600,"1h":2*3600}
        if any((as_of-utc(data["reviews"][tf]["as_of"])).total_seconds() > limit for tf,limit in review_limits.items()):
            reasons.append("STALE_TIMEFRAME_REVIEW")
        bias = {data["reviews"][tf]["bias"] for tf in ("1d", "4h", "1h")}
        if len(bias) != 1 or "NEUTRAL" in bias:
            reasons.append("TIMEFRAME_CONFLICT")
        side = next(iter(bias)) if len(bias)==1 else None
        news = data["news"]
        if news["status"] != "clear" or (as_of-utc(news["as_of"])).total_seconds() > config.max_news_age_seconds:
            reasons.append("NEWS_UNVERIFIED")
        currencies = {event["currency"] for event in news["events"] if event["currency"] in data["symbol"]}
        # USD macro events also affect the supported risk-asset defaults.
        if data["asset_class"] in {"crypto", "index", "commodity", "equity"}:
            currencies.add("USD")
        if any(event["impact"]=="high" and event["currency"] in currencies and
               abs((utc(event["starts_at"])-as_of).total_seconds()) <= config.news_window_minutes*60
               for event in news["events"]):
            reasons.append("HIGH_IMPACT_NEWS_WINDOW")
        scope = session_scope(as_of, data["symbol"], news["events"])
        if config.enforce_session and scope != "SETUPS":
            reasons.append("SESSION_REVIEW_ONLY")
        if account["daily_pnl"] <= -config.initial_equity*config.daily_loss_fraction:
            reasons.append("DAILY_LOSS_LIMIT")
        if data["symbol"] in account["open_symbols"]:
            reasons.append("POSITION_ALREADY_OPEN")
        if len(account["open_symbols"]) >= config.max_open_positions:
            reasons.append("OPEN_POSITION_LIMIT")
        if account["open_notional"] >= account["equity"]*config.max_total_notional_fraction:
            reasons.append("EXPOSURE_LIMIT")
        result["evidence"] = {"source":data["source"],"session_scope":scope,"reviews":data["reviews"],
                              "news":news,"spread_bps":spread,"setup":"breakout_retest"}
        if reasons:
            return result
        candidates = []
        for level in data["levels"]:
            # A weekly opposing level is usable for risk, but the entry must be
            # tied to the reviewed major monthly/historical station.
            if level["timeframe"] not in {"1M", "historical"}:
                continue
            setup = detect_retest(data["bars"], level, side, config)
            if setup:
                candidates.append((level, setup))
        if not candidates:
            reasons.append("NO_CONFIRMED_RETEST")
            return result
        entry = data["quote"]["ask"] if side=="LONG" else data["quote"]["bid"]
        level, setup = min(candidates, key=lambda candidate:abs(entry-candidate[0]["price"]))
        price = level["price"]
        tolerance = price*config.level_tolerance_bps/10000
        if abs(entry-price) > tolerance*config.max_chase_tolerances:
            reasons.append("ENTRY_CHASING_PRICE")
            return result
        stop_buffer = price*config.stop_buffer_bps/10000
        last = data["bars"][-1]
        if side=="LONG":
            stop = min(last["low"],price)-stop_buffer
            opponents = [x["price"] for x in data["levels"] if x["id"]!=level["id"] and x["kind"]=="resistance" and x["price"]>entry]
            target = min(opponents)-stop_buffer if opponents else None
        else:
            stop = max(last["high"],price)+stop_buffer
            opponents = [x["price"] for x in data["levels"] if x["id"]!=level["id"] and x["kind"]=="support" and x["price"]<entry]
            target = max(opponents)+stop_buffer if opponents else None
        if target is None:
            reasons.append("NO_REVIEWED_TARGET_LEVEL")
            return result
        if not ((stop<entry<target) if side=="LONG" else (target<entry<stop)):
            reasons.append("OPPOSING_LEVEL_TOO_CLOSE")
            return result
        friction = entry*2*(config.fee_bps+config.slippage_bps)/10000
        unit_risk = abs(entry-stop)+friction
        reward = abs(target-entry)-friction
        ratio = reward/unit_risk
        if ratio < config.min_reward_risk:
            reasons.append("OPPOSING_LEVEL_TOO_CLOSE")
            return result
        quantity = min(account["equity"]*config.risk_fraction/unit_risk,
                       account["equity"]*config.max_notional_fraction/entry,
                       (account["equity"]*config.max_total_notional_fraction-account["open_notional"])/entry)
        if quantity <= 0:
            reasons.append("EXPOSURE_LIMIT")
            return result
        result["intent"] = {"side":side,"entry":entry,"stop":stop,"target":target,"quantity":quantity,
                            "notional":quantity*entry,"risk_cash":quantity*unit_risk,"reward_risk":ratio,
                            "fee_bps":config.fee_bps,"slippage_bps":config.slippage_bps}
        result["evidence"].update(level=level, acceptance=setup["acceptance"], retest=last["close_time"])
        result["call"] = side
        reasons.append("REVIEWED_BREAKOUT_RETEST")
        return result
    except (ValueError, KeyError, TypeError, OverflowError, AttributeError) as exc:
        result.update(call="NO_TRADE",intent=None)
        reasons.append("INVALID_EVIDENCE")
        # Never include the raw untrusted payload or a provider exception.
        result["validation_error"] = "Market evidence or paper-account contract is invalid."
        return result


def detect_retest(bars, level, side, config):
    price = level["price"]
    tol = price*config.level_tolerance_bps/10000
    last = bars[-1]
    long = side == "LONG"
    if level["kind"] != ("resistance" if long else "support"):
        return None
    if long:
        held = price-tol <= last["low"] <= price+tol and last["close"] > price+tol/2
    else:
        held = price-tol <= last["high"] <= price+tol and last["close"] < price-tol/2
    if not held:
        return None
    # Acceptance must precede the retest on different, already-closed bars.
    # Require two closes beyond the zone and a previous close on its other side.
    for index in range(1,len(bars)-2):
        before, first, second = bars[index-1:index+2]
        if long:
            accepted = before["close"] <= price+tol and first["close"]>price+tol and second["close"]>price+tol
            intact = all(bar["close"]>price-tol for bar in bars[index+2:])
        else:
            accepted = before["close"] >= price-tol and first["close"]<price-tol and second["close"]<price-tol
            intact = all(bar["close"]<price+tol for bar in bars[index+2:])
        if accepted and intact:
            return {"acceptance":[first["close_time"],second["close_time"]]}
    return None
