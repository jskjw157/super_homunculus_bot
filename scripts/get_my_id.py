#!/usr/bin/env python3
"""Utility to discover your Telegram/Discord user ID.

Run this and send a message to your bot. It will print the user ID
that you need to put in .env as TELEGRAM_ALLOWED_USERS or
DISCORD_ALLOWED_USERS.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))


async def telegram_id():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or token.startswith("your"):
        print("TELEGRAM_BOT_TOKEN not set in .env")
        return

    from telegram import Bot
    bot = Bot(token=token)
    print("Waiting for Telegram messages... (send any message to your bot)")

    last_id = 0
    while True:
        updates = await bot.get_updates(offset=last_id + 1, timeout=10)
        for upd in updates:
            if upd.message:
                user = upd.message.from_user
                print(f"\n{'='*40}")
                print(f"User: {user.first_name} {user.last_name or ''}")
                print(f"Username: @{user.username or 'N/A'}")
                print(f"User ID: {user.id}")
                print(f"Chat ID: {upd.message.chat_id}")
                print(f"{'='*40}")
                print(f"\nAdd to .env:")
                print(f"TELEGRAM_ALLOWED_USERS={user.id}")
                return
            last_id = max(last_id, upd.update_id)


if __name__ == "__main__":
    asyncio.run(telegram_id())
