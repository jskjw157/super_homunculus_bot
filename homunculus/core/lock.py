"""Distributed lock manager for preventing concurrent task execution.

Uses file-based locking with atomic creation (O_EXCL) and staleness
detection via heartbeat timestamps. Each platform context gets an
isolated lock file to avoid cross-channel interference.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# Staleness threshold — if no heartbeat for this long, the lock is stale.
STALE_TIMEOUT_SEC = 1800  # 30 minutes

# Only allow safe context identifiers (prevents path traversal).
_CTX_PATTERN = re.compile(r"^(dm|telegram|ch_\d{1,20})$", re.ASCII)


class LockManager:
    """File-based mutual exclusion for task processing.

    Supports per-context isolation so that independent channels
    never block each other.

    Usage::

        lm = LockManager("/project/root")
        if lm.acquire(msg_ids=[42], summary="do stuff", context="dm"):
            try:
                ...  # do work
                lm.heartbeat(context="dm")  # keep alive
            finally:
                lm.release(context="dm")
    """

    DEFAULT_LOCK = "working.json"

    def __init__(self, base_dir: str):
        self._base_dir = base_dir
        self._workspace = os.path.join(base_dir, "workspace")

    # ── path helpers ──

    @staticmethod
    def _validate_ctx(ctx: str) -> None:
        if not _CTX_PATTERN.match(ctx):
            raise ValueError(f"Invalid context identifier: {ctx!r}")

    def _lock_path(self, ctx: str | None = None) -> str:
        if not ctx:
            return os.path.join(self._base_dir, self.DEFAULT_LOCK)
        self._validate_ctx(ctx)
        return os.path.join(self._workspace, ctx, self.DEFAULT_LOCK)

    # ── public API ──

    def status(self, ctx: str | None = None) -> dict | None:
        """Return current lock info, or *None* if unlocked.

        If the lock is stale (no heartbeat within *STALE_TIMEOUT_SEC*),
        the returned dict includes ``"stale": True``.
        """
        path = self._lock_path(ctx)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                info = json.load(fh)
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning("Lock file unreadable (%s): %s", path, exc)
            return None

        ts_str = info.get("last_heartbeat") or info.get("acquired_at", "")
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            idle = (datetime.now() - ts).total_seconds()
            if idle > STALE_TIMEOUT_SEC:
                info["stale"] = True
                logger.warning(
                    "Stale lock detected (idle %d min): %s",
                    int(idle / 60),
                    info.get("summary", "?"),
                )
        except (ValueError, TypeError):
            age = time.time() - os.path.getmtime(path)
            if age > STALE_TIMEOUT_SEC:
                self._remove(path)
                return None
        return info

    def acquire(
        self,
        msg_ids: list[int],
        summary: str,
        ctx: str | None = None,
    ) -> bool:
        """Try to atomically create the lock file.

        Returns *True* on success, *False* if another task holds the lock.
        """
        path = self._lock_path(ctx)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "msg_ids": msg_ids,
            "summary": summary[:80],
            "acquired_at": now_str,
            "last_heartbeat": now_str,
            "pid": os.getpid(),
            "context": ctx or "",
        }
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            try:
                os.write(fd, json.dumps(payload, ensure_ascii=False, indent=2).encode())
            finally:
                os.close(fd)
            logger.info("Lock acquired: msg_ids=%s", msg_ids)
            return True
        except FileExistsError:
            logger.info("Lock already held — cannot acquire.")
            return False
        except OSError as exc:
            logger.error("Lock acquire failed: %s", exc)
            return False

    def heartbeat(self, ctx: str | None = None) -> None:
        """Update *last_heartbeat* to signal the task is still alive."""
        path = self._lock_path(ctx)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        data["last_heartbeat"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._atomic_write(path, data)

    def release(self, ctx: str | None = None) -> None:
        """Remove the lock file."""
        self._remove(self._lock_path(ctx))
        logger.info("Lock released (ctx=%s).", ctx or "default")

    # ── internals ──

    @staticmethod
    def _remove(path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    @staticmethod
    def _atomic_write(path: str, data: dict) -> None:
        dir_ = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
