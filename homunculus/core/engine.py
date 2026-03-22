"""Task orchestration engine.

Coordinates the lifecycle of a user request:
  1. Merge pending messages into a single work unit
  2. Acquire an execution lock
  3. Reserve workspace and memory
  4. Delegate to AI bridge
  5. Deliver results and release lock

Platform-agnostic: works identically for Telegram, Discord, or any
future adapter implementing ``PlatformAdapter``.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime

from .lock import LockManager
from .memory import MemoryManager

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TaskEngine:
    """Stateless orchestrator for task processing.

    Each public method handles one step; the caller (a platform-specific
    script) stitches them together.  This keeps the engine testable
    without any network I/O.
    """

    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or _BASE_DIR
        self.lock = LockManager(self.base_dir)
        self.memory = MemoryManager(self.base_dir)

    # ── Step 1: merge pending messages ──

    @staticmethod
    def merge_pending(tasks: list[dict]) -> dict | None:
        """Combine multiple pending messages into a single work unit.

        Args:
            tasks: List of dicts from ``PlatformAdapter.fetch_pending()``.
                Each must have: instruction, msg_id, chat_id, timestamp,
                user_name.  Optional: files, location, stale_resume.

        Returns:
            Merged work unit dict, or *None* if *tasks* is empty.
        """
        if not tasks:
            return None

        ordered = sorted(tasks, key=lambda t: t["timestamp"])
        is_resume = any(t.get("stale_resume") for t in ordered)

        parts: list[str] = []
        if is_resume:
            parts.extend([
                ">> Resuming interrupted work",
                "Check the workspace for prior results. "
                "Continue if safe, otherwise restart from scratch.",
                "", "---", "",
            ])

        all_files: list[dict] = []

        for idx, task in enumerate(ordered, 1):
            parts.append(f"[Request {idx}] ({task['timestamp']})")

            if task.get("instruction"):
                parts.append(task["instruction"])

            for f in task.get("files", []):
                name = os.path.basename(f["path"])
                size = _human_size(f.get("size", 0))
                parts.append(f"  Attachment: {name} ({size})")
                parts.append(f"    Path: {f['path']}")
                all_files.append(f)

            loc = task.get("location")
            if loc:
                parts.append(f"  Location: {loc['latitude']}, {loc['longitude']}")

            parts.append("")

        return {
            "instruction": "\n".join(parts).strip(),
            "msg_ids": list(dict.fromkeys(t["msg_id"] for t in ordered)),
            "chat_id": ordered[0]["chat_id"],
            "timestamp": ordered[0]["timestamp"],
            "user_name": ordered[0].get("user_name", "unknown"),
            "all_timestamps": [t["timestamp"] for t in ordered],
            "files": all_files,
            "stale_resume": is_resume,
        }

    # ── Step 2–3: lock + reserve ──

    def begin_work(
        self,
        merged: dict,
        source: str = "Telegram",
        ctx: str | None = None,
    ) -> str | None:
        """Acquire lock and reserve workspace.

        Returns workspace path on success, *None* if lock is held.
        """
        if not self.lock.acquire(
            msg_ids=merged["msg_ids"],
            summary=merged["instruction"][:80],
            ctx=ctx,
        ):
            return None

        ws = self.memory.reserve(
            instruction=merged["instruction"],
            chat_id=merged["chat_id"],
            timestamps=merged["all_timestamps"],
            msg_ids=merged["msg_ids"],
            source=source,
            ctx=ctx,
        )
        return ws

    # ── Step 5: finalize ──

    def finish_work(
        self,
        msg_ids: list[int],
        instruction: str,
        result: str,
        *,
        files: list[str] | None = None,
        chat_id: int | None = None,
        ctx: str | None = None,
    ) -> None:
        """Update index, write final manifest, and release the lock."""
        primary = msg_ids[0]
        self.memory.upsert(
            primary,
            instruction,
            result=result,
            files=files,
            chat_id=chat_id,
            ctx=ctx,
        )

        # Overwrite manifest with final result.
        ws = self.memory.workspace_path(primary, ctx)
        manifest = os.path.join(ws, MemoryManager.MANIFEST_NAME)
        if os.path.exists(manifest):
            try:
                with open(manifest, "r", encoding="utf-8") as fh:
                    content = fh.read()
                content = re.sub(
                    r"\[result\] .*",
                    f"[result] {result[:500]}",
                    content,
                    count=1,
                )
                with open(manifest, "w", encoding="utf-8") as fh:
                    fh.write(content)
            except Exception as exc:
                logger.warning("Manifest update failed: %s", exc)

        self.lock.release(ctx)


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 ** 2:.1f} MB"
