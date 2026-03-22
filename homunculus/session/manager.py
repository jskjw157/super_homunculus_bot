"""Multi-platform session lifecycle manager.

Tracks Claude AI sessions per platform context, enabling conversation
continuity across bot restarts. Sessions are persisted to disk and
restored on startup.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Metadata for an active AI session."""
    session_id: str
    context: str          # "dm", "ch_123"
    cwd: str = ""         # working directory
    created_at: str = ""
    last_used: str = ""


class SessionLifecycle:
    """Manages session persistence and restoration.

    Sessions are stored in a JSON file and keyed by context identifier.
    On process restart, sessions are restored from disk so the AI can
    resume prior conversations.

    Usage::

        sl = SessionLifecycle("/project/root")
        sl.save("dm", "session-uuid-here", cwd="/project")
        sid = sl.get("dm")  # returns "session-uuid-here"
    """

    STATE_FILE = "session_state.json"

    def __init__(self, base_dir: str):
        self._path = os.path.join(base_dir, self.STATE_FILE)
        self._cache: dict[str, SessionInfo] = {}
        self._load()

    def _load(self) -> None:
        """Restore sessions from disk."""
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for ctx, info in data.get("sessions", {}).items():
                self._cache[ctx] = SessionInfo(
                    session_id=info.get("session_id", ""),
                    context=ctx,
                    cwd=info.get("cwd", ""),
                    created_at=info.get("created_at", ""),
                    last_used=info.get("last_used", ""),
                )
            logger.info("Restored %d session(s) from disk.", len(self._cache))
        except Exception as exc:
            logger.warning("Session state load error: %s", exc)

    def _persist(self) -> None:
        """Write current state to disk."""
        data = {"sessions": {}}
        for ctx, info in self._cache.items():
            data["sessions"][ctx] = {
                "session_id": info.session_id,
                "cwd": info.cwd,
                "created_at": info.created_at,
                "last_used": info.last_used,
            }
        try:
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error("Session persist error: %s", exc)

    def get(self, ctx: str) -> str | None:
        """Get session ID for context, or None."""
        info = self._cache.get(ctx)
        return info.session_id if info else None

    def save(self, ctx: str, session_id: str, cwd: str = "") -> None:
        """Save or update a session."""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        existing = self._cache.get(ctx)
        self._cache[ctx] = SessionInfo(
            session_id=session_id,
            context=ctx,
            cwd=cwd,
            created_at=existing.created_at if existing else now,
            last_used=now,
        )
        self._persist()

    def remove(self, ctx: str) -> None:
        """Remove a session."""
        self._cache.pop(ctx, None)
        self._persist()

    def list_all(self) -> list[SessionInfo]:
        """Return all active sessions."""
        return list(self._cache.values())

    def clear(self) -> None:
        """Remove all sessions."""
        self._cache.clear()
        self._persist()
