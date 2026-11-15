"""Durable metadata-only outbox; never reconstruct an event during retry.

A saved request survives process/source changes. Its original camera/context is
immutable. This is not persistence of person identity, clips, or event duration.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

import httpx


class EventQueueFull(RuntimeError):
    pass


class EventSaveOutbox:
    def __init__(self, path, *, camera_id, limit=256):
        if not isinstance(camera_id, str) or not camera_id or type(limit) is not int or limit < 1:
            raise ValueError("outbox configuration")
        self.camera_id, self.limit = camera_id, limit
        if str(path) != ":memory:":
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS binding(camera TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS requests(
                event_id TEXT PRIMARY KEY, body TEXT NOT NULL, digest TEXT NOT NULL,
                state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_retry REAL NOT NULL DEFAULT 0, error TEXT);
        """)
        previous = self.db.execute("SELECT camera FROM binding").fetchone()
        if previous and previous[0] != camera_id:
            self.db.close()
            raise ValueError("outbox camera mismatch")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO binding VALUES(?)", (camera_id,))

    def enqueue(self, event):
        if (not isinstance(event, dict) or event.get("camera_id") != self.camera_id
                or not re.fullmatch(r"EVT_[0-9a-f]{32}", event.get("event_id", ""))):
            raise ValueError("outbox event identity")
        body = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(body.encode()) > 65536:
            raise ValueError("event metadata too large")
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self.db:
            previous = self.db.execute("SELECT body FROM requests WHERE event_id=?",
                                       (event["event_id"],)).fetchone()
            if previous:
                if previous[0] != body:
                    raise ValueError("immutable event payload conflict")
                return
            if self.db.execute("SELECT count(*) FROM requests").fetchone()[0] >= self.limit:
                raise EventQueueFull("event metadata outbox full; pending requests retained")
            self.db.execute("INSERT INTO requests(event_id,body,digest,state) VALUES(?,?,?,'pending')",
                            (event["event_id"], body, digest))

    def acknowledge(self, event_id):
        with self.db:
            self.db.execute("DELETE FROM requests WHERE event_id=?", (event_id,))

    def state(self, event_id):
        row = self.db.execute("SELECT state FROM requests WHERE event_id=?", (event_id,)).fetchone()
        return row[0] if row else None

    def counts(self):
        return dict(self.db.execute("SELECT state,count(*) FROM requests GROUP BY state"))

    def pump(self, submit, *, now=None, batch_limit=1):
        if type(batch_limit) is not int or batch_limit < 1:
            raise ValueError("batch_limit")
        now = time.time() if now is None else now
        if not math.isfinite(now):
            raise ValueError("retry time")
        rows = self.db.execute(
            "SELECT event_id,body,digest,attempts FROM requests "
            "WHERE state='pending' AND next_retry<=? ORDER BY next_retry,rowid LIMIT ?",
            (now, batch_limit)).fetchall()
        outcomes = []
        for event_id, body, digest, attempts in rows:
            state, error = "pending", None
            if hashlib.sha256(body.encode()).hexdigest() != digest:
                state, error = "rejected", "body_integrity"
            else:
                try:
                    result = submit(json.loads(body))
                    if not isinstance(result, dict) or result.get("event_id") != event_id:
                        error = "response_binding"
                    else:
                        self.acknowledge(event_id)
                        outcomes.append((event_id, "saved"))
                        continue
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    error = "http_" + str(status)
                    if status not in (429, 500, 502, 503, 504):
                        state = "rejected"
                    if status in (401, 403):
                        raise  # Retain the request; revoked credentials stop the runner.
                except httpx.TransportError:
                    error = "transport"
                except json.JSONDecodeError:
                    error = "response_json"
            delay = min(30, 2 ** min(attempts, 5))
            with self.db:
                self.db.execute(
                    "UPDATE requests SET state=?,attempts=attempts+1,next_retry=?,error=? WHERE event_id=?",
                    (state, now+delay, error, event_id))
            outcomes.append((event_id, error))
        return outcomes

    def close(self):
        self.db.close()
