"""Telegram message listener (long-polling).

Polls the Telegram Bot API for new messages and persists them
to the local JSON store. Supports text, photos, documents,
video, audio, voice messages, and location sharing.

Run directly::

    python -m homunculus.platforms.telegram.listener
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USERS = [
    int(uid.strip())
    for uid in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
    if uid.strip()
]
POLL_INTERVAL = int(os.getenv("TELEGRAM_POLLING_INTERVAL", "10"))

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)))
MESSAGES_FILE = os.path.join(_BASE_DIR, "telegram_messages.json")


def _load() -> dict:
    if os.path.exists(MESSAGES_FILE):
        try:
            with open(MESSAGES_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"messages": [], "last_update_id": 0}


def _save(data: dict) -> None:
    with open(MESSAGES_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


async def _download(bot, file_id: str, msg_id: int, ftype: str,
                    fname: str | None = None) -> str | None:
    """Download a Telegram file into ``workspace/job_<msg_id>/``."""
    try:
        ws = os.path.join(_BASE_DIR, "workspace", f"job_{msg_id}")
        os.makedirs(ws, exist_ok=True)

        tg_file = await bot.get_file(file_id)
        if fname:
            name = fname
        else:
            ext = os.path.splitext(tg_file.file_path or "")[1] or ".bin"
            prefix = {"photo": "image", "video": "video",
                      "audio": "audio", "voice": "voice"}.get(ftype, "file")
            name = f"{prefix}_{msg_id}{ext}"

        local = os.path.join(ws, name)
        await tg_file.download_to_drive(local)
        logger.info("Downloaded %s (%d bytes)", name, tg_file.file_size or 0)
        return local
    except Exception as exc:
        logger.error("Download failed: %s", exc)
        return None


async def fetch_new_messages() -> int | None:
    """Fetch and store new messages (single pass). Returns count or None on error."""
    from telegram import Bot

    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set.")
        return None

    bot = Bot(token=BOT_TOKEN)
    data = _load()
    last_id = data.get("last_update_id", 0)

    try:
        updates = await bot.get_updates(
            offset=last_id + 1, timeout=5, allowed_updates=["message"],
        )
    except Exception as exc:
        logger.error("Poll error: %s", exc)
        return None

    count = 0
    for upd in updates:
        if not upd.message:
            continue
        msg = upd.message
        user = msg.from_user
        if ALLOWED_USERS and user.id not in ALLOWED_USERS:
            logger.debug("Blocked user %d", user.id)
            continue

        text = msg.caption or msg.text or ""
        files: list[dict] = []

        # Photos (pick largest).
        if msg.photo:
            p = msg.photo[-1]
            path = await _download(bot, p.file_id, msg.message_id, "photo")
            if path:
                files.append({"type": "photo", "path": path, "size": p.file_size or 0})

        # Documents.
        if msg.document:
            path = await _download(bot, msg.document.file_id, msg.message_id,
                                   "document", msg.document.file_name)
            if path:
                files.append({"type": "document", "path": path,
                              "name": msg.document.file_name,
                              "mime_type": msg.document.mime_type,
                              "size": msg.document.file_size or 0})

        # Video.
        if msg.video:
            path = await _download(bot, msg.video.file_id, msg.message_id, "video")
            if path:
                files.append({"type": "video", "path": path,
                              "duration": msg.video.duration,
                              "size": msg.video.file_size or 0})

        # Audio.
        if msg.audio:
            path = await _download(bot, msg.audio.file_id, msg.message_id,
                                   "audio", msg.audio.file_name)
            if path:
                files.append({"type": "audio", "path": path,
                              "duration": msg.audio.duration,
                              "size": msg.audio.file_size or 0})

        # Voice.
        if msg.voice:
            path = await _download(bot, msg.voice.file_id, msg.message_id, "voice")
            if path:
                files.append({"type": "voice", "path": path,
                              "duration": msg.voice.duration,
                              "size": msg.voice.file_size or 0})

        # Location.
        location = None
        if msg.location:
            location = {
                "latitude": msg.location.latitude,
                "longitude": msg.location.longitude,
            }
            if hasattr(msg.location, "horizontal_accuracy") and msg.location.horizontal_accuracy:
                location["accuracy"] = msg.location.horizontal_accuracy

        if not text and not files and not location:
            continue

        entry = {
            "message_id": msg.message_id,
            "update_id": upd.update_id,
            "type": "user",
            "user_id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "chat_id": msg.chat_id,
            "text": text,
            "files": files,
            "location": location,
            "timestamp": msg.date.strftime("%Y-%m-%d %H:%M:%S"),
            "processed": False,
        }
        data["messages"].append(entry)
        if upd.update_id > data["last_update_id"]:
            data["last_update_id"] = upd.update_id
        count += 1

    if count:
        _save(data)
    return count


async def listen_loop() -> None:
    """Continuous polling loop. Ctrl+C to stop."""
    print("=" * 50)
    print("Telegram Listener — polling every %d sec" % POLL_INTERVAL)
    print("Allowed users:", ALLOWED_USERS or "all")
    print("=" * 50)

    cycle = 0
    try:
        while True:
            cycle += 1
            n = await fetch_new_messages()
            ts = datetime.now().strftime("%H:%M:%S")
            if n is None:
                logger.warning("[%s] #%d error, retrying...", ts, cycle)
            elif n > 0:
                logger.info("[%s] #%d — %d new message(s)", ts, cycle, n)
            await asyncio.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nListener stopped.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(listen_loop())
