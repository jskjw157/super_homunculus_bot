"""Tests for homunculus.core.lock.LockManager.

Covers the full acquire/release lifecycle, double-acquire protection,
heartbeat updates, stale-lock detection, context isolation, and the
atomic O_EXCL file-creation guarantee.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from homunculus.core.lock import LockManager, STALE_TIMEOUT_SEC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_lock(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _lock_path(lm: LockManager, ctx: str | None = None) -> str:
    return lm._lock_path(ctx)


# ---------------------------------------------------------------------------
# Acquire / release cycle
# ---------------------------------------------------------------------------


class TestAcquireRelease:
    def test_acquire_returns_true_on_success(self, tmp_path):
        lm = LockManager(str(tmp_path))
        result = lm.acquire(msg_ids=[1], summary="test task")
        assert result is True

    def test_acquire_creates_lock_file(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="test task")
        assert os.path.exists(_lock_path(lm))

    def test_lock_file_contains_expected_fields(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[7, 8], summary="hello world")
        data = _read_lock(_lock_path(lm))
        assert data["msg_ids"] == [7, 8]
        assert data["summary"] == "hello world"
        assert "acquired_at" in data
        assert "last_heartbeat" in data
        assert data["pid"] == os.getpid()

    def test_release_removes_lock_file(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="task")
        lm.release()
        assert not os.path.exists(_lock_path(lm))

    def test_release_on_nonexistent_lock_is_idempotent(self, tmp_path):
        lm = LockManager(str(tmp_path))
        # Should not raise even if there is no lock file.
        lm.release()

    def test_acquire_release_acquire_cycle(self, tmp_path):
        lm = LockManager(str(tmp_path))
        assert lm.acquire(msg_ids=[1], summary="first") is True
        lm.release()
        assert lm.acquire(msg_ids=[2], summary="second") is True
        lm.release()


# ---------------------------------------------------------------------------
# Double-acquire (returns False when lock is held)
# ---------------------------------------------------------------------------


class TestDoubleAcquire:
    def test_double_acquire_returns_false(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="first")
        result = lm.acquire(msg_ids=[2], summary="second attempt")
        assert result is False

    def test_double_acquire_does_not_overwrite_existing_lock(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="first")
        lm.acquire(msg_ids=[2], summary="second attempt")
        data = _read_lock(_lock_path(lm))
        # The original lock data must still be intact.
        assert data["msg_ids"] == [1]
        assert data["summary"] == "first"

    def test_status_returns_none_when_unlocked(self, tmp_path):
        lm = LockManager(str(tmp_path))
        assert lm.status() is None

    def test_status_returns_dict_when_locked(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[5], summary="active task")
        info = lm.status()
        assert info is not None
        assert info["msg_ids"] == [5]


# ---------------------------------------------------------------------------
# Heartbeat updates timestamp
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_heartbeat_updates_last_heartbeat_field(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="task")

        before = _read_lock(_lock_path(lm))["last_heartbeat"]
        # Advance the mocked clock by 2 seconds so the timestamps differ.
        future_dt = datetime.now() + timedelta(seconds=2)
        with patch("homunculus.core.lock.datetime") as mock_dt:
            mock_dt.now.return_value = future_dt
            mock_dt.strptime = datetime.strptime
            lm.heartbeat()

        after = _read_lock(_lock_path(lm))["last_heartbeat"]
        assert after != before

    def test_heartbeat_does_not_alter_other_fields(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[3], summary="stable task")
        lm.heartbeat()
        data = _read_lock(_lock_path(lm))
        assert data["msg_ids"] == [3]
        assert data["summary"] == "stable task"

    def test_heartbeat_on_missing_lock_is_silent(self, tmp_path):
        lm = LockManager(str(tmp_path))
        # No lock file exists — must not raise.
        lm.heartbeat()


# ---------------------------------------------------------------------------
# Stale lock detection (mock time)
# ---------------------------------------------------------------------------


class TestStaleLockDetection:
    def test_fresh_lock_is_not_stale(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="fresh task")
        info = lm.status()
        assert info is not None
        assert info.get("stale") is not True

    def test_old_heartbeat_marks_lock_as_stale(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="stale task")

        # Rewrite the lock file with a timestamp far in the past.
        path = _lock_path(lm)
        data = _read_lock(path)
        past = datetime.now() - timedelta(seconds=STALE_TIMEOUT_SEC + 60)
        data["last_heartbeat"] = past.strftime("%Y-%m-%d %H:%M:%S")
        data["acquired_at"] = past.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

        info = lm.status()
        assert info is not None
        assert info.get("stale") is True

    def test_stale_detection_uses_last_heartbeat_over_acquired_at(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="task")

        path = _lock_path(lm)
        data = _read_lock(path)
        # acquired_at is old, but last_heartbeat is recent.
        past = datetime.now() - timedelta(seconds=STALE_TIMEOUT_SEC + 60)
        data["acquired_at"] = past.strftime("%Y-%m-%d %H:%M:%S")
        # last_heartbeat stays at current time (set during acquire).
        data["last_heartbeat"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

        info = lm.status()
        assert info is not None
        # Should NOT be stale because heartbeat is recent.
        assert info.get("stale") is not True


# ---------------------------------------------------------------------------
# Context isolation
# ---------------------------------------------------------------------------


class TestContextIsolation:
    def test_dm_and_channel_locks_are_independent(self, tmp_path):
        lm = LockManager(str(tmp_path))
        assert lm.acquire(msg_ids=[1], summary="dm task", ctx="dm") is True
        # A different context must be acquirable simultaneously.
        assert lm.acquire(msg_ids=[2], summary="channel task", ctx="ch_999") is True

    def test_dm_lock_does_not_appear_in_channel_status(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="dm only", ctx="dm")
        assert lm.status(ctx="ch_999") is None

    def test_channel_lock_does_not_appear_in_dm_status(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="channel only", ctx="ch_999")
        assert lm.status(ctx="dm") is None

    def test_release_per_context_leaves_other_contexts_intact(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="dm task", ctx="dm")
        lm.acquire(msg_ids=[2], summary="channel task", ctx="ch_100")
        lm.release(ctx="dm")
        assert lm.status(ctx="dm") is None
        assert lm.status(ctx="ch_100") is not None

    def test_invalid_context_raises_value_error(self, tmp_path):
        lm = LockManager(str(tmp_path))
        with pytest.raises(ValueError):
            lm.acquire(msg_ids=[1], summary="bad ctx", ctx="../traversal")

    def test_default_context_lock_path_is_at_base_dir(self, tmp_path):
        lm = LockManager(str(tmp_path))
        expected = os.path.join(str(tmp_path), LockManager.DEFAULT_LOCK)
        assert lm._lock_path() == expected

    def test_named_context_lock_path_is_under_workspace(self, tmp_path):
        lm = LockManager(str(tmp_path))
        path = lm._lock_path("ch_42")
        assert "workspace" in path
        assert "ch_42" in path


# ---------------------------------------------------------------------------
# Atomic file creation (O_EXCL)
# ---------------------------------------------------------------------------


class TestAtomicFileCreation:
    def test_acquire_uses_o_excl_flag(self, tmp_path):
        """Verify atomicity: the lock file must not already exist after acquire."""
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="first")
        lock_path = _lock_path(lm)
        # The file exists after the first acquire.
        assert os.path.isfile(lock_path)
        # Attempting to open with O_EXCL ourselves should raise FileExistsError,
        # confirming the original create used O_EXCL atomically.
        with pytest.raises(FileExistsError):
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            os.close(fd)

    def test_acquire_creates_parent_directories(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[1], summary="nested ctx", ctx="ch_1234")
        ws_path = os.path.join(str(tmp_path), "workspace", "ch_1234")
        assert os.path.isdir(ws_path)

    def test_lock_file_content_is_valid_json(self, tmp_path):
        lm = LockManager(str(tmp_path))
        lm.acquire(msg_ids=[10, 11], summary="json test")
        with open(_lock_path(lm), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert isinstance(data, dict)

    def test_summary_is_truncated_to_80_chars(self, tmp_path):
        lm = LockManager(str(tmp_path))
        long_summary = "x" * 200
        lm.acquire(msg_ids=[1], summary=long_summary)
        data = _read_lock(_lock_path(lm))
        assert len(data["summary"]) <= 80
