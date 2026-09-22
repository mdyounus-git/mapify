"""A small on-disk cache for outbound lookups.

Every external request we make is cacheable and idempotent, and caching them
is not just a speed-up: it is how we stay inside the OpenStreetMap usage
policies when a user re-analyses the same image.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lookups (
    key        TEXT PRIMARY KEY,
    namespace  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS lookups_ns ON lookups(namespace);
"""


class LookupCache:
    """Thread-safe key/value cache with per-entry expiry."""

    def __init__(self, path: str | Path, ttl_seconds: float = 30 * 24 * 3600):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @staticmethod
    def key_for(namespace: str, **parts: Any) -> str:
        blob = json.dumps(parts, sort_keys=True, default=str)
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
        return f"{namespace}:{digest}"

    def get(self, key: str) -> Any | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, created_at FROM lookups WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        payload, created = row
        if self.ttl and time.time() - created > self.ttl:
            self.delete(key)
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def put(self, key: str, value: Any) -> None:
        namespace = key.split(":", 1)[0]
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO lookups (key, namespace, payload, created_at)"
                " VALUES (?, ?, ?, ?)",
                (key, namespace, json.dumps(value), time.time()),
            )
            self._conn.commit()

    def delete(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM lookups WHERE key = ?", (key,))
            self._conn.commit()

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT namespace, COUNT(*) FROM lookups GROUP BY namespace"
            ).fetchall()
        return {ns: n for ns, n in rows}

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM lookups")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class RateLimiter:
    """A shared minimum-interval gate.

    OpenStreetMap's Nominatim policy is an absolute maximum of one request per
    second from a single application. This enforces that across threads rather
    than trusting call sites to sleep.
    """

    def __init__(self, min_interval_s: float):
        self.min_interval = max(0.0, min_interval_s)
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self) -> float:
        """Block until the next call is allowed. Returns seconds waited."""
        with self._lock:
            now = time.monotonic()
            wait = self._last + self.min_interval - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            else:
                wait = 0.0
            self._last = now
            return wait
