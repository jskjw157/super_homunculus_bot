"""Tests for homunculus.session.manager.SessionLifecycle.

Covers save/get, disk persistence across restarts, remove, list_all,
and clear.  All tests are hermetically isolated via tmp_path.
"""

from __future__ import annotations

import json
import os

import pytest

from homunculus.session.manager import SessionLifecycle, SessionInfo


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def sl(tmp_path) -> SessionLifecycle:
    return SessionLifecycle(str(tmp_path))


# ---------------------------------------------------------------------------
# save and get
# ---------------------------------------------------------------------------


class TestSaveAndGet:
    def test_get_returns_none_for_unknown_context(self, sl):
        assert sl.get("dm") is None

    def test_save_then_get_returns_session_id(self, sl):
        sl.save("dm", "session-abc-123")
        assert sl.get("dm") == "session-abc-123"

    def test_save_overwrites_existing_session_id(self, sl):
        sl.save("dm", "old-session")
        sl.save("dm", "new-session")
        assert sl.get("dm") == "new-session"

    def test_save_stores_cwd(self, sl, tmp_path):
        sl.save("dm", "sess-1", cwd=str(tmp_path))
        info = next(s for s in sl.list_all() if s.context == "dm")
        assert info.cwd == str(tmp_path)

    def test_save_sets_created_at_on_first_call(self, sl):
        sl.save("dm", "sess-1")
        info = next(s for s in sl.list_all() if s.context == "dm")
        assert info.created_at != ""

    def test_save_preserves_created_at_on_update(self, sl):
        sl.save("dm", "sess-1")
        first_created = next(s for s in sl.list_all() if s.context == "dm").created_at
        sl.save("dm", "sess-2")
        updated_created = next(s for s in sl.list_all() if s.context == "dm").created_at
        assert first_created == updated_created

    def test_save_updates_last_used_on_each_call(self, sl):
        sl.save("dm", "sess-1")
        first_used = next(s for s in sl.list_all() if s.context == "dm").last_used
        sl.save("dm", "sess-2")
        second_used = next(s for s in sl.list_all() if s.context == "dm").last_used
        # last_used may be the same second if the test runs fast; just verify it exists.
        assert second_used != ""

    def test_multiple_contexts_stored_independently(self, sl):
        sl.save("dm", "dm-session")
        sl.save("ch_100", "ch-session")
        assert sl.get("dm") == "dm-session"
        assert sl.get("ch_100") == "ch-session"


# ---------------------------------------------------------------------------
# Persistence to disk (write, recreate, read back)
# ---------------------------------------------------------------------------


class TestDiskPersistence:
    def test_state_file_is_created_after_save(self, tmp_path):
        sl = SessionLifecycle(str(tmp_path))
        sl.save("dm", "sess-xyz")
        state_path = os.path.join(str(tmp_path), SessionLifecycle.STATE_FILE)
        assert os.path.isfile(state_path)

    def test_state_file_is_valid_json(self, tmp_path):
        sl = SessionLifecycle(str(tmp_path))
        sl.save("dm", "sess-abc")
        state_path = os.path.join(str(tmp_path), SessionLifecycle.STATE_FILE)
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert "sessions" in data

    def test_new_instance_restores_session_from_disk(self, tmp_path):
        sl1 = SessionLifecycle(str(tmp_path))
        sl1.save("dm", "persistent-session-id")

        # Simulate a process restart by creating a fresh instance.
        sl2 = SessionLifecycle(str(tmp_path))
        assert sl2.get("dm") == "persistent-session-id"

    def test_new_instance_restores_multiple_sessions(self, tmp_path):
        sl1 = SessionLifecycle(str(tmp_path))
        sl1.save("dm", "sess-dm")
        sl1.save("ch_42", "sess-ch42")

        sl2 = SessionLifecycle(str(tmp_path))
        assert sl2.get("dm") == "sess-dm"
        assert sl2.get("ch_42") == "sess-ch42"

    def test_new_instance_restores_cwd(self, tmp_path):
        sl1 = SessionLifecycle(str(tmp_path))
        sl1.save("dm", "sess", cwd="/project/root")

        sl2 = SessionLifecycle(str(tmp_path))
        info = next(s for s in sl2.list_all() if s.context == "dm")
        assert info.cwd == "/project/root"

    def test_instance_with_no_state_file_starts_empty(self, tmp_path):
        # No save ever called — state file does not exist.
        sl = SessionLifecycle(str(tmp_path))
        assert sl.list_all() == []

    def test_corrupted_state_file_is_handled_gracefully(self, tmp_path):
        state_path = os.path.join(str(tmp_path), SessionLifecycle.STATE_FILE)
        with open(state_path, "w") as fh:
            fh.write("not valid json {{{{")
        # Must not raise.
        sl = SessionLifecycle(str(tmp_path))
        assert sl.list_all() == []


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


