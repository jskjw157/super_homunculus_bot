"""Abstract base class for messaging platform adapters.

Every platform (Telegram, Discord, etc.) implements this interface,
enabling the ``TaskEngine`` to work identically regardless of the
underlying messaging service.

Design rationale:
  - Strategy Pattern: swap adapters without touching orchestration logic.
  - Template Method: ``process()`` defines the pipeline; subclasses
    override individual steps.
"""

from __future__ import annotations

import abc
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PlatformAdapter(abc.ABC):
    """Contract that every messaging platform must fulfill.

    Subclasses must implement all abstract methods.  The optional
    ``process()`` template demonstrates the standard pipeline but
    callers may also invoke steps individually for finer control.
    """

    # ── abstract interface ──

    @abc.abstractmethod
    def fetch_pending(self) -> list[dict]:
        """Return unprocessed messages for this platform.

        Each dict must contain at least::

            {
                "msg_id": int,
                "instruction": str,
                "chat_id": int,
                "timestamp": str,       # ISO-ish: "2026-01-15 14:30:00"
                "user_name": str,
                "files": list[dict],    # optional attachments
                "location": dict|None,  # optional geo
            }
        """

    @abc.abstractmethod
    def send_text(self, chat_id: int, text: str) -> bool:
        """Send a text message. Return True on success."""

    @abc.abstractmethod
    def send_files(self, chat_id: int, text: str, paths: list[str]) -> bool:
        """Send files with an optional caption. Return True on success."""

    @abc.abstractmethod
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
        """Deliver final task output to the user and persist metadata."""

    @abc.abstractmethod
    def mark_completed(self, msg_ids: list[int]) -> None:
        """Mark message IDs as processed so they are not fetched again."""

    # ── optional template method ──

    def process(self, engine: Any) -> bool:
        """Run the standard task pipeline.

        1. Fetch pending messages
        2. Merge into work unit
        3. Send acknowledgement
        4. Acquire lock + reserve workspace
        5. (caller runs AI bridge)
        6. Deliver result, mark done, release lock

        Returns *True* if there was work to process.
        """
        pending = self.fetch_pending()
        if not pending:
            return False

        merged = engine.merge_pending(pending)
        if not merged:
            return False

        # Acknowledge receipt.
        count = len(merged["msg_ids"])
        ack = (
            f"Starting work ({count} requests merged)."
            if count > 1
            else "Starting work."
        )
        self.send_text(merged["chat_id"], ack)

        return True
