"""Claude Agent SDK bridge.

Manages Claude Code sessions: creation, resumption, prompt assembly,
and streaming response handling.  Designed to maintain long-running
conversations across bot restarts via session persistence.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class TaskResult:
    """Result of an AI task execution."""
    success: bool
    text: str = ""
    files: list[str] = field(default_factory=list)
    session_id: str = ""
    cost_usd: float = 0.0
    turns: int = 0
    error: str = ""


class ClaudeBridge:
    """Interface to Claude Agent SDK for task execution.

    Handles session lifecycle:
      - New session creation with project context
      - Session resumption for conversation continuity
      - Graceful fallback on session expiry

    Usage::

        bridge = ClaudeBridge(project_dir="/path/to/project")
        result = await bridge.run(
            ctx="dm",
            instruction="Build a landing page",
            memories=[...],
            workspace="/path/to/workspace",
        )
    """

    def __init__(self, project_dir: str):
        self._project_dir = project_dir
        self._sessions: dict[str, str] = {}  # ctx → session_id

    async def run(
        self,
        ctx: str,
        instruction: str,
        memories: list[dict],
        workspace: str,
        *,
        progress_callback=None,
        project_cwd: str | None = None,
        target_session_id: str | None = None,
    ) -> TaskResult:
        """Execute a task via Claude Agent SDK.

        Args:
            ctx: Platform context ("dm", "ch_123").
            instruction: The user's request.
            memories: Relevant past task manifests.
            workspace: Directory for task outputs.
            progress_callback: Optional async fn(text) for progress updates.
            project_cwd: Override working directory for Claude.
            target_session_id: Explicit session to resume.

        Returns:
            TaskResult with the AI's output.
        """
        try:
            from claude_code_sdk import (
                ClaudeCodeOptions,
                query,
                TextBlock,
                ToolUseBlock,
                ToolResultBlock,
                ResultMessage,
            )
        except ImportError:
            return TaskResult(
                success=False,
                error="claude_code_sdk not installed. Run: pip install claude-code-sdk",
            )

        # Determine session to resume.
        session_id = target_session_id or self._sessions.get(ctx)

        # Build prompt.
        prompt = self._build_prompt(instruction, memories, workspace)

        cwd = project_cwd or self._project_dir

        options = ClaudeCodeOptions(
            max_turns=50,
            cwd=cwd,
        )

        # Set resume if we have a prior session.
        if session_id:
            options.resume = session_id

        result_parts: list[str] = []
        result_files: list[str] = []
        new_session_id = ""
        turns = 0
        cost = 0.0

        try:
            async for msg in query(prompt=prompt, options=options):
                if hasattr(msg, "session_id") and msg.session_id:
                    new_session_id = msg.session_id

                if hasattr(msg, "content"):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            result_parts.append(block.text)
                            if progress_callback and len(block.text) > 20:
                                await progress_callback(block.text[:200])

                if hasattr(msg, "is_error") and msg.is_error:
                    return TaskResult(
                        success=False,
                        error="\n".join(result_parts),
                        session_id=new_session_id,
                    )

                if isinstance(msg, ResultMessage):
                    if hasattr(msg, "cost_usd"):
                        cost = msg.cost_usd or 0.0
                    if hasattr(msg, "num_turns"):
                        turns = msg.num_turns or 0

        except Exception as exc:
            error_msg = str(exc)
            # If session expired, retry without resume.
            if session_id and "session" in error_msg.lower():
                logger.warning("Session expired for ctx=%s, creating new.", ctx)
                self._sessions.pop(ctx, None)
                options.resume = None
                try:
                    async for msg in query(prompt=prompt, options=options):
                        if hasattr(msg, "session_id") and msg.session_id:
                            new_session_id = msg.session_id
                        if hasattr(msg, "content"):
                            for block in msg.content:
                                if isinstance(block, TextBlock):
                                    result_parts.append(block.text)
                except Exception as retry_exc:
                    return TaskResult(success=False, error=str(retry_exc))
            else:
                return TaskResult(success=False, error=error_msg)

        # Persist session for next call.
        if new_session_id:
            self._sessions[ctx] = new_session_id

        return TaskResult(
            success=True,
            text="\n".join(result_parts),
            files=result_files,
            session_id=new_session_id,
            cost_usd=cost,
            turns=turns,
        )

    def _build_prompt(
        self,
        instruction: str,
        memories: list[dict],
        workspace: str,
    ) -> str:
        """Assemble the full prompt with context."""
        parts = [instruction]

        if memories:
            parts.append("\n---\n[Reference: Previous tasks]")
            for m in memories[:5]:
                content = m.get("content", "")
                if len(content) > 500:
                    content = content[:500] + "..."
                parts.append(content)

        parts.append(f"\n[Workspace: {workspace}]")
        return "\n\n".join(parts)

    async def shutdown(self) -> None:
        """Clean up resources."""
        self._sessions.clear()
