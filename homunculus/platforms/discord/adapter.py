"""Discord platform adapter.

Implements ``PlatformAdapter`` for Discord using SQLite-backed
message storage for reliability.  Supports per-channel context
isolation (DM vs guild channels).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from ..base import PlatformAdapter
from ...core.lock import LockManager
from ...core.memory import MemoryManager

logger = logging.getLogger(__name__)


class DiscordAdapter(PlatformAdapter):
    """Connects the task engine to Discord via SQLite message queue."""

    def __init__(self, base_dir: str):
        self._base_dir = base_dir
        self._lock = LockManager(base_dir)
        self._memory = MemoryManager(base_dir)
        self._store = None  # lazy init

    def _get_store(self):
        if self._store is None:
            from ...core.store import MessageStore
            self._store = MessageStore(self._base_dir)
        return self._store

    # ── PlatformAdapter interface ──

    def fetch_pending(self, ctx: str = "dm") -> list[dict]:
        """Fetch unprocessed messages for a given context."""
        info = self._lock.status(ctx)
        if info and not info.get("stale"):
            logger.info("[%s] Lock held — skipping.", ctx)
            return []
        if info and info.get("stale"):
            logger.warning("[%s] Stale lock — releasing.", ctx)
            self._lock.release(ctx)

        store = self._get_store()
        rows = store.fetch_pending(context=ctx)
        if not rows:
            return []

        return [
            {
                "msg_id": r["message_id"],
                "instruction": r["text"],
                "chat_id": r["channel_id"],
                "channel_id": r["channel_id"],
                "channel_type": r.get("channel_type", "dm"),
                "channel_name": r.get("channel_name", ""),
                "timestamp": r["timestamp"],
                "user_name": r.get("user_name", ""),
                "files": r.get("files", []),
                "stale_resume": False,
            }
            for r in rows
        ]

    def get_pending_contexts(self) -> list[str]:
        """Return context identifiers that have pending work."""
        return self._get_store().get_pending_contexts()

    def send_text(self, chat_id: int, text: str) -> bool:
        from .sender import send_message_sync
        return send_message_sync(chat_id, text)

    def send_files(self, chat_id: int, text: str, paths: list[str]) -> bool:
        from .sender import send_files_sync
        return send_files_sync(chat_id, text, paths)

    def deliver_result(
        self,
        instruction: str,
        result: str,
        chat_id: int,
        timestamps: list[str],
        msg_ids: list[int],
        *,
        files: list[str] | None = None,
        ctx: str | None = None,
    ) -> None:
        from .sender import send_files_sync

        body = f"**Task Complete**\n\n**Result:**\n{result}"
        if files:
            names = [os.path.basename(f) for f in files]
            body += f"\n\n**Files:** {', '.join(names)}"
        if len(msg_ids) > 1:
            body += f"\n\n_{len(msg_ids)} requests merged_"

        ok = send_files_sync(chat_id, body, files or [])

        if not ok:
            result = f"[send failed] {result}"
            files = []

        # Save bot response to SQLite.
        store = self._get_store()
        store.save_bot_response(
            channel_id=chat_id,
            context=ctx or "dm",
            text=body,
            reply_to_ids=msg_ids,
            files=[os.path.basename(f) for f in (files or [])],
        )

        # Persist to workspace.
        primary = msg_ids[0]
        self._memory.upsert(
            primary, instruction, result=result[:200],
            files=[os.path.basename(f) for f in (files or [])],
            chat_id=chat_id, timestamp=timestamps[0], ctx=ctx,
        )

    def mark_completed(self, msg_ids: list[int]) -> None:
        self._get_store().mark_done_batch(msg_ids)
        logger.info("Marked %d Discord message(s) done.", len(msg_ids))

    def mark_failed(self, msg_ids: list[int], error: str) -> None:
        """Mark messages as failed (retries if under threshold)."""
        store = self._get_store()
        for mid in msg_ids:
            store.mark_failed(mid, error)

    def recover_stale(self, timeout_min: int = 30) -> int:
        """Recover messages stuck in 'processing' state."""
        return self._get_store().recover_stale(timeout_min)
