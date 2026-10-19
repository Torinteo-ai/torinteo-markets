"""Fail-closed, local approval boundary for Freqtrade dry-run entries.

This module does not call an exchange, submit an order, or implement a strategy.
The ticket directory is a trusted local handoff, not an authentication protocol.
"""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat

from ..models import iso, number, utc


TICKET_FIELDS = frozenset({
    "schema_version", "mode", "executor", "provenance", "decision_id", "symbol",
    "side", "issued_at", "expires_at", "signal_bar_close", "entry", "stop", "target",
    "quantity", "notional", "risk_cash", "fee_bps", "slippage_bps", "entry_tolerance_bps",
})
DECISION_ID = re.compile(r"[a-f0-9]{64}\Z")
SPOT_PAIR = re.compile(r"[A-Z0-9]{1,20}/[A-Z0-9]{1,20}\Z")
MAX_TICKET_BYTES = 16_384


def dry_run_mode(dry_run, runmode):
    """Accept Freqtrade's RunMode enum or its exact string value, never truthiness."""
    return dry_run is True and getattr(runmode, "value", runmode) == "dry_run"


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError("ticket timestamp must be ISO text")
    return utc(value)


def _within_budget(actual, approved):
    # The native producer uses floats. Permit only one representable float of
    # arithmetic rounding, not a monetary or proportional budget allowance.
    return actual <= math.nextafter(approved, math.inf)


