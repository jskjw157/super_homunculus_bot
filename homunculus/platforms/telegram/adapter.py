"""Telegram platform adapter.

Implements ``PlatformAdapter`` for Telegram Bot API using
the ``python-telegram-bot`` library.  Messages are stored in a
local JSON file and processed through the standard task pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

from ..base import PlatformAdapter
from ...core.memory import MemoryManager

logger = logging.getLogger(__name__)


class TelegramAdapter(PlatformAdapter):
    """Connects the task engine to Telegram Bot API."""

    MESSAGES_FILE = "telegram_messages.json"

    def __init__(self, base_dir: str):
        self._base_dir = base_dir
        self._msg_path = os.path.join(base_dir, self.MESSAGES_FILE)
        self._memory = MemoryManager(base_dir)

    # ── message persistence ──

    def _load_messages(self) -> dict:
        if not os.path.exists(self._msg_path):
            return {"messages": [], "last_update_id": 0}
        try:
            with open(self._msg_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            logger.warning("Message file read error: %s", exc)
            return {"messages": [], "last_update_id": 0}

    def _save_messages(self, data: dict) -> None:
        with open(self._msg_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def _save_response(self, chat_id: int, text: str, reply_ids: list[int],
                       files: list[str] | None = None) -> None:
        """Persist bot response for conversation context."""
        data = self._load_messages()
        data["messages"].append({
            "message_id": f"bot_{reply_ids[0]}",
            "type": "bot",
            "chat_id": chat_id,
            "text": text,
            "files": files or [],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reply_to": reply_ids,
            "processed": True,
        })
        self._save_messages(data)

    # ── PlatformAdapter interface ──

    def fetch_pending(self) -> list[dict]:
        """Return unprocessed user messages from the local JSON store."""
        from ...core.lock import LockManager

        lock = LockManager(self._base_dir)
        info = lock.status()
        if info and not info.get("stale"):
            logger.info("Active lock exists — skipping fetch.")
            return []
        if info and info.get("stale"):
            logger.warning("Stale lock detected — releasing for restart.")
            lock.release()

        # Poll Telegram API for new messages (if listener isn't running).
        self._poll_once()

        # Garbage-collect old processed messages (30+ days).
        self._cleanup_old()

        data = self._load_messages()
        messages = data.get("messages", [])

        pending = []
        for msg in messages:
            if msg.get("processed"):
                continue
            if msg.get("type") == "bot":
                continue
            pending.append({
                "msg_id": msg["message_id"],
                "instruction": msg.get("text", ""),
                "chat_id": msg["chat_id"],
                "timestamp": msg["timestamp"],
                "user_name": msg.get("first_name", msg.get("username", "")),
                "files": msg.get("files", []),
                "location": msg.get("location"),
            })

        return pending

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

        body = f"**Task Complete**\n\n{result}"
        if files:
            names = [os.path.basename(f) for f in files]
            body += f"\n\nFiles: {', '.join(names)}"
        if len(msg_ids) > 1:
            body += f"\n\n_{len(msg_ids)} requests merged_"

        ok = send_files_sync(chat_id, body, files or [])

        if ok:
            self._save_response(chat_id, body, msg_ids,
                                [os.path.basename(f) for f in (files or [])])
        else:
            result = f"[send failed] {result}"
            files = []

        # Persist to workspace manifest + index.
        primary = msg_ids[0]
        self._memory.upsert(
            primary, instruction, result=result[:200],
            files=[os.path.basename(f) for f in (files or [])],
            chat_id=chat_id, timestamp=timestamps[0], ctx=ctx,
        )

    def mark_completed(self, msg_ids: list[int]) -> None:
        data = self._load_messages()
        id_set = set(msg_ids)
        for msg in data["messages"]:
            if msg.get("message_id") in id_set:
                msg["processed"] = True
        self._save_messages(data)
        logger.info("Marked %d message(s) as completed.", len(msg_ids))

    # ── helpers ──

    def _poll_once(self) -> None:
        """Fetch new messages from Telegram API (single pass)."""
        try:
            from .sender import run_async_safe
            from .listener import fetch_new_messages
            run_async_safe(fetch_new_messages())
        except Exception as exc:
            logger.warning("Telegram poll error: %s", exc)

    def _cleanup_old(self, days: int = 30) -> None:
        """Remove processed messages older than *days*."""
        data = self._load_messages()
        cutoff = datetime.now() - timedelta(days=days)
        before = len(data["messages"])
        data["messages"] = [
            m for m in data["messages"]
            if not m.get("processed")
            or datetime.strptime(m["timestamp"], "%Y-%m-%d %H:%M:%S") > cutoff
        ]
        removed = before - len(data["messages"])
        if removed:
            self._save_messages(data)
            logger.info("Cleaned up %d old messages.", removed)
