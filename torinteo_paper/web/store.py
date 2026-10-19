"""Opaque experiment storage over the unchanged transactional paper ledger."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from ..demo_data import demo_snapshot
from ..gate import canonical
from ..ledger import PaperLedger
from ..models import RiskConfig, iso, utc


class ExperimentNotFound(ValueError):
    pass


class ExperimentStore:
    def __init__(self, data_dir, *, clock):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._locks = {}
        self._registry_lock = threading.RLock()

    def directory(self, identifier):
        try:
            if not isinstance(identifier, str) or str(uuid.UUID(identifier)) != identifier:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise ExperimentNotFound("Experiment not found.") from exc
        path = self.root / identifier
        if not path.is_dir() or path.is_symlink() or (path / "ledger.sqlite3").is_symlink():
            raise ExperimentNotFound("Experiment not found.")
        return path

    @contextmanager
    def locked(self, identifier):
        path = self.directory(identifier)
        with self._registry_lock:
            lock = self._locks.setdefault(identifier, threading.RLock())
        with lock:
            # POSIX file locking serializes web actions across local workers.
            # Core writes independently retain their SQLite BEGIN IMMEDIATE lock.
            fd = os.open(path / "web.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                try:
                    import fcntl
                except ImportError:
                    fcntl = None
                if fcntl:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                if fcntl:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _connection(self, identifier):
        path = self.directory(identifier) / "ledger.sqlite3"
        if not path.is_file():
            raise ExperimentNotFound("Experiment not found.")
        return sqlite3.connect(path, timeout=15)

    def create(self, name, kind, config=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(not c.isprintable() for c in name):
            raise ValueError("Experiment name must contain 1 to 80 printable characters.")
        if kind not in {"synthetic", "market", "historical"}:
            raise ValueError("Experiment kind must be synthetic, market or historical.")
        if config is not None and not isinstance(config, dict):
            raise ValueError("Paper configuration must be an object.")
        if any(isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) > 1e12
               for value in (config or {}).values()):
            raise ValueError("Paper settings exceed the supported numerical range.")
        try:
            risk = RiskConfig(**(config or {}))
        except TypeError as exc:
            raise ValueError("Paper configuration contains unsupported fields or values.") from exc
        with self._registry_lock:
            if len(self.list()) >= 250:
                raise ValueError("This owner workspace supports at most 250 experiments.")
            identifier = str(uuid.uuid4())
            directory = self.root / identifier
            directory.mkdir(mode=0o700)
            ledger = PaperLedger(directory / "ledger.sqlite3", risk)
            os.chmod(ledger.path, 0o600)
            result = {"id": identifier, "name": name.strip(), "kind": kind,
                      "created_at": iso(self.clock()), "config": risk.to_dict()}
            prepared = demo_snapshot() if kind == "synthetic" and risk == RiskConfig() else None
            with self._connection(identifier) as db:
                db.executescript("""
                    CREATE TABLE web_experiment(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
                    CREATE TABLE web_snapshot(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
                    CREATE TABLE web_flags(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                """)
                db.execute("INSERT INTO web_experiment(id,payload) VALUES(1,?)", (canonical(result),))
                if prepared:
                    db.execute("INSERT INTO web_snapshot(id,payload) VALUES(1,?)", (canonical(prepared),))
            return result

    def metadata(self, identifier):
        with self._connection(identifier) as db:
            try:
                row = db.execute("SELECT payload FROM web_experiment WHERE id=1").fetchone()
            except sqlite3.OperationalError as exc:
                raise ExperimentNotFound("Experiment not found.") from exc
        if not row:
            raise ExperimentNotFound("Experiment not found.")
        return json.loads(row[0])

    def list(self):
        experiments = []
        for path in self.root.iterdir():
            if not path.is_dir() or path.is_symlink():
                continue
            try:
                experiments.append(self.metadata(path.name))
            except (ExperimentNotFound, sqlite3.Error):
                continue
        return sorted(experiments, key=lambda item: (item["created_at"], item["id"]), reverse=True)

    def ledger(self, identifier):
        metadata = self.metadata(identifier)
        return PaperLedger(self.directory(identifier) / "ledger.sqlite3", RiskConfig(**metadata["config"]))

    def snapshot(self, identifier):
        with self._connection(identifier) as db:
            row = db.execute("SELECT payload FROM web_snapshot WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def save_snapshot(self, identifier, value, *, custom=False):
        with self._connection(identifier) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT payload FROM web_snapshot WHERE id=1").fetchone()
            replace = previous is None
            if previous:
                saved = json.loads(previous[0])
                incoming_at, saved_at = utc(value["as_of"]), utc(saved["as_of"])
                replace = incoming_at > saved_at
                if incoming_at == saved_at:
                    settings = json.loads(db.execute("SELECT payload FROM web_experiment WHERE id=1").fetchone()[0])["config"]

                    def recorded_order(snapshot):
                        # This is the core's immutable snapshot/config identity.
                        digest = hashlib.sha256(canonical({"snapshot": snapshot, "config": settings}).encode()).hexdigest()
                        row = db.execute("SELECT rowid FROM decisions WHERE id=?", (digest,)).fetchone()
                        return row[0] if row else 0

                    # Equal observation times follow original decision order,
                    # never the arrival order of retries. An unrecorded draft
                    # cannot displace recorded evidence at the same instant.
                    replace = recorded_order(value) > recorded_order(saved)
            if replace:
                db.execute("INSERT INTO web_snapshot(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                           (canonical(value),))
            if custom:
                db.execute("INSERT OR REPLACE INTO web_flags(key,value) VALUES('custom','1')")

    def mark_custom(self, identifier):
        with self._connection(identifier) as db:
            db.execute("INSERT OR REPLACE INTO web_flags(key,value) VALUES('custom','1')")

    def detail(self, identifier):
        experiment = self.metadata(identifier)
        ledger = self.ledger(identifier)
        with self._connection(identifier) as db:
            metadata = dict(db.execute("SELECT key,value FROM metadata"))
            decisions = [json.loads(row[0]) for row in db.execute("SELECT payload FROM decisions ORDER BY rowid")]
            custom = db.execute("SELECT value FROM web_flags WHERE key='custom'").fetchone() is not None
        snapshot = self.snapshot(identifier)
        at = utc(metadata.get("event_clock") or (snapshot or {}).get("as_of") or self.clock())
        orders, events = ledger.orders(), ledger.events()
        demo = {"enabled": False, "stage": "unavailable", "next_step": None, "available_steps": []}
        if experiment["kind"] == "synthetic" and experiment["config"] == RiskConfig().to_dict() and not custom:
            if not decisions:
                stage = "prepared" if snapshot else "empty"
            elif len(decisions) == 1 and len(orders) == 1:
                stage = {"PENDING": "evaluated", "OPEN": "filled", "CLOSED": "closed"}.get(orders[0]["status"], "unavailable")
            elif len(decisions) == 2 and len(orders) == 1 and orders[0]["status"] == "CLOSED" and decisions[-1]["call"] == "NO_TRADE":
                stage = "blocked"
            else:
                stage = "unavailable"
            next_step = {"empty": "prepare", "prepared": "evaluate", "evaluated": "fill", "filled": "close", "closed": "blocked"}.get(stage)
            demo = {"enabled": stage != "unavailable", "stage": stage,
                    "next_step": next_step, "available_steps": [next_step] if next_step else [],
                    "description": "Invented TEST/USD software demonstration; not a market backtest."}
        return {"experiment": experiment, "mode": "paper", "as_of": iso(at),
                "account": {**ledger.account(at), "currency": ledger.config.account_currency},
                "orders": orders, "decisions": decisions, "events": events,
                "latest_snapshot": snapshot, "demo": demo}
