#!/usr/bin/env python3
"""Process pending Telegram messages through the task engine.

Typical usage (scheduled via cron/launchd/Task Scheduler)::

    python scripts/run_telegram.py
"""

import os
import sys

# Add project root to path.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from homunculus.core.engine import TaskEngine
from homunculus.platforms.telegram.adapter import TelegramAdapter
from homunculus.platforms.telegram.sender import send_message_sync


def main():
    engine = TaskEngine(ROOT)
    adapter = TelegramAdapter(ROOT)

    # 1. Fetch pending.
    print("Checking for pending Telegram messages...")
    pending = adapter.fetch_pending()
    if not pending:
        print("No pending messages.")
        return

    # 2. Merge.
    merged = engine.merge_pending(pending)
    if not merged:
        return

    # 3. Acknowledge.
    count = len(merged["msg_ids"])
    ack = f"Starting work ({count} requests merged)." if count > 1 else "Starting work."
    send_message_sync(merged["chat_id"], ack)

    # 4. Lock + reserve workspace.
    ws = engine.begin_work(merged, source="Telegram")
    if not ws:
        print("Lock held by another process.")
        return

    # 5. Print task info.
    print("=" * 50)
    print(f"Message IDs: {merged['msg_ids']}")
    print(f"User: {merged['user_name']}")
    print(f"Workspace: {ws}")
    print(f"\nInstruction:\n{merged['instruction']}")
    print("=" * 50)

    print("\nReady. Claude Code will now process this task.")


if __name__ == "__main__":
    main()
