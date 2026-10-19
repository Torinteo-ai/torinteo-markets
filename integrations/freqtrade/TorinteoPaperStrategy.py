"""Confirmation integration mixin only: this file is NOT a runnable strategy.

An independently reviewed Freqtrade IStrategy subclass must supply normal entry
signals, sizing, and persistent stop/target handling. See docs/FREQTRADE.md.
No optional dependency is imported here and no exchange/API calls are made.
"""
from datetime import timedelta
import os

from torinteo_paper.adapters.freqtrade import DECISION_ID, TicketStore, dry_run_mode
from torinteo_paper.models import utc


class TorinteoPaperConfirmationMixin:
    """Gate one latest-candle dry-run spot entry and preserve its approved terms.

    Place this mixin before IStrategy in the host's inheritance list. It does not
    implement populate_entry_trend or claim to enforce a complete trade lifecycle.
    """

    @staticmethod
    def _decision_from_tag(entry_tag):
        if not isinstance(entry_tag, str) or not entry_tag.startswith("tm:"):
            return None
        decision_id = entry_tag[3:]
        return decision_id if DECISION_ID.fullmatch(decision_id) else None

    def _ticket_store(self):
        return TicketStore(os.environ.get("TORINTEO_TICKET_DIR", ""))

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force,
                            current_time, entry_tag, side, **kwargs):
        """Deny unsupported mode, old/mismatched candle, bad ticket, or replay."""
        try:
            if not dry_run_mode(self.config.get("dry_run"), self.config.get("runmode")):
                return False
            trading_mode = self.config.get("trading_mode", "spot")
            if (getattr(trading_mode, "value", trading_mode) != "spot"
                    or self.timeframe != "15m"
                    or self.config.get("timeframe", "15m") != "15m"
                    or getattr(self, "can_short", False) is not False
                    or getattr(self, "position_adjustment_enable", False) is not False
                    or self.config.get("position_adjustment_enable", False) is not False
                    or self.config.get("force_entry_enable", False) is not False
                    or self.config.get("api_server", {}).get("enabled", False) is not False
                    or type(self.config.get("max_open_trades")) is not int
                    or self.config.get("max_open_trades") != 1
                    or order_type != "limit"):
                return False
            decision_id = self._decision_from_tag(entry_tag)
            if decision_id is None:
                return False
            store = self._ticket_store()
            ticket = store.read(decision_id)
            if ticket is None:
                return False
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if dataframe.empty:
                return False
            # Freqtrade dates are candle opening times. Inspect only its latest
            # analyzed row; never stamp today's ticket over a historical series.
            candle = dataframe.iloc[-1]
            signal_close = utc(candle["date"]) + timedelta(minutes=15)
            current = utc(current_time)
            if (candle.get("enter_long") != 1 or candle.get("enter_tag") != entry_tag
                    or candle.get("exit_long", 0) == 1
                    or utc(ticket["signal_bar_close"]) != signal_close
                    or not 0 <= (current - signal_close).total_seconds() < 900):
                return False
            return store.claim(ticket, pair=pair, side=side, rate=rate, amount=amount,
                               now=current, dry_run=self.config.get("dry_run"),
                               runmode=self.config.get("runmode"))
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, OverflowError):
            return False

    def torinteo_approved_terms(self, entry_tag):
        """Retrieve immutable claimed stop/target for host exit callbacks.

        Expired entry approval never disables protection on an open position.
        The host must persist the decision tag on its trade and treat missing
        terms as a reconciliation fault while retaining its native stoploss.
        """
        try:
            decision_id = self._decision_from_tag(entry_tag)
            if decision_id is None:
                return None
            return self._ticket_store().claimed_ticket(decision_id)
        except (OSError, ValueError):
            return None

    def check_entry_timeout(self, pair, trade, order, current_time, **kwargs):
        """Request cancellation when unfilled entry approval has expired."""
        terms = self.torinteo_approved_terms(getattr(trade, "enter_tag", None))
        try:
            return terms is None or utc(current_time) >= utc(terms["expires_at"])
        except (ValueError, KeyError, TypeError, AttributeError):
            return True

    def confirm_trade_exit(self, pair, trade, order_type, amount, rate, time_in_force,
                           exit_reason, current_time, **kwargs):
        """Never veto protective or other host-generated exits on an entry check."""
        return True
