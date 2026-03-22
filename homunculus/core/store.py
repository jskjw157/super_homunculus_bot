"""SQLite-backed message queue store.

Thread-safe message persistence with atomic state transitions
(pending → processing → done/failed). Supports per-context
isolation, bot response logging, and stale message recovery.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
import threading
from datetime import datetime
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class InsertResult(Enum):
    INSERTED = "inserted"
    DUPLICATE = "duplicate"
    ERROR = "error"


class MessageStore:
    """SQLite message queue with thread-safe access.

    Uses WAL mode and a threading.Lock to safely support both
    the listener thread and the processing thread.
    """

    DB_NAME = "messages.db"

    def __init__(self, base_dir: str):
        db_path = os.path.join(base_dir, self.DB_NAME)
        is_new = not os.path.exists(db_path)
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                     isolation_level=None)
        self._conn.row_factory = sqlite3.Row

        if is_new:
            self._set_perms(db_path)
        self._init_db()

    def _set_perms(self, path: str) -> None:
        mode = stat.S_IRUSR | stat.S_IWUSR
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                os.chmod(p, mode)

    def _init_db(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._set_perms(self._path)

        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY,
                message_id      INTEGER UNIQUE NOT NULL,
                channel_id      INTEGER NOT NULL,
                context         TEXT NOT NULL DEFAULT 'dm',
                channel_type    TEXT DEFAULT 'dm',
                channel_name    TEXT DEFAULT '',
                user_id         INTEGER,
                user_name       TEXT DEFAULT '',
                text            TEXT DEFAULT '',
                files           TEXT DEFAULT '[]',
                timestamp       TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                retry_count     INTEGER DEFAULT 0,
                error_msg       TEXT DEFAULT '',
                created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_status_ctx ON messages(status, context);
            CREATE INDEX IF NOT EXISTS idx_channel ON messages(channel_id);

            CREATE TABLE IF NOT EXISTS bot_responses (
                id              INTEGER PRIMARY KEY,
                channel_id      INTEGER NOT NULL,
                context         TEXT NOT NULL DEFAULT 'dm',
                text            TEXT DEFAULT '',
                reply_to_ids    TEXT DEFAULT '[]',
                files           TEXT DEFAULT '[]',
                created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
        """)

    # ── insert ──

    def insert(self, *, message_id: int, channel_id: int, context: str,
               channel_type: str = "dm", channel_name: str = "",
               user_id: int = 0, user_name: str = "", text: str = "",
               files: list | None = None, timestamp: str = "") -> InsertResult:
        with self._lock:
            try:
                self._conn.execute(
                    """INSERT INTO messages
                       (message_id, channel_id, context, channel_type, channel_name,
                        user_id, user_name, text, files, timestamp)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (message_id, channel_id, context, channel_type, channel_name,
                     user_id, user_name, text,
                     json.dumps(files or [], ensure_ascii=False), timestamp),
                )
                return InsertResult.INSERTED
            except sqlite3.IntegrityError:
                return InsertResult.DUPLICATE
            except Exception as exc:
                logger.error("Insert error: %s", exc)
                return InsertResult.ERROR

    # ── fetch ──

    def fetch_pending(self, context: str = "dm", limit: int = 50) -> list[dict]:
        """Atomically transition pending → processing and return rows."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM messages
                   WHERE status='pending' AND context=?
                   ORDER BY message_id ASC LIMIT ?""",
                (context, limit),
            ).fetchall()
            if not rows:
                return []

            ids = [r["message_id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"""UPDATE messages SET status='processing',
                    updated_at=datetime('now','localtime')
                    WHERE message_id IN ({placeholders})""",
                ids,
            )

            return [self._row_to_dict(r) for r in rows]

    def get_pending_contexts(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT context FROM messages WHERE status='pending'"
            ).fetchall()
            return [r["context"] for r in rows]

    # ── status transitions ──

    def mark_done_batch(self, msg_ids: list[int]) -> None:
        with self._lock:
            ph = ",".join("?" * len(msg_ids))
            self._conn.execute(
                f"""UPDATE messages SET status='done',
                    updated_at=datetime('now','localtime')
                    WHERE message_id IN ({ph})""",
                msg_ids,
            )

    def mark_failed(self, msg_id: int, error: str) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT retry_count FROM messages WHERE message_id=?", (msg_id,)
            ).fetchone()
            if not row:
                return
            retries = (row["retry_count"] or 0) + 1
            new_status = "pending" if retries < 3 else "failed"
            self._conn.execute(
                """UPDATE messages SET status=?, retry_count=?, error_msg=?,
                   updated_at=datetime('now','localtime')
                   WHERE message_id=?""",
                (new_status, retries, error[:500], msg_id),
            )

    def recover_stale(self, timeout_min: int = 30) -> int:
        """Reset messages stuck in 'processing' for too long."""
        with self._lock:
            cur = self._conn.execute(
                """UPDATE messages SET status='pending',
                   updated_at=datetime('now','localtime')
                   WHERE status='processing'
                   AND updated_at < datetime('now','localtime',?)""",
                (f"-{timeout_min} minutes",),
            )
            return cur.rowcount

    # ── bot responses ──

    def save_bot_response(self, *, channel_id: int, context: str, text: str,
                          reply_to_ids: list[int],
                          files: list[str] | None = None) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO bot_responses
                   (channel_id, context, text, reply_to_ids, files)
                   VALUES (?,?,?,?,?)""",
                (channel_id, context, text,
                 json.dumps(reply_to_ids), json.dumps(files or [])),
            )

    # ── helpers ──

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for key in ("files",):
            if isinstance(d.get(key), str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = []
        return d

    def close(self) -> None:
        self._conn.close()


# Module-level singleton for convenience.
_instance: MessageStore | None = None


def get_store(base_dir: str | None = None) -> MessageStore:
    global _instance
    if _instance is None:
        if base_dir is None:
            base_dir = os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            ))
        _instance = MessageStore(base_dir)
    return _instance
