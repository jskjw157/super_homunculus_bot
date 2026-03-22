"""Task memory and workspace management.

Provides persistent storage for task metadata (instructions, results, files)
and keyword-based retrieval. Each task gets an isolated workspace directory
under ``workspace/<context>/job_<id>/``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

_CTX_PATTERN = re.compile(r"^(dm|telegram|ch_\d{1,20})$", re.ASCII)


class MemoryManager:
    """Manages task workspaces, metadata index, and recall.

    Directory layout::

        <base>/workspace/
            index.json              # global task index
            dm/job_42/manifest.txt  # per-task metadata
            ch_123/job_99/...
    """

    INDEX_NAME = "index.json"
    MANIFEST_NAME = "manifest.txt"

    def __init__(self, base_dir: str):
        self._base_dir = base_dir
        self._ws_root = os.path.join(base_dir, "workspace")
        self._index_path = os.path.join(self._ws_root, self.INDEX_NAME)

    # ── workspace paths ──

    @staticmethod
    def _validate_ctx(ctx: str) -> None:
        if not _CTX_PATTERN.match(ctx):
            raise ValueError(f"Bad context: {ctx!r}")

    def workspace_path(self, msg_id: int, ctx: str | None = None) -> str:
        """Return (and create) the workspace directory for a given task."""
        if ctx:
            self._validate_ctx(ctx)
            path = os.path.join(self._ws_root, ctx, f"job_{msg_id}")
        else:
            path = os.path.join(self._ws_root, f"job_{msg_id}")
        os.makedirs(path, exist_ok=True)
        return path

    # ── index CRUD ──

    def _load_index(self) -> dict:
        if not os.path.exists(self._index_path):
            return {"tasks": [], "updated_at": ""}
        try:
            with open(self._index_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            logger.warning("Index read error: %s", exc)
            return {"tasks": [], "updated_at": ""}

    def _save_index(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self._index_path), exist_ok=True)
        data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self._index_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def upsert(
        self,
        msg_id: int,
        instruction: str,
        *,
        result: str = "",
        files: list[str] | None = None,
        chat_id: int | None = None,
        timestamp: str | None = None,
        ctx: str | None = None,
    ) -> None:
        """Insert or update a task entry in the index."""
        idx = self._load_index()

        keywords = list({w for w in instruction.split() if len(w) >= 2})[:10]
        ws = self.workspace_path(msg_id, ctx)

        entry = {
            "msg_id": msg_id,
            "timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "instruction": instruction,
            "keywords": keywords,
            "result": result,
            "files": files or [],
            "chat_id": chat_id,
            "workspace": ws,
            "context": ctx,
        }

        # Replace existing or append.
        replaced = False
        for i, t in enumerate(idx["tasks"]):
            if t["msg_id"] == msg_id:
                idx["tasks"][i] = entry
                replaced = True
                break
        if not replaced:
            idx["tasks"].append(entry)

        idx["tasks"].sort(key=lambda t: t["msg_id"], reverse=True)
        self._save_index(idx)
        logger.debug("Index upsert: msg_id=%d", msg_id)

    def search(
        self,
        *,
        keyword: str | None = None,
        msg_id: int | None = None,
        ctx: str | None = None,
    ) -> list[dict]:
        """Search the task index by keyword or message ID."""
        idx = self._load_index()
        tasks = idx["tasks"]

        if ctx:
            self._validate_ctx(ctx)
            tasks = [t for t in tasks if t.get("context") == ctx]

        if msg_id is not None:
            return [t for t in tasks if t["msg_id"] == msg_id]

        if keyword:
            kw = keyword.lower()
            return [
                t for t in tasks
                if kw in t["instruction"].lower()
                or any(kw in k.lower() for k in t.get("keywords", []))
            ]

        return tasks

    # ── manifest (task_info) ──

    def reserve(
        self,
        instruction: str,
        chat_id: int,
        timestamps: list[str],
        msg_ids: list[int],
        source: str = "Telegram",
        ctx: str | None = None,
    ) -> str:
        """Create workspace and write initial manifest before work begins.

        Returns the workspace directory path.
        """
        primary = msg_ids[0]
        ws = self.workspace_path(primary, ctx)
        manifest = os.path.join(ws, self.MANIFEST_NAME)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        id_info = (
            f"{', '.join(map(str, msg_ids))} (merged {len(msg_ids)})"
            if len(msg_ids) > 1
            else str(primary)
        )
        ts_info = (
            "\n".join(f"  - job_{m}: {t}" for m, t in zip(msg_ids, timestamps))
            if len(msg_ids) > 1
            else timestamps[0]
        )

        content = (
            f"[time] {now}\n"
            f"[msg_id] {id_info}\n"
            f"[source] {source} (chat_id: {chat_id})\n"
            f"[msg_date]\n{ts_info}\n"
            f"[instruction] {instruction}\n"
            f"[result] (in progress...)\n"
        )

        with open(manifest, "w", encoding="utf-8") as fh:
            fh.write(content)

        # Also update index.
        self.upsert(
            primary, instruction,
            chat_id=chat_id, timestamp=timestamps[0], ctx=ctx,
        )

        return ws

    def load_all(self, ctx: str | None = None) -> list[dict]:
        """Load all task manifests from workspace directories.

        Returns list sorted newest-first.
        """
        if not os.path.exists(self._ws_root):
            return []

        results: list[dict] = []

        def _scan(base: str, scan_ctx: str | None = None) -> None:
            if not os.path.isdir(base):
                return
            for name in os.listdir(base):
                if not name.startswith("job_"):
                    continue
                manifest = os.path.join(base, name, self.MANIFEST_NAME)
                if not os.path.isfile(manifest):
                    continue
                try:
                    id_part = name.split("_", 1)[1]
                    mid = int(id_part) if id_part.isdigit() else id_part
                    with open(manifest, "r", encoding="utf-8") as fh:
                        content = fh.read()
                    results.append({
                        "msg_id": mid,
                        "workspace": os.path.join(base, name),
                        "context": scan_ctx,
                        "content": content,
                    })
                except Exception as exc:
                    logger.warning("Manifest read error (%s): %s", name, exc)

        if ctx is None:
            _scan(self._ws_root, scan_ctx="telegram")
            _scan(os.path.join(self._ws_root, "dm"), scan_ctx="dm")
            for item in os.listdir(self._ws_root):
                if item.startswith("ch_"):
                    _scan(os.path.join(self._ws_root, item), scan_ctx=item)
        elif ctx == "telegram":
            _scan(self._ws_root, scan_ctx="telegram")
        else:
            self._validate_ctx(ctx)
            _scan(os.path.join(self._ws_root, ctx), scan_ctx=ctx)

        results.sort(
            key=lambda r: (isinstance(r["msg_id"], int), r["msg_id"]),
            reverse=True,
        )
        return results

    def load_relevant(
        self,
        instruction: str,
        *,
        max_items: int = 10,
        max_chars: int = 12000,
        ctx: str | None = None,
    ) -> list[dict]:
        """Load manifests most relevant to *instruction*.

        Strategy:
          1. Always include the 3 most recent (temporal context).
          2. Score remaining by keyword overlap.
          3. Truncate to stay within *max_chars*.
        """
        all_mem = self.load_all(ctx)
        if not all_mem:
            return []

        keywords = {w.lower() for w in instruction.split() if len(w) >= 2}
        recent, rest = all_mem[:3], all_mem[3:]

        scored = []
        for m in rest:
            hits = sum(1 for kw in keywords if kw in m["content"].lower())
            if hits:
                scored.append((hits, m))
        scored.sort(key=lambda x: x[0], reverse=True)

        selected = recent + [m for _, m in scored[: max_items - len(recent)]]

        out: list[dict] = []
        total = 0
        for m in selected:
            size = len(m["content"])
            if total + size > max_chars:
                break
            out.append(m)
            total += size
        return out
