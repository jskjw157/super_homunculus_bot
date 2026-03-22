"""Discord message and file sender.

Uses Discord webhook/bot token to send messages and files.
Handles the 2000-character message limit and file attachments.
"""

from __future__ import annotations

import asyncio
import logging
import os
from io import BytesIO

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_MSG_LIMIT = 2000


async def send_message(channel_id: int, text: str) -> bool:
    """Send a text message to a Discord channel, chunking if needed."""
    import discord

    if not BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not configured.")
        return False

    client = discord.Client(intents=discord.Intents.default())

    @client.event
    async def on_ready():
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
            chunks = _chunk_text(text, DISCORD_MSG_LIMIT)
            for chunk in chunks:
                await channel.send(chunk)
        except Exception as exc:
            logger.error("Discord send error: %s", exc)
        finally:
            await client.close()

    try:
        await client.start(BOT_TOKEN)
    except Exception as exc:
        logger.error("Discord client error: %s", exc)
        return False
    return True


async def send_files(channel_id: int, text: str, paths: list[str]) -> bool:
    """Send text and file attachments to a Discord channel."""
    import discord

    if not BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not configured.")
        return False

    client = discord.Client(intents=discord.Intents.default())
    success = True

    @client.event
    async def on_ready():
        nonlocal success
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)

            # Send text first (chunked).
            if text:
                for chunk in _chunk_text(text, DISCORD_MSG_LIMIT):
                    await channel.send(chunk)

            # Send files.
            for p in paths:
                if os.path.exists(p):
                    await channel.send(file=discord.File(p))
                else:
                    logger.warning("File not found: %s", p)
        except Exception as exc:
            logger.error("Discord send error: %s", exc)
            success = False
        finally:
            await client.close()

    try:
        await client.start(BOT_TOKEN)
    except Exception as exc:
        logger.error("Discord client error: %s", exc)
        return False
    return success


def _chunk_text(text: str, limit: int) -> list[str]:
    """Split text into chunks respecting the character limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to split at newline.
        idx = text.rfind("\n", 0, limit)
        if idx == -1:
            idx = limit
        chunks.append(text[:idx])
        text = text[idx:].lstrip("\n")
    return chunks


# ── sync wrappers ──

def _run_sync(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def send_message_sync(channel_id: int, text: str) -> bool:
    return _run_sync(send_message(channel_id, text))


def send_files_sync(channel_id: int, text: str, paths: list[str]) -> bool:
    return _run_sync(send_files(channel_id, text, paths))
