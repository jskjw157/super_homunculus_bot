"""Discord message listener using discord.py gateway.

Listens for messages in allowed guilds and persists them to the
SQLite message store for later processing.

Run directly::

    python -m homunculus.platforms.discord.listener
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime

import discord
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
ALLOWED_USERS = [
    int(uid.strip())
    for uid in os.getenv("DISCORD_ALLOWED_USERS", "").split(",")
    if uid.strip()
]

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)))


class HomunculusListener(discord.Client):
    """Gateway listener that captures user messages into SQLite."""

    def __init__(self, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        super().__init__(intents=intents, **kwargs)
        self._store = None

    def _get_store(self):
        if self._store is None:
            from ...core.store import MessageStore
            self._store = MessageStore(_BASE_DIR)
        return self._store

    async def on_ready(self):
        logger.info("Listener connected as %s", self.user)

    async def on_message(self, message: discord.Message):
        # Ignore bot's own messages.
        if message.author == self.user:
            return
        if message.author.bot:
            return

        # Verify allowed users.
        if ALLOWED_USERS and message.author.id not in ALLOWED_USERS:
            logger.debug("Blocked user: %d", message.author.id)
            return

        # Determine context.
        if isinstance(message.channel, discord.DMChannel):
            ctx = "dm"
            ch_name = "DM"
            ch_type = "dm"
        else:
            ctx = f"ch_{message.channel.id}"
            ch_name = getattr(message.channel, "name", str(message.channel.id))
            ch_type = "channel"

        text = message.content or ""

        # Download attachments.
        files = []
        for att in message.attachments:
            ws = os.path.join(_BASE_DIR, "workspace", ctx, f"job_{message.id}")
            os.makedirs(ws, exist_ok=True)
            local = os.path.join(ws, att.filename)
            try:
                await att.save(local)
                files.append({
                    "type": "document",
                    "path": local,
                    "name": att.filename,
                    "size": att.size,
                })
            except Exception as exc:
                logger.warning("Attachment download failed: %s", exc)

        if not text and not files:
            return

        store = self._get_store()
        store.insert(
            message_id=message.id,
            channel_id=message.channel.id,
            context=ctx,
            channel_type=ch_type,
            channel_name=ch_name,
            user_id=message.author.id,
            user_name=message.author.display_name,
            text=text,
            files=files,
            timestamp=message.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
        logger.info("[%s] New message from %s: %s",
                     ctx, message.author.display_name, text[:60])


def run():
    """Start the Discord listener."""
    if not BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not set.")
        return
    client = HomunculusListener()
    client.run(BOT_TOKEN)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
