"""Transactional, restartable paper orders; contains no exchange client."""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from .gate import canonical, evaluate
from .models import RiskConfig, iso, number, symbol, utc, validate_bar


class PaperLedger:
    def __init__(self, path, config=None):
        self.path=Path(path)
        self.config=config or RiskConfig()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS decisions(
                    id TEXT PRIMARY KEY, as_of TEXT, symbol TEXT, call TEXT NOT NULL,
                    executor TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orders(
                    id INTEGER PRIMARY KEY, decision_id TEXT NOT NULL UNIQUE REFERENCES decisions(id),
                    symbol TEXT NOT NULL, side TEXT NOT NULL, provenance TEXT NOT NULL,
                    quantity REAL NOT NULL, planned_quantity REAL NOT NULL, intended_entry REAL NOT NULL, entry REAL,
                    stop REAL NOT NULL,target REAL NOT NULL,notional REAL NOT NULL,risk_cash REAL NOT NULL,
                    fee_bps REAL NOT NULL,slippage_bps REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('PENDING','OPEN','CLOSED','CANCELLED')),
                    approved_at TEXT NOT NULL,expires_at TEXT NOT NULL,opened_at TEXT,closed_at TEXT,
                    exit_price REAL,fees REAL NOT NULL DEFAULT 0,net_pnl REAL,exit_reason TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_per_symbol
                    ON orders(symbol) WHERE status IN ('PENDING','OPEN');
                CREATE TABLE IF NOT EXISTS events(
                    sequence INTEGER PRIMARY KEY,occurred_at TEXT NOT NULL,kind TEXT NOT NULL,
                    decision_id TEXT,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS processed_bars(
                    symbol TEXT NOT NULL,open_time TEXT NOT NULL,close_time TEXT NOT NULL,
                    provenance TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(symbol,open_time));
                CREATE TABLE IF NOT EXISTS cashflows(
                    sequence INTEGER PRIMARY KEY,occurred_at TEXT NOT NULL,kind TEXT NOT NULL,
                    amount REAL NOT NULL,order_id INTEGER NOT NULL REFERENCES orders(id));
            """)
            db.execute("BEGIN IMMEDIATE")
            raw=canonical(self.config.to_dict())
            saved=db.execute("SELECT value FROM metadata WHERE key='config'").fetchone()
            if saved and saved[0]!=raw:
                raise ValueError("existing paper ledger uses a different configuration; use a new experiment database")
            db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('config',?)",(raw,))
            db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('schema','1')")
            db.commit()

    @contextmanager
    def _connection(self):
        db=sqlite3.connect(self.path,timeout=15,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        except Exception:
            if db.in_transaction:db.rollback()
            raise
        finally:
            db.close()

    def _event(self,db,when,kind,decision_id,payload):
        db.execute("INSERT INTO events(occurred_at,kind,decision_id,payload) VALUES(?,?,?,?)",
                   (iso(when),kind,decision_id,canonical(payload)))

    def _account(self,db,when):
        local=utc(when).astimezone(ZoneInfo(self.config.risk_day_timezone))
        start=iso(local.replace(hour=0,minute=0,second=0,microsecond=0))
        cutoff=iso(when)
        total=db.execute("SELECT COALESCE(SUM(amount),0) FROM cashflows WHERE occurred_at<=?",(cutoff,)).fetchone()[0]
        pnl=db.execute("SELECT COALESCE(SUM(amount),0) FROM cashflows WHERE occurred_at>=? AND occurred_at<=?",(start,cutoff)).fetchone()[0]
        # Reconstruct exposure at the requested time, including a position that
        # has closed since that time and the original size before a later fill.
        rows=db.execute("""SELECT symbol,CASE WHEN opened_at IS NULL OR opened_at>?
                    THEN planned_quantity*intended_entry ELSE notional END AS notional
                    FROM orders WHERE approved_at<=? AND (closed_at IS NULL OR closed_at>?)""",
                    (cutoff,cutoff,cutoff)).fetchall()
        return {"equity":self.config.initial_equity+total,"daily_pnl":pnl,
                "open_symbols":[row["symbol"] for row in rows],"open_notional":sum(row["notional"] for row in rows)}

    def _clock(self,db,when):
        previous=db.execute("SELECT value FROM metadata WHERE key='logical_clock'").fetchone()
        if previous and utc(when)<utc(previous[0]):raise ValueError("new observations cannot move the experiment clock backwards")
        db.execute("INSERT INTO metadata(key,value) VALUES('logical_clock',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(iso(when),))

    def _event_clock(self,db,when):
        previous=db.execute("SELECT value FROM metadata WHERE key='event_clock'").fetchone()
        if previous and utc(when)<utc(previous[0]):raise ValueError("late event would rewrite already-processed paper state")
        db.execute("INSERT INTO metadata(key,value) VALUES('event_clock',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(iso(when),))

    def _bind(self,db,key,value):
        previous=db.execute("SELECT value FROM metadata WHERE key=?",(key,)).fetchone()
        if previous and previous[0]!=value:raise ValueError(f"paper experiment cannot mix {key}; use a separate database")
        db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES(?,?)",(key,value))

    def account(self,when):
        with self._connection() as db:return self._account(db,when)

    def decision(self,decision_id):
        if not decision_id:return None
        with self._connection() as db:
            row=db.execute("SELECT payload FROM decisions WHERE id=?",(decision_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def record(self,snapshot,decision,*,now,executor="native"):
        if executor not in {"native","freqtrade_dry_run"}:
            raise ValueError("unsupported paper executor")
        decision=json.loads(canonical(decision))
        if not decision["decision_id"]:
            decision["decision_id"]=hashlib.sha256(canonical({"invalid":decision,"at":iso(now)}).encode()).hexdigest()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old=db.execute("SELECT payload,executor FROM decisions WHERE id=?",(decision["decision_id"],)).fetchone()
            if old:
                if old["executor"]!=executor:
                    raise ValueError("this decision already belongs to a different paper executor")
                db.commit()
                return json.loads(old["payload"])
            self._clock(db,now)
            self._event_clock(db,now)
            self._bind(db,"executor",executor)
            if decision["provenance"]:self._bind(db,"provenance",decision["provenance"])
            # Recompute all deterministic entry and risk checks under the same
            # write lock that reserves exposure. Model results cannot override it.
            if decision["call"] in {"LONG","SHORT"}:
                checked=evaluate(snapshot,now=now,config=self.config,account=self._account(db,now))
                if checked["call"]!=decision["call"]:
                    decision.update(call="NO_TRADE",intent=None)
                    decision["reason_codes"].extend(checked["reason_codes"])
                    decision["reason_codes"].append("TRANSACTIONAL_RECHECK_BLOCKED")
                else:
                    decision["intent"]=checked["intent"]
            decision["executor"]=executor
            decision["approved_at"]=iso(now)
            db.execute("INSERT INTO decisions(id,as_of,symbol,call,executor,payload) VALUES(?,?,?,?,?,?)",
                       (decision["decision_id"],decision["as_of"],decision["symbol"],decision["call"],executor,canonical(decision)))
            self._event(db,now,"DECISION",decision["decision_id"],decision)
            if decision["call"] in {"LONG","SHORT"} and executor=="native":
                intent=decision["intent"]
                db.execute("""INSERT INTO orders(decision_id,symbol,side,provenance,quantity,planned_quantity,intended_entry,
                    stop,target,notional,risk_cash,fee_bps,slippage_bps,status,approved_at,expires_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,?)""",
                    (decision["decision_id"],decision["symbol"],intent["side"],decision["provenance"],intent["quantity"],intent["quantity"],intent["entry"],
                     intent["stop"],intent["target"],intent["notional"],intent["risk_cash"],intent["fee_bps"],intent["slippage_bps"],
                     decision["approved_at"],decision["expires_at"]))
                self._event(db,now,"PAPER_ORDER_PENDING",decision["decision_id"],{"fill_status":"not_filled","provenance":decision["provenance"]})
            db.commit()
        return decision

    def orders(self):
        with self._connection() as db:return [dict(row) for row in db.execute("SELECT * FROM orders ORDER BY id")]

    def events(self):
        with self._connection() as db:
            return [{**dict(row),"payload":json.loads(row["payload"])} for row in db.execute("SELECT * FROM events ORDER BY sequence")]

    def _cancel(self,db,order,when,reason):
        db.execute("UPDATE orders SET status='CANCELLED',closed_at=?,exit_reason=? WHERE id=?",(iso(when),reason,order["id"]))
        self._event(db,when,"PAPER_ORDER_CANCELLED",order["decision_id"],{"reason":reason})

    def _fill(self,db,order,bar):
        when=utc(bar["open_time"])
        if when < utc(order["approved_at"]):return None
        if when >= utc(order["expires_at"]):
            self._cancel(db,order,when,"APPROVAL_EXPIRED");return None
        account=self._account(db,when)
        if account["daily_pnl"] <= -self.config.initial_equity*self.config.daily_loss_fraction:
            self._cancel(db,order,when,"DAILY_LOSS_LIMIT");return None
        long=order["side"]=="LONG"
        slip=order["slippage_bps"]/10000
        fill=bar["open"]*(1+slip if long else 1-slip)
        if abs(fill-order["intended_entry"])/order["intended_entry"] > 0.001:
            self._cancel(db,order,when,"ENTRY_PRICE_MOVED");return None
        if not ((order["stop"]<fill<order["target"]) if long else (order["target"]<fill<order["stop"])):
            self._cancel(db,order,when,"FILL_OUTSIDE_BRACKET");return None
        friction=fill*2*(order["fee_bps"]+order["slippage_bps"])/10000
        unit_risk=abs(fill-order["stop"])+friction
        ratio=(abs(order["target"]-fill)-friction)/unit_risk
        if ratio<self.config.min_reward_risk:
            self._cancel(db,order,when,"FILL_REWARD_RISK");return None
        other_notional=account["open_notional"]-order["notional"]
        quantity=min(order["quantity"],order["risk_cash"]/unit_risk,
                     account["equity"]*self.config.risk_fraction/unit_risk,
                     account["equity"]*self.config.max_notional_fraction/fill,
                     max(0,account["equity"]*self.config.max_total_notional_fraction-other_notional)/fill)
        if quantity<=0:
            self._cancel(db,order,when,"FILL_EXPOSURE_LIMIT");return None
        fees=quantity*fill*order["fee_bps"]/10000
        db.execute("UPDATE orders SET status='OPEN',entry=?,quantity=?,notional=?,risk_cash=?,fees=?,opened_at=? WHERE id=?",
                   (fill,quantity,quantity*fill,quantity*unit_risk,fees,iso(when),order["id"]))
        db.execute("INSERT INTO cashflows(occurred_at,kind,amount,order_id) VALUES(?,'ENTRY_FEE',?,?)",(iso(when),-fees,order["id"]))
        self._event(db,when,"PAPER_FILL",order["decision_id"],{"price":fill,"quantity":quantity,"fees":fees,"provenance":order["provenance"]})
        return dict(db.execute("SELECT * FROM orders WHERE id=?",(order["id"],)).fetchone())

    def _settle(self,db,order,bar):
        if utc(bar["open_time"]) < utc(order["opened_at"]):return
        long=order["side"]=="LONG"
        stop_hit=bar["low"]<=order["stop"] if long else bar["high"]>=order["stop"]
        target_hit=bar["high"]>=order["target"] if long else bar["low"]<=order["target"]
        if not stop_hit and not target_hit:return
        if stop_hit:
            raw=min(bar["open"],order["stop"]) if long else max(bar["open"],order["stop"])
            reason="STOP_LOSS"
        else:
            # Do not credit favourable gap improvement to a target limit order.
            raw=order["target"];reason="TAKE_PROFIT"
        price=raw*(1-order["slippage_bps"]/10000 if long else 1+order["slippage_bps"]/10000)
        exit_fee=price*order["quantity"]*order["fee_bps"]/10000
        fees=order["fees"]+exit_fee
        pnl=(price-order["entry"])*order["quantity"]*(1 if long else -1)-fees
        when=bar["close_time"]
        db.execute("UPDATE orders SET status='CLOSED',closed_at=?,exit_price=?,fees=?,net_pnl=?,exit_reason=? WHERE id=?",
                   (iso(when),price,fees,pnl,reason,order["id"]))
        db.execute("INSERT INTO cashflows(occurred_at,kind,amount,order_id) VALUES(?,'EXIT_SETTLEMENT',?,?)",
                   (iso(when),pnl+order["fees"],order["id"]))
        self._event(db,when,"PAPER_CLOSE",order["decision_id"],{"price":price,"fees":fees,"net_pnl":pnl,"reason":reason,
                    "intrabar_ambiguous":bool(stop_hit and target_hit),"provenance":order["provenance"]})

    def advance(self,instrument,bar,*,observed_at,provenance,timeframe="15m"):
        symbol(instrument)
        if provenance not in {"market","historical","synthetic"}:raise ValueError("invalid bar provenance")
        intervals={"1m":60,"5m":300,"15m":900}
        if timeframe not in intervals:raise ValueError("unsupported execution interval")
        bar=validate_bar(bar,as_of=observed_at,interval_seconds=intervals[timeframe])
        digest=hashlib.sha256(canonical({"bar":bar,"provenance":provenance}).encode()).hexdigest()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old=db.execute("SELECT hash FROM processed_bars WHERE symbol=? AND open_time=?",(instrument,bar["open_time"])).fetchone()
            if old:
                if old[0]!=digest:raise ValueError("the same observed bar changed; start a separate experiment")
                db.commit();return
            self._clock(db,observed_at)
            self._event_clock(db,bar["close_time"])
            self._bind(db,"provenance",provenance)
            latest=db.execute("SELECT close_time FROM processed_bars WHERE symbol=? ORDER BY open_time DESC LIMIT 1",(instrument,)).fetchone()
            if latest and utc(bar["open_time"])<utc(latest[0]):raise ValueError("out-of-order paper settlement")
            orders=[dict(row) for row in db.execute("SELECT * FROM orders WHERE symbol=? AND status IN ('OPEN','PENDING') ORDER BY id",(instrument,))]
            if latest and any(order["status"]=="OPEN" for order in orders) and utc(bar["open_time"])!=utc(latest[0]):
                raise ValueError("missing execution bars for an open position; supply the missing observations first")
            if any(order["provenance"]!=provenance for order in orders):
                raise ValueError("bar and order provenance differ")
            for order in orders:
                if order["status"]=="PENDING":order=self._fill(db,order,bar)
                if order and order["status"]=="OPEN":self._settle(db,order,bar)
            db.execute("INSERT INTO processed_bars(symbol,open_time,close_time,provenance,hash) VALUES(?,?,?,?,?)",
                       (instrument,bar["open_time"],bar["close_time"],provenance,digest))
            db.commit()
