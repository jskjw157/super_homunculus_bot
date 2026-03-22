"""Telegram message and file sender.

Handles text chunking (4096-char limit), file size validation (50 MB),
and automatic format fallback (Markdown → plain text).
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MAX_TEXT_LEN = 4000
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


async def send_message(chat_id: int, text: str, parse_mode: str = "Markdown") -> bool:
    """Send a text message, auto-chunking if too long."""
    from telegram import Bot

    if not _check_token():
        return False
    try:
        bot = Bot(token=BOT_TOKEN)
        if len(text) > MAX_TEXT_LEN:
            for i in range(0, len(text), MAX_TEXT_LEN):
                if i > 0:
                    await asyncio.sleep(0.5)
                await bot.send_message(chat_id=chat_id, text=text[i:i + MAX_TEXT_LEN],
                                       parse_mode=parse_mode)
        else:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        return True
    except Exception as exc:
        logger.error("Send failed: %s", exc)
        # Retry without formatting.
        if parse_mode:
            return await send_message(chat_id, text, parse_mode=None)
        return False


async def send_file(chat_id: int, path: str, caption: str | None = None) -> bool:
    """Send a single file with optional caption."""
    from telegram import Bot

    if not _check_token():
        return False
    if not os.path.exists(path):
        logger.error("File not found: %s", path)
        return False
    if os.path.getsize(path) > MAX_FILE_SIZE:
        logger.error("File too large (>50 MB): %s", path)
        return False
    try:
        bot = Bot(token=BOT_TOKEN)
        with open(path, "rb") as fh:
            await bot.send_document(chat_id=chat_id, document=fh, caption=caption)
        return True
    except Exception as exc:
        logger.error("File send failed: %s", exc)
        return False


async def send_files(chat_id: int, text: str, paths: list[str]) -> bool:
    """Send text + multiple files."""
    ok = await send_message(chat_id, text)
    for p in paths:
        if not await send_file(chat_id, p):
            ok = False
    return ok


# ── sync wrappers ──

def run_async_safe(coro):
    """Run an async coroutine from synchronous code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)


def send_message_sync(chat_id: int, text: str) -> bool:
    return run_async_safe(send_message(chat_id, text))


def send_files_sync(chat_id: int, text: str, paths: list[str]) -> bool:
    return run_async_safe(send_files(chat_id, text, paths))


def _check_token() -> bool:
    if not BOT_TOKEN or BOT_TOKEN in ("your_bot_token_here", "YOUR_BOT_TOKEN"):
        logger.error("TELEGRAM_BOT_TOKEN not configured.")
        return False
    return True
