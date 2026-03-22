"""Tests for homunculus.core.store.MessageStore.

Covers insert/fetch_pending, duplicate detection, status transitions
(pending → processing → done/failed), stale recovery, and
get_pending_contexts.
"""

from __future__ import annotations

import time

import pytest

from homunculus.core.store import InsertResult, MessageStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    """Fresh MessageStore backed by a temp directory."""
    s = MessageStore(str(tmp_path))
    yield s
    s.close()


def _insert(
    store: MessageStore,
    message_id: int,
    context: str = "dm",
    text: str = "hello",
    channel_id: int = 1,
) -> InsertResult:
    return store.insert(
        message_id=message_id,
        channel_id=channel_id,
        context=context,
        text=text,
        timestamp="2024-01-01 12:00:00",
    )


# ---------------------------------------------------------------------------
# insert + fetch_pending
# ---------------------------------------------------------------------------


class TestInsertAndFetchPending:
    def test_insert_returns_inserted_on_first_call(self, store):
        result = _insert(store, 1)
        assert result == InsertResult.INSERTED

    def test_fetched_message_has_correct_text(self, store):
        _insert(store, 1, text="my instruction")
        rows = store.fetch_pending(context="dm")
        assert rows[0]["text"] == "my instruction"

    def test_fetched_message_has_correct_context(self, store):
        _insert(store, 1, context="ch_42")
        rows = store.fetch_pending(context="ch_42")
        assert rows[0]["context"] == "ch_42"

    def test_fetch_pending_does_not_cross_contexts(self, store):
        _insert(store, 1, context="dm")
        _insert(store, 2, context="ch_42")
        dm_rows = store.fetch_pending(context="dm")
        assert all(r["context"] == "dm" for r in dm_rows)

    def test_fetch_pending_respects_limit(self, store):
        for i in range(10):
            _insert(store, i, context="dm")
        rows = store.fetch_pending(context="dm", limit=3)
        assert len(rows) == 3

    def test_fetch_pending_returns_empty_when_no_pending(self, store):
        rows = store.fetch_pending(context="dm")
        assert rows == []

    def test_files_field_deserialized_as_list(self, store):
        store.insert(
            message_id=99,
            channel_id=1,
            context="dm",
            files=[{"path": "/tmp/a.txt", "size": 10}],
            timestamp="2024-01-01 12:00:00",
        )
        rows = store.fetch_pending(context="dm")
        assert isinstance(rows[0]["files"], list)


# ---------------------------------------------------------------------------
# Duplicate insert returns DUPLICATE
# ---------------------------------------------------------------------------


class TestDuplicateInsert:
    def test_duplicate_message_id_returns_duplicate(self, store):
        _insert(store, 1)
        result = _insert(store, 1)
        assert result == InsertResult.DUPLICATE

    def test_duplicate_does_not_create_extra_row(self, store):
        _insert(store, 1)
        _insert(store, 1)
        # Transition to processing so we can fetch and count.
        rows = store.fetch_pending(context="dm")
        assert len(rows) == 1

    def test_different_message_ids_are_not_duplicates(self, store):
        r1 = _insert(store, 1)
        r2 = _insert(store, 2)
        assert r1 == InsertResult.INSERTED
        assert r2 == InsertResult.INSERTED


# ---------------------------------------------------------------------------
# fetch_pending transitions status to processing
# ---------------------------------------------------------------------------


class TestFetchPendingTransition:
    def test_fetch_pending_sets_status_to_processing(self, store):
        _insert(store, 1)
        store.fetch_pending(context="dm")
        # A second fetch should return nothing (already processing).
        rows = store.fetch_pending(context="dm")
        assert rows == []

    def test_fetch_pending_returns_rows_ordered_by_message_id(self, store):
        for mid in [5, 1, 3]:
            _insert(store, mid)
        rows = store.fetch_pending(context="dm")
        ids = [r["message_id"] for r in rows]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# mark_done_batch
# ---------------------------------------------------------------------------


class TestMarkDoneBatch:
    def test_mark_done_removes_from_pending(self, store):
        _insert(store, 1)
        _insert(store, 2)
        store.fetch_pending(context="dm")   # transitions to processing
        store.mark_done_batch([1, 2])
        # Nothing left in pending.
        rows = store.fetch_pending(context="dm")
        assert rows == []

    def test_mark_done_empty_list_does_not_raise(self, store):
        store.mark_done_batch([])  # must not raise

    def test_mark_done_nonexistent_ids_does_not_raise(self, store):
        store.mark_done_batch([9999])  # must not raise


# ---------------------------------------------------------------------------
# mark_failed with retry (< 3 → pending)
# ---------------------------------------------------------------------------