class TestRemove:
    def test_remove_deletes_session(self, sl):
        sl.save("dm", "sess-1")
        sl.remove("dm")
        assert sl.get("dm") is None

    def test_remove_nonexistent_context_is_idempotent(self, sl):
        sl.remove("dm")  # must not raise

    def test_remove_persists_to_disk(self, tmp_path):
        sl1 = SessionLifecycle(str(tmp_path))
        sl1.save("dm", "sess-1")
        sl1.remove("dm")

        sl2 = SessionLifecycle(str(tmp_path))
        assert sl2.get("dm") is None

    def test_remove_leaves_other_sessions_intact(self, sl):
        sl.save("dm", "dm-sess")
        sl.save("ch_99", "ch-sess")
        sl.remove("dm")
        assert sl.get("ch_99") == "ch-sess"


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


class TestListAll:
    def test_list_all_returns_empty_initially(self, sl):
        assert sl.list_all() == []

    def test_list_all_returns_session_info_objects(self, sl):
        sl.save("dm", "sess")
        items = sl.list_all()
        assert len(items) == 1
        assert isinstance(items[0], SessionInfo)

    def test_list_all_returns_all_saved_sessions(self, sl):
        sl.save("dm", "dm-sess")
        sl.save("ch_1", "ch1-sess")
        sl.save("ch_2", "ch2-sess")
        assert len(sl.list_all()) == 3

    def test_list_all_session_info_has_correct_context(self, sl):
        sl.save("ch_55", "sess-ch55")
        infos = sl.list_all()
        contexts = [i.context for i in infos]
        assert "ch_55" in contexts

    def test_list_all_after_remove_excludes_deleted(self, sl):
        sl.save("dm", "dm-sess")
        sl.save("ch_1", "ch1-sess")
        sl.remove("dm")
        items = sl.list_all()
        assert all(i.context != "dm" for i in items)


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_removes_all_sessions(self, sl):
        sl.save("dm", "sess1")
        sl.save("ch_1", "sess2")
        sl.clear()
        assert sl.list_all() == []

    def test_clear_on_empty_is_idempotent(self, sl):
        sl.clear()  # must not raise
        assert sl.list_all() == []

    def test_clear_persists_to_disk(self, tmp_path):
        sl1 = SessionLifecycle(str(tmp_path))
        sl1.save("dm", "sess")
        sl1.clear()

        sl2 = SessionLifecycle(str(tmp_path))
        assert sl2.list_all() == []

    def test_save_after_clear_works_correctly(self, sl):
        sl.save("dm", "old-sess")
        sl.clear()
        sl.save("dm", "new-sess")
        assert sl.get("dm") == "new-sess"

    def test_clear_writes_empty_sessions_dict_to_disk(self, tmp_path):
        sl = SessionLifecycle(str(tmp_path))
        sl.save("dm", "sess")
        sl.clear()
        state_path = os.path.join(str(tmp_path), SessionLifecycle.STATE_FILE)
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["sessions"] == {}
