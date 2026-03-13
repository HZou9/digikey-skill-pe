"""SQLite-based response cache for DigiKey API."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path


class Cache:
    """SQLite cache for API responses."""

    def __init__(self, db_path: str, default_ttl: int = 86400):
        self.db_path = db_path
        self.default_ttl = default_ttl
        self._init_db()

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON cache(expires_at)")

    @staticmethod
    def make_key(endpoint: str, params: dict) -> str:
        raw = f"{endpoint}:{json.dumps(params, sort_keys=True)}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT value FROM cache WHERE key = ? AND expires_at > ?",
                (key, time.time()),
            ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def set(self, key: str, value: dict, ttl: int | None = None):
        ttl = ttl or self.default_ttl
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (key, json.dumps(value), now + ttl, now),
            )

    def clear(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache")

    def cleanup(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            valid = conn.execute(
                "SELECT COUNT(*) FROM cache WHERE expires_at > ?", (time.time(),)
            ).fetchone()[0]
        return {"total_entries": total, "valid_entries": valid, "expired": total - valid}
