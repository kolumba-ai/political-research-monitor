"""Pipeline bookkeeping that does not belong in the knowledge graph itself:
stage completion (for incremental refresh), unresolved claims, human reviews, monitors and their events."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ids import utcnow


class PipelineState:
    def __init__(self, path: Path | str):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS stage_runs (country TEXT, stage TEXT, completed_at TEXT, status TEXT,
                    summary TEXT, PRIMARY KEY (country, stage));
                CREATE TABLE IF NOT EXISTS pipeline_runs (run_id TEXT PRIMARY KEY, country TEXT, mode TEXT,
                    started_at TEXT, finished_at TEXT, status TEXT, summary TEXT);
                CREATE TABLE IF NOT EXISTS claims (id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT, stage TEXT,
                    subject TEXT, claim TEXT, status TEXT, reason TEXT, urls TEXT, run_id TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS reviews (country TEXT, check_name TEXT, decision TEXT, reviewer TEXT,
                    note TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS monitors (monitor_id TEXT PRIMARY KEY, country TEXT, topic TEXT,
                    query TEXT, webhook_secret TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS monitor_events (id INTEGER PRIMARY KEY AUTOINCREMENT, monitor_id TEXT,
                    received_at TEXT, payload TEXT, status TEXT, note TEXT);
                """
            )

    # ---- stages ----
    def stage_fresh(self, country: str, stage: str, ttl_days: int) -> bool:
        with self._lock:
            row = self._db.execute("SELECT completed_at, status FROM stage_runs WHERE country=? AND stage=?",
                                   (country, stage)).fetchone()
        if not row or row[1] != "ok":
            return False
        return datetime.now(timezone.utc) - datetime.fromisoformat(row[0]) < timedelta(days=ttl_days)

    def mark_stage(self, country: str, stage: str, status: str, summary: dict) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO stage_runs VALUES (?,?,?,?,?)",
                             (country, stage, utcnow(), status, json.dumps(summary, default=str)))

    def stages(self, country: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT stage, completed_at, status, summary FROM stage_runs WHERE country=?",
                                    (country,)).fetchall()
        return [{"stage": r[0], "completed_at": r[1], "status": r[2], "summary": json.loads(r[3])} for r in rows]

    # ---- runs ----
    def start_run(self, run_id: str, country: str, mode: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO pipeline_runs VALUES (?,?,?,?,?,?,?)",
                             (run_id, country, mode, utcnow(), None, "running", None))

    def finish_run(self, run_id: str, status: str, summary: dict) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE pipeline_runs SET finished_at=?, status=?, summary=? WHERE run_id=?",
                             (utcnow(), status, json.dumps(summary, default=str), run_id))

    def last_run(self, country: str | None = None) -> dict | None:
        q = "SELECT run_id, country, mode, started_at, finished_at, status, summary FROM pipeline_runs"
        args: tuple = ()
        if country:
            q += " WHERE country=?"
            args = (country,)
        with self._lock:
            r = self._db.execute(q + " ORDER BY started_at DESC LIMIT 1", args).fetchone()
        if not r:
            return None
        return {"run_id": r[0], "country": r[1], "mode": r[2], "started_at": r[3], "finished_at": r[4],
                "status": r[5], "summary": json.loads(r[6]) if r[6] else None}

    # ---- claims ----
    def record_claim(self, country: str, stage: str, subject: str, claim: dict, status: str, reason: str,
                     urls: list[str], run_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO claims (country, stage, subject, claim, status, reason, urls, run_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (country, stage, subject, json.dumps(claim, default=str), status, reason, json.dumps(urls), run_id,
                 utcnow()),
            )

    def claims(self, country: str, run_id: str | None = None) -> list[dict]:
        q = "SELECT stage, subject, claim, status, reason, urls, run_id, created_at FROM claims WHERE country=?"
        args: tuple = (country,)
        if run_id:
            q += " AND run_id=?"
            args += (run_id,)
        with self._lock:
            rows = self._db.execute(q + " ORDER BY id", args).fetchall()
        return [{"stage": r[0], "subject": r[1], "claim": json.loads(r[2]), "status": r[3], "reason": r[4],
                 "urls": json.loads(r[5]), "run_id": r[6], "created_at": r[7]} for r in rows]

    # ---- reviews ----
    def add_review(self, country: str, check: str, decision: str, reviewer: str, note: str = "") -> None:
        if decision not in ("approve", "reject"):
            raise ValueError("decision must be approve|reject")
        with self._lock, self._db:
            self._db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?)",
                             (country, check, decision, reviewer, note, utcnow()))

    def latest_review(self, country: str, check: str) -> dict | None:
        with self._lock:
            r = self._db.execute(
                "SELECT decision, reviewer, note, created_at FROM reviews WHERE country=? AND check_name=? "
                "ORDER BY created_at DESC LIMIT 1", (country, check)).fetchone()
        return {"decision": r[0], "reviewer": r[1], "note": r[2], "created_at": r[3]} if r else None

    # ---- monitors ----
    def add_monitor(self, monitor_id: str, country: str, topic: str, query: str, secret: str | None) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO monitors VALUES (?,?,?,?,?,?)",
                             (monitor_id, country, topic, query, secret, utcnow()))

    def monitors(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT monitor_id, country, topic, query, webhook_secret, created_at FROM monitors"
                                    ).fetchall()
        return [{"monitor_id": r[0], "country": r[1], "topic": r[2], "query": r[3], "webhook_secret": r[4],
                 "created_at": r[5]} for r in rows]

    def add_event(self, monitor_id: str, payload: dict, status: str = "pending", note: str = "") -> int:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO monitor_events (monitor_id, received_at, payload, status, note) "
                                   "VALUES (?,?,?,?,?)", (monitor_id, utcnow(), json.dumps(payload), status, note))
            return int(cur.lastrowid)

    def events(self, status: str | None = None) -> list[dict]:
        q = "SELECT id, monitor_id, received_at, payload, status, note FROM monitor_events"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        with self._lock:
            rows = self._db.execute(q + " ORDER BY id", args).fetchall()
        return [{"id": r[0], "monitor_id": r[1], "received_at": r[2], "payload": json.loads(r[3]), "status": r[4],
                 "note": r[5]} for r in rows]

    def set_event_status(self, event_id: int, status: str, note: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE monitor_events SET status=?, note=? WHERE id=?", (status, note, event_id))
