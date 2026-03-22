#!/usr/bin/env python3
"""Process pending Discord messages through the task engine.

Iterates over all contexts (DM + channels) with pending work.

Usage::

    python scripts/run_discord.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from homunculus.core.engine import TaskEngine
from homunculus.platforms.discord.adapter import DiscordAdapter
from homunculus.platforms.discord.sender import send_message_sync


def main():
    engine = TaskEngine(ROOT)
    adapter = DiscordAdapter(ROOT)

    # Recover any stale messages first.
    recovered = adapter.recover_stale()
    if recovered:
        print(f"Recovered {recovered} stale message(s).")

    # Get all contexts with pending work.
    contexts = adapter.get_pending_contexts()
    if not contexts:
        print("No pending Discord messages.")
        return

    for ctx in contexts:
        print(f"\n--- Processing context: {ctx} ---")

        pending = adapter.fetch_pending(ctx=ctx)
        if not pending:
            continue

        merged = engine.merge_pending(pending)
        if not merged:
            continue

        # Acknowledge.
        count = len(merged["msg_ids"])
        ack = f"Starting work ({count} requests merged)." if count > 1 else "Starting work."
        send_message_sync(merged["chat_id"], ack)

        # Lock + reserve.
        ws = engine.begin_work(merged, source="Discord", ctx=ctx)
        if not ws:
            print(f"Lock held for context {ctx} — skipping.")
            continue

        print(f"Workspace: {ws}")
        print(f"Instruction:\n{merged['instruction'][:200]}...")
        print("Ready for Claude Code processing.")


if __name__ == "__main__":
    main()
