"""Tests for homunculus.core.engine.TaskEngine.

Covers merge_pending (single, multiple, empty, stale_resume),
begin_work (acquires lock + reserves workspace, fails when held),
and finish_work (updates manifest + releases lock).
"""

from __future__ import annotations

import os
import re

import pytest

from homunculus.core.engine import TaskEngine
from homunculus.core.memory import MemoryManager


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------


def _make_engine(tmp_path) -> TaskEngine:
    return TaskEngine(base_dir=str(tmp_path))


def _task(
    msg_id: int = 1,
    instruction: str = "do something",
    chat_id: int = 100,
    timestamp: str = "2024-01-01 12:00:00",
    user_name: str = "alice",
    *,
    files: list | None = None,
    stale_resume: bool = False,
) -> dict:
    t = {
        "msg_id": msg_id,
        "instruction": instruction,
        "chat_id": chat_id,
        "timestamp": timestamp,
        "user_name": user_name,
        "files": files or [],
    }
    if stale_resume:
        t["stale_resume"] = True
    return t


# ---------------------------------------------------------------------------
# merge_pending — single task
# ---------------------------------------------------------------------------


class TestMergePendingSingleTask:
    def test_merge_single_returns_dict(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = engine.merge_pending([_task()])
        assert isinstance(result, dict)

    def test_merge_single_preserves_instruction(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = engine.merge_pending([_task(instruction="hello world")])
        assert "hello world" in result["instruction"]

    def test_merge_single_sets_chat_id(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = engine.merge_pending([_task(chat_id=777)])
        assert result["chat_id"] == 777

    def test_merge_single_sets_msg_ids_list(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = engine.merge_pending([_task(msg_id=42)])
        assert result["msg_ids"] == [42]

    def test_merge_single_sets_user_name(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = engine.merge_pending([_task(user_name="bob")])
        assert result["user_name"] == "bob"

    def test_merge_single_stale_resume_false_by_default(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = engine.merge_pending([_task()])
        assert result["stale_resume"] is False

    def test_merge_single_includes_attachment_info(self, tmp_path, tmp_path_factory):
        # Create a real file so os.path.basename works without an error.
        f = tmp_path_factory.mktemp("att") / "data.csv"
        f.write_text("a,b,c")
        engine = _make_engine(tmp_path)
        task = _task(files=[{"path": str(f), "size": 6}])
        result = engine.merge_pending([task])
        assert "data.csv" in result["instruction"]


# ---------------------------------------------------------------------------
# merge_pending — multiple tasks
# ---------------------------------------------------------------------------


class TestMergePendingMultipleTasks:
    def test_merge_multiple_combines_instructions(self, tmp_path):
        engine = _make_engine(tmp_path)
        tasks = [
            _task(msg_id=1, instruction="first request", timestamp="2024-01-01 10:00:00"),
            _task(msg_id=2, instruction="second request", timestamp="2024-01-01 11:00:00"),
        ]
        result = engine.merge_pending(tasks)
        assert "first request" in result["instruction"]
        assert "second request" in result["instruction"]

    def test_merge_multiple_sorts_by_timestamp(self, tmp_path):
        engine = _make_engine(tmp_path)
        tasks = [
            _task(msg_id=2, instruction="B", timestamp="2024-01-01 11:00:00"),
            _task(msg_id=1, instruction="A", timestamp="2024-01-01 10:00:00"),
        ]
        result = engine.merge_pending(tasks)
        idx_a = result["instruction"].index("A")
        idx_b = result["instruction"].index("B")
        assert idx_a < idx_b, "Earlier timestamp must appear first"

    def test_merge_multiple_collects_all_msg_ids(self, tmp_path):
        engine = _make_engine(tmp_path)
        tasks = [_task(msg_id=i) for i in range(1, 5)]
        result = engine.merge_pending(tasks)
        assert set(result["msg_ids"]) == {1, 2, 3, 4}

    def test_merge_multiple_deduplicates_msg_ids(self, tmp_path):
        engine = _make_engine(tmp_path)
        tasks = [
            _task(msg_id=1, timestamp="2024-01-01 10:00:00"),
            _task(msg_id=1, timestamp="2024-01-01 10:00:00"),
        ]
        result = engine.merge_pending(tasks)
        assert result["msg_ids"].count(1) == 1

    def test_merge_multiple_all_timestamps_preserved(self, tmp_path):
        engine = _make_engine(tmp_path)
        tasks = [
            _task(msg_id=1, timestamp="2024-01-01 10:00:00"),
            _task(msg_id=2, timestamp="2024-01-01 11:00:00"),
        ]
        result = engine.merge_pending(tasks)
        assert len(result["all_timestamps"]) == 2


# ---------------------------------------------------------------------------
# merge_pending — empty list
# ---------------------------------------------------------------------------


class TestMergePendingEmpty:
    def test_merge_empty_list_returns_none(self, tmp_path):
        engine = _make_engine(tmp_path)
        result = engine.merge_pending([])
        assert result is None


# ---------------------------------------------------------------------------
# merge_pending — stale_resume flag
# ---------------------------------------------------------------------------


class TestMergePendingStaleResume:
    def test_stale_resume_flag_is_propagated(self, tmp_path):
        engine = _make_engine(tmp_path)
        task = _task(stale_resume=True)
        result = engine.merge_pending([task])
        assert result["stale_resume"] is True

    def test_stale_resume_prepends_resumption_header(self, tmp_path):
        engine = _make_engine(tmp_path)
        task = _task(stale_resume=True)
        result = engine.merge_pending([task])
        assert "Resuming" in result["instruction"] or "resume" in result["instruction"].lower()

    def test_no_stale_resume_when_all_tasks_are_fresh(self, tmp_path):
        engine = _make_engine(tmp_path)
        tasks = [_task(msg_id=i) for i in range(1, 4)]
        result = engine.merge_pending(tasks)
        assert result["stale_resume"] is False

    def test_stale_resume_true_if_any_task_has_flag(self, tmp_path):
        engine = _make_engine(tmp_path)
        tasks = [
            _task(msg_id=1, timestamp="2024-01-01 10:00:00"),
            _task(msg_id=2, timestamp="2024-01-01 11:00:00", stale_resume=True),
        ]
        result = engine.merge_pending(tasks)
        assert result["stale_resume"] is True


# ---------------------------------------------------------------------------
# begin_work — acquires lock and reserves workspace
# ---------------------------------------------------------------------------


class TestBeginWork:
    def _merged(self) -> dict:
        return {
            "instruction": "build API",
            "msg_ids": [1],
            "chat_id": 100,
            "timestamp": "2024-01-01 12:00:00",
            "all_timestamps": ["2024-01-01 12:00:00"],
            "user_name": "tester",
            "files": [],
            "stale_resume": False,
        }

    def test_begin_work_returns_workspace_path_on_success(self, tmp_path):
        engine = _make_engine(tmp_path)
        ws = engine.begin_work(self._merged())
        assert ws is not None
        assert os.path.isdir(ws)

    def test_begin_work_creates_manifest_file(self, tmp_path):
        engine = _make_engine(tmp_path)
        ws = engine.begin_work(self._merged())
        assert os.path.isfile(os.path.join(ws, MemoryManager.MANIFEST_NAME))

    def test_begin_work_acquires_lock(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.begin_work(self._merged())
        assert engine.lock.status() is not None

    def test_begin_work_with_context(self, tmp_path):
        engine = _make_engine(tmp_path)
        ws = engine.begin_work(self._merged(), ctx="dm")
        assert "dm" in ws
        assert engine.lock.status(ctx="dm") is not None

    def test_begin_work_returns_none_when_lock_is_held(self, tmp_path):
        engine = _make_engine(tmp_path)
        engine.begin_work(self._merged())
        # Second call to begin_work on same default context must fail.
        result = engine.begin_work(self._merged())
        assert result is None


# ---------------------------------------------------------------------------
# begin_work — fails when lock is held
# ---------------------------------------------------------------------------


class TestBeginWorkLockConflict:
    def _merged(self, msg_id: int = 1, ctx: str | None = None) -> dict:
        return {
            "instruction": f"task {msg_id}",
            "msg_ids": [msg_id],
            "chat_id": 200,
            "timestamp": "2024-06-01 10:00:00",
            "all_timestamps": ["2024-06-01 10:00:00"],
            "user_name": "user",
            "files": [],
            "stale_resume": False,
        }

    def test_second_begin_work_returns_none_same_default_context(self, tmp_path):
        engine = _make_engine(tmp_path)
        assert engine.begin_work(self._merged(1)) is not None
        assert engine.begin_work(self._merged(2)) is None

    def test_different_named_contexts_do_not_block_each_other(self, tmp_path):
        engine = _make_engine(tmp_path)
        ws_dm = engine.begin_work(self._merged(1), ctx="dm")
        ws_ch = engine.begin_work(self._merged(2), ctx="ch_100")
        assert ws_dm is not None
        assert ws_ch is not None


# ---------------------------------------------------------------------------
# finish_work — updates manifest and releases lock
# ---------------------------------------------------------------------------


class TestFinishWork:
    def _start(self, engine: TaskEngine, ctx: str | None = None) -> dict:
        merged = {
            "instruction": "complete this task",
            "msg_ids": [10],
            "chat_id": 50,
            "timestamp": "2024-03-15 09:00:00",
            "all_timestamps": ["2024-03-15 09:00:00"],
            "user_name": "worker",
            "files": [],
            "stale_resume": False,
        }
        engine.begin_work(merged, ctx=ctx)
        return merged

    def test_finish_work_releases_lock(self, tmp_path):
        engine = _make_engine(tmp_path)
        merged = self._start(engine)
        engine.finish_work(
            msg_ids=merged["msg_ids"],
            instruction=merged["instruction"],
            result="task completed",
            chat_id=merged["chat_id"],
        )
        assert engine.lock.status() is None

    def test_finish_work_updates_manifest_with_result(self, tmp_path):
        engine = _make_engine(tmp_path)
        merged = self._start(engine)
        engine.finish_work(
            msg_ids=merged["msg_ids"],
            instruction=merged["instruction"],
            result="final answer",
            chat_id=merged["chat_id"],
        )
        ws = engine.memory.workspace_path(10)
        manifest_text = open(os.path.join(ws, MemoryManager.MANIFEST_NAME)).read()
        assert "final answer" in manifest_text

    def test_finish_work_replaces_in_progress_marker(self, tmp_path):
        engine = _make_engine(tmp_path)
        merged = self._start(engine)
        engine.finish_work(
            msg_ids=merged["msg_ids"],
            instruction=merged["instruction"],
            result="done",
            chat_id=merged["chat_id"],
        )
        ws = engine.memory.workspace_path(10)
        manifest_text = open(os.path.join(ws, MemoryManager.MANIFEST_NAME)).read()
        assert "in progress" not in manifest_text

    def test_finish_work_updates_index(self, tmp_path):
        engine = _make_engine(tmp_path)
        merged = self._start(engine)
        engine.finish_work(
            msg_ids=merged["msg_ids"],
            instruction=merged["instruction"],
            result="stored result",
            files=["output.txt"],
            chat_id=merged["chat_id"],
        )
        results = engine.memory.search(msg_id=10)
        assert results[0]["result"] == "stored result"

    def test_finish_work_with_context_releases_correct_lock(self, tmp_path):
        engine = _make_engine(tmp_path)
        merged = self._start(engine, ctx="dm")
        engine.finish_work(
            msg_ids=merged["msg_ids"],
            instruction=merged["instruction"],
            result="done",
            chat_id=merged["chat_id"],
            ctx="dm",
        )
        assert engine.lock.status(ctx="dm") is None

    def test_finish_work_with_long_result_truncates_in_manifest(self, tmp_path):
        engine = _make_engine(tmp_path)
        merged = self._start(engine)
        long_result = "x" * 1000
        engine.finish_work(
            msg_ids=merged["msg_ids"],
            instruction=merged["instruction"],
            result=long_result,
            chat_id=merged["chat_id"],
        )
        ws = engine.memory.workspace_path(10)
        manifest_text = open(os.path.join(ws, MemoryManager.MANIFEST_NAME)).read()
        # The manifest [result] line should contain at most 500 chars of result.
        match = re.search(r"\[result\] (.+)", manifest_text)
        assert match is not None
        assert len(match.group(1)) <= 500