def validate_ticket(ticket, *, pair, side, rate, amount, now, dry_run, runmode):
    """Return False for every unsupported, stale, misbound, or over-budget entry.

    ``amount`` is base-asset quantity; ``rate`` is the proposed quote-asset price.
    Approval is for a spot LONG. Freqtrade's callback spells that side ``long``.
    Costs follow the native gate: round-trip fee/slippage at the proposed rate.
    Validation is not a fill, a freshness lookup, or proof of market provenance.
    """
    try:
        if not dry_run_mode(dry_run, runmode):
            return False
        if not isinstance(ticket, dict) or set(ticket) != TICKET_FIELDS:
            return False
        if type(ticket["schema_version"]) is not int or ticket["schema_version"] != 1:
            return False
        if (ticket["mode"], ticket["executor"], ticket["provenance"]) != (
            "paper", "freqtrade_dry_run", "market"
        ):
            return False
        if not isinstance(ticket["decision_id"], str) or not DECISION_ID.fullmatch(ticket["decision_id"]):
            return False
        if not isinstance(pair, str) or not SPOT_PAIR.fullmatch(pair) or ticket["symbol"] != pair:
            return False
        if pair.split("/")[0] == pair.split("/")[1]:
            return False
        if ticket["side"] != "LONG" or side not in ("long", "LONG"):
            return False
        issued, expires = _stamp(ticket["issued_at"]), _stamp(ticket["expires_at"])
        signal_close, current = _stamp(ticket["signal_bar_close"]), utc(now)
        if not 0 < (expires - issued).total_seconds() <= 120:
            return False
        if not signal_close <= issued <= current < expires:
            return False
        values = {key: number(ticket[key], key, strictly_positive=True)
                  for key in ("entry", "stop", "target", "quantity", "notional", "risk_cash")}
        fee, slip = number(ticket["fee_bps"], "fee_bps"), number(ticket["slippage_bps"], "slippage_bps")
        if fee > 1000 or slip > 1000:
            return False
        tolerance = number(ticket["entry_tolerance_bps"], "entry_tolerance_bps")
        if tolerance != 10:
            return False
        rate, amount = number(rate, "rate", strictly_positive=True), number(amount, "amount", strictly_positive=True)
        if not values["stop"] < values["entry"] < values["target"] or not values["stop"] < rate < values["target"]:
            return False
        lower = values["entry"] * (1 - tolerance / 10_000)
        upper = values["entry"] * (1 + tolerance / 10_000)
        if rate < math.nextafter(lower, -math.inf) or rate > math.nextafter(upper, math.inf):
            return False
        if amount > values["quantity"]:
            return False
        notional = amount * rate
        cash_risk = amount * (rate - values["stop"] + 2 * rate * (fee + slip) / 10_000)
        return (math.isfinite(notional) and math.isfinite(cash_risk)
                and _within_budget(notional, values["notional"])
                and _within_budget(cash_risk, values["risk_cash"]))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class TicketStore:
    """Bounded ticket reads and a durable, atomic, at-most-once confirmation claim.

    Losing an order after claiming intentionally burns its approval. Restarting
    does not make that ticket reusable. The claim is not an execution receipt.
    """

    def __init__(self, directory):
        if not directory:
            raise ValueError("ticket directory is required")
        self.directory = Path(directory).expanduser().resolve(strict=True)
        info = self.directory.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise ValueError("ticket directory must not be writable by other users")
        self.claims_path = self.directory / "claims.sqlite3"

    def read(self, decision_id):
        """Read only the exact hex-ID JSON file, rejecting symlinks and large files."""
        if not isinstance(decision_id, str) or not DECISION_ID.fullmatch(decision_id):
            return None
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            candidate = self.directory / f"{decision_id}.json"
            if candidate.is_symlink():
                return None
            with os.fdopen(os.open(candidate, flags), "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TICKET_BYTES or info.st_mode & 0o022:
                    return None
                raw = stream.read(MAX_TICKET_BYTES + 1)
            if len(raw) > MAX_TICKET_BYTES:
                return None
            ticket = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(ticket, dict) or ticket.get("decision_id") != decision_id:
                return None
            return ticket
        except (OSError, ValueError, UnicodeError):
            return None

    def claim(self, ticket, *, pair, side, rate, amount, now, dry_run, runmode):
        """Validate and durably consume an approval before returning True."""
        if not validate_ticket(ticket, pair=pair, side=side, rate=rate, amount=amount,
                               now=now, dry_run=dry_run, runmode=runmode):
            return False
        try:
            if self.claims_path.is_symlink():
                return False
            if self.claims_path.exists():
                info = self.claims_path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                    return False
            else:
                try:
                    descriptor = os.open(self.claims_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(descriptor)
                except FileExistsError:
                    pass
            info = self.claims_path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                return False
            # A single filesystem/SQLite transaction is the cross-process lock.
            with closing(sqlite3.connect(self.claims_path, timeout=2)) as connection:
                with connection:
                    connection.execute("PRAGMA synchronous=FULL")
                    connection.execute("CREATE TABLE IF NOT EXISTS confirmation_claims ("
                                       "decision_id TEXT PRIMARY KEY, ticket_json TEXT NOT NULL, "
                                       "pair TEXT NOT NULL, rate REAL NOT NULL, amount REAL NOT NULL, "
                                       "confirmed_at TEXT NOT NULL)")
                    connection.execute("INSERT INTO confirmation_claims VALUES (?, ?, ?, ?, ?, ?)",
                                       (ticket["decision_id"], json.dumps(ticket, sort_keys=True, allow_nan=False),
                                        pair, float(rate), float(amount), iso(now)))
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError):
            return False

    def claimed_ticket(self, decision_id):
        """Return retained approved terms for exit handling, even after expiry.

        This method grants no entry authority and does not claim a second time.
        """
        if not isinstance(decision_id, str) or not DECISION_ID.fullmatch(decision_id):
            return None
        try:
            info = self.claims_path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
                return None
            uri = self.claims_path.as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
                row = connection.execute("SELECT ticket_json FROM confirmation_claims WHERE decision_id = ?",
                                         (decision_id,)).fetchone()
            ticket = json.loads(row[0], object_pairs_hook=_unique_object) if row else None
            return ticket if isinstance(ticket, dict) and ticket.get("decision_id") == decision_id else None
        except (OSError, sqlite3.Error, ValueError, UnicodeError):
            return None