class TestMarkFailedWithRetry:
    def test_first_failure_transitions_back_to_pending(self, store):
        _insert(store, 1)
        store.fetch_pending(context="dm")  # → processing
        store.mark_failed(1, "temporary error")
        # Back to pending; a fresh fetch should return it.
        rows = store.fetch_pending(context="dm")
        assert len(rows) == 1
        assert rows[0]["message_id"] == 1

    def test_second_failure_still_pending(self, store):
        _insert(store, 1)
        store.fetch_pending(context="dm")
        store.mark_failed(1, "err1")
        store.fetch_pending(context="dm")  # back to processing
        store.mark_failed(1, "err2")
        rows = store.fetch_pending(context="dm")
        assert len(rows) == 1

    def test_mark_failed_nonexistent_id_does_not_raise(self, store):
        store.mark_failed(9999, "error")  # must not raise


# ---------------------------------------------------------------------------
# mark_failed exhausted (>= 3 → failed)
# ---------------------------------------------------------------------------


class TestMarkFailedExhausted:
    def _exhaust(self, store: MessageStore, message_id: int) -> None:
        """Fail a message three times so it reaches the 'failed' status."""
        _insert(store, message_id)
        for _ in range(3):
            rows = store.fetch_pending(context="dm")
            assert any(r["message_id"] == message_id for r in rows), (
                "Expected message to be pending for retry"
            )
            store.mark_failed(message_id, "persistent error")

    def test_third_failure_transitions_to_failed_status(self, store):
        self._exhaust(store, 1)
        # Message should NOT appear in pending anymore.
        rows = store.fetch_pending(context="dm")
        assert not any(r["message_id"] == 1 for r in rows)

    def test_failed_message_does_not_interfere_with_other_messages(self, store):
        self._exhaust(store, 1)
        _insert(store, 2)  # fresh message inserted AFTER msg 1 is exhausted
        rows = store.fetch_pending(context="dm")
        assert any(r["message_id"] == 2 for r in rows)


# ---------------------------------------------------------------------------
# recover_stale
# ---------------------------------------------------------------------------


class TestRecoverStale:
    def test_recover_stale_returns_zero_when_none_stale(self, store):
        count = store.recover_stale(timeout_min=0)
        assert count == 0

    def test_recover_stale_resets_processing_to_pending(self, store):
        _insert(store, 1)
        store.fetch_pending(context="dm")  # → processing

        # Use timeout_min=0 which selects messages updated before "now - 0 min",
        # i.e. the sqlite expression datetime('now','localtime','-0 minutes').
        # In practice this catches messages whose updated_at is in the past,
        # which our freshly inserted row should satisfy at wall-clock speed.
        # We give the DB a tiny moment to commit before checking.
        recovered = store.recover_stale(timeout_min=0)
        if recovered == 0:
            # On some systems the timestamp granularity is coarser than a
            # sub-second test run.  Insert a known-old row via direct SQL.
            store._conn.execute(
                "UPDATE messages SET updated_at=datetime('now','localtime','-1 hour') "
                "WHERE message_id=1"
            )
            recovered = store.recover_stale(timeout_min=1)

        assert recovered >= 1
        rows = store.fetch_pending(context="dm")
        assert any(r["message_id"] == 1 for r in rows)

    def test_recover_stale_leaves_done_messages_untouched(self, store):
        _insert(store, 1)
        store.fetch_pending(context="dm")
        store.mark_done_batch([1])
        recovered = store.recover_stale(timeout_min=0)
        rows = store.fetch_pending(context="dm")
        assert rows == []


# ---------------------------------------------------------------------------
# get_pending_contexts
# ---------------------------------------------------------------------------


class TestGetPendingContexts:
    def test_returns_empty_when_no_pending_messages(self, store):
        assert store.get_pending_contexts() == []

    def test_returns_distinct_contexts_with_pending_messages(self, store):
        _insert(store, 1, context="dm")
        _insert(store, 2, context="ch_100")
        _insert(store, 3, context="ch_100")  # duplicate context
        contexts = store.get_pending_contexts()
        assert set(contexts) == {"dm", "ch_100"}

    def test_processing_messages_excluded_from_pending_contexts(self, store):
        _insert(store, 1, context="dm")
        store.fetch_pending(context="dm")  # → processing
        contexts = store.get_pending_contexts()
        assert "dm" not in contexts

    def test_done_messages_excluded_from_pending_contexts(self, store):
        _insert(store, 1, context="dm")
        store.fetch_pending(context="dm")
        store.mark_done_batch([1])
        contexts = store.get_pending_contexts()
        assert "dm" not in contexts
