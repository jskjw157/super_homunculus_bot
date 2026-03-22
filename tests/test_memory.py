"""Tests for homunculus.core.memory.MemoryManager.

Covers workspace creation, upsert/search by keyword and msg_id,
manifest reservation, load_all discovery, and load_relevant ranking.
"""

from __future__ import annotations

import json
import os

import pytest

from homunculus.core.memory import MemoryManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mm(tmp_path) -> MemoryManager:
    return MemoryManager(str(tmp_path))


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# workspace_path
# ---------------------------------------------------------------------------


class TestWorkspacePath:
    def test_workspace_path_creates_directory(self, tmp_path):
        mm = _make_mm(tmp_path)
        ws = mm.workspace_path(msg_id=1)
        assert os.path.isdir(ws)

    def test_workspace_path_without_context_is_under_workspace_root(self, tmp_path):
        mm = _make_mm(tmp_path)
        ws = mm.workspace_path(msg_id=42)
        assert ws == os.path.join(str(tmp_path), "workspace", "job_42")

    def test_workspace_path_with_context_is_under_context_subdir(self, tmp_path):
        mm = _make_mm(tmp_path)
        ws = mm.workspace_path(msg_id=99, ctx="dm")
        assert ws == os.path.join(str(tmp_path), "workspace", "dm", "job_99")

    def test_workspace_path_with_channel_context(self, tmp_path):
        mm = _make_mm(tmp_path)
        ws = mm.workspace_path(msg_id=7, ctx="ch_555")
        assert "ch_555" in ws and "job_7" in ws

    def test_workspace_path_is_idempotent(self, tmp_path):
        mm = _make_mm(tmp_path)
        ws1 = mm.workspace_path(msg_id=1)
        ws2 = mm.workspace_path(msg_id=1)
        assert ws1 == ws2

    def test_invalid_context_raises_value_error(self, tmp_path):
        mm = _make_mm(tmp_path)
        with pytest.raises(ValueError):
            mm.workspace_path(msg_id=1, ctx="invalid_ctx!")


# ---------------------------------------------------------------------------
# upsert + search by keyword
# ---------------------------------------------------------------------------


class TestUpsertAndSearchByKeyword:
    def test_upsert_then_search_by_keyword_returns_match(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.upsert(1, "build a website for my cafe")
        results = mm.search(keyword="cafe")
        assert len(results) == 1
        assert results[0]["msg_id"] == 1

    def test_search_by_keyword_is_case_insensitive(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.upsert(2, "Deploy the Docker container")
        results = mm.search(keyword="docker")
        assert len(results) == 1

    def test_search_by_keyword_partial_match_in_instruction(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.upsert(3, "analyse the quarterly report")
        results = mm.search(keyword="quarterly")
        assert any(r["msg_id"] == 3 for r in results)

    def test_search_by_keyword_no_match_returns_empty(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.upsert(4, "build a landing page")
        results = mm.search(keyword="nonexistent_xyz")
        assert results == []

    def test_upsert_updates_existing_entry(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.upsert(5, "original instruction", result="")
        mm.upsert(5, "original instruction", result="done!")
        results = mm.search(msg_id=5)
        assert len(results) == 1
        assert results[0]["result"] == "done!"

    def test_upsert_stores_files_list(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.upsert(6, "generate report", files=["report.pdf", "chart.png"])
        results = mm.search(msg_id=6)
        assert results[0]["files"] == ["report.pdf", "chart.png"]

    def test_search_no_args_returns_all(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.upsert(10, "task A")
        mm.upsert(11, "task B")
        results = mm.search()
        assert len(results) == 2

    def test_index_is_sorted_newest_first_by_msg_id(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.upsert(1, "old task")
        mm.upsert(100, "new task")
        results = mm.search()
        assert results[0]["msg_id"] == 100


# ---------------------------------------------------------------------------
# upsert + search by msg_id
# ---------------------------------------------------------------------------


class TestSearchByMsgId:
    def test_search_by_msg_id_returns_exact_match(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.upsert(42, "some instruction")
        results = mm.search(msg_id=42)
        assert len(results) == 1
        assert results[0]["msg_id"] == 42

    def test_search_by_msg_id_nonexistent_returns_empty(self, tmp_path):
        mm = _make_mm(tmp_path)
        results = mm.search(msg_id=9999)
        assert results == []

    def test_search_by_msg_id_ignores_other_entries(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.upsert(1, "task one")
        mm.upsert(2, "task two")
        results = mm.search(msg_id=1)
        assert len(results) == 1
        assert results[0]["msg_id"] == 1

    def test_search_by_context_filters_correctly(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.upsert(1, "dm task", ctx="dm")
        mm.upsert(2, "channel task", ctx="ch_100")
        dm_results = mm.search(ctx="dm")
        assert all(r["context"] == "dm" for r in dm_results)
        assert len(dm_results) == 1


# ---------------------------------------------------------------------------
# reserve creates manifest file
# ---------------------------------------------------------------------------


class TestReserve:
    def test_reserve_creates_manifest_file(self, tmp_path):
        mm = _make_mm(tmp_path)
        ws = mm.reserve(
            instruction="build dashboard",
            chat_id=12345,
            timestamps=["2024-01-01 12:00:00"],
            msg_ids=[1],
        )
        manifest = os.path.join(ws, MemoryManager.MANIFEST_NAME)
        assert os.path.isfile(manifest)

    def test_reserve_manifest_contains_instruction(self, tmp_path):
        mm = _make_mm(tmp_path)
        ws = mm.reserve(
            instruction="generate weekly report",
            chat_id=99,
            timestamps=["2024-06-01 09:00:00"],
            msg_ids=[7],
        )
        manifest_text = open(os.path.join(ws, MemoryManager.MANIFEST_NAME)).read()
        assert "generate weekly report" in manifest_text

    def test_reserve_manifest_shows_in_progress_result(self, tmp_path):
        mm = _make_mm(tmp_path)
        ws = mm.reserve(
            instruction="something",
            chat_id=1,
            timestamps=["2024-01-01 00:00:00"],
            msg_ids=[1],
        )
        manifest_text = open(os.path.join(ws, MemoryManager.MANIFEST_NAME)).read()
        assert "in progress" in manifest_text

    def test_reserve_also_updates_index(self, tmp_path):
        mm = _make_mm(tmp_path)
        mm.reserve(
            instruction="index me",
            chat_id=1,
            timestamps=["2024-01-01 00:00:00"],
            msg_ids=[55],
        )
        results = mm.search(msg_id=55)
        assert len(results) == 1

    def test_reserve_with_multiple_msg_ids_records_merged_info(self, tmp_path):
        mm = _make_mm(tmp_path)
        ws = mm.reserve(
            instruction="merged task",
            chat_id=1,
            timestamps=["2024-01-01 00:00:00", "2024-01-01 00:01:00"],
            msg_ids=[10, 11],
            source="Discord",
        )
        manifest_text = open(os.path.join(ws, MemoryManager.MANIFEST_NAME)).read()
        assert "merged" in manifest_text.lower() or "10" in manifest_text

    def test_reserve_with_context_places_workspace_correctly(self, tmp_path):
        mm = _make_mm(tmp_path)
        ws = mm.reserve(
            instruction="channel task",
            chat_id=1,
            timestamps=["2024-01-01 00:00:00"],
            msg_ids=[20],
            ctx="ch_999",
        )
        assert "ch_999" in ws


# ---------------------------------------------------------------------------
# load_all finds manifests
# ---------------------------------------------------------------------------


class TestLoadAll:
    def _populate(self, mm: MemoryManager, entries: list[tuple]) -> None:
        """Helper: reserve workspaces for (msg_id, ctx, instruction) tuples."""
        for msg_id, ctx, instruction in entries:
            mm.reserve(
                instruction=instruction,
                chat_id=1,
                timestamps=["2024-01-01 00:00:00"],
                msg_ids=[msg_id],
                ctx=ctx,
            )

    def test_load_all_returns_empty_when_no_workspace(self, tmp_path):
        mm = _make_mm(tmp_path)
        assert mm.load_all() == []

    def test_load_all_finds_reserved_manifests(self, tmp_path):
        mm = _make_mm(tmp_path)
        self._populate(mm, [(1, "dm", "task one"), (2, "dm", "task two")])
        results = mm.load_all(ctx="dm")
        assert len(results) == 2

    def test_load_all_sorted_newest_first(self, tmp_path):
        mm = _make_mm(tmp_path)
        self._populate(mm, [(1, "dm", "old"), (50, "dm", "new")])
        results = mm.load_all(ctx="dm")
        assert results[0]["msg_id"] == 50

    def test_load_all_filtered_by_context(self, tmp_path):
        mm = _make_mm(tmp_path)
        self._populate(mm, [(1, "dm", "dm task"), (2, "ch_100", "ch task")])
        dm_results = mm.load_all(ctx="dm")
        assert all(r["context"] == "dm" for r in dm_results)

    def test_load_all_with_no_ctx_includes_all_contexts(self, tmp_path):
        mm = _make_mm(tmp_path)
        self._populate(mm, [(1, "dm", "dm task"), (2, "ch_100", "ch task")])
        results = mm.load_all()
        contexts = {r["context"] for r in results}
        assert "dm" in contexts
        assert "ch_100" in contexts

    def test_load_all_result_contains_content_field(self, tmp_path):
        mm = _make_mm(tmp_path)
        self._populate(mm, [(3, "dm", "check content")])
        results = mm.load_all(ctx="dm")
        assert "content" in results[0]
        assert len(results[0]["content"]) > 0


# ---------------------------------------------------------------------------
# load_relevant returns most recent + keyword-matched
# ---------------------------------------------------------------------------


class TestLoadRelevant:
    def _seed(self, mm: MemoryManager, count: int, ctx: str = "dm") -> None:
        for i in range(1, count + 1):
            mm.reserve(
                instruction=f"task number {i}",
                chat_id=1,
                timestamps=[f"2024-{i:02d}-01 00:00:00" if i <= 12 else "2024-12-01 00:00:00"],
                msg_ids=[i],
                ctx=ctx,
            )

    def test_load_relevant_returns_empty_when_no_manifests(self, tmp_path):
        mm = _make_mm(tmp_path)
        result = mm.load_relevant("anything")
        assert result == []

    def test_load_relevant_always_includes_most_recent(self, tmp_path):
        mm = _make_mm(tmp_path)
        self._seed(mm, 10)
        results = mm.load_relevant("something unrelated", ctx="dm")
        msg_ids = [r["msg_id"] for r in results]
        # The three highest msg_ids (8, 9, 10) should appear.
        for mid in [10, 9, 8]:
            assert mid in msg_ids

    def test_load_relevant_scores_by_keyword_overlap(self, tmp_path):
        mm = _make_mm(tmp_path)
        # Plant a specific keyword in msg_id 1 which is otherwise old.
        mm.reserve(
            instruction="deploy the kubernetes cluster",
            chat_id=1,
            timestamps=["2024-01-01 00:00:00"],
            msg_ids=[1],
            ctx="dm",
        )
        # Add several more recent but unrelated tasks.
        for i in range(2, 8):
            mm.reserve(
                instruction=f"unrelated task {i}",
                chat_id=1,
                timestamps=[f"2024-0{i}-01 00:00:00"],
                msg_ids=[i],
                ctx="dm",
            )
        # Query with the unique keyword — entry 1 should score and be included.
        results = mm.load_relevant("kubernetes deployment", ctx="dm")
        msg_ids = [r["msg_id"] for r in results]
        assert 1 in msg_ids

    def test_load_relevant_respects_max_chars(self, tmp_path):
        mm = _make_mm(tmp_path)
        self._seed(mm, 20)
        results = mm.load_relevant("task", max_chars=100, ctx="dm")
        total_chars = sum(len(r["content"]) for r in results)
        assert total_chars <= 100

    def test_load_relevant_respects_max_items(self, tmp_path):
        mm = _make_mm(tmp_path)
        self._seed(mm, 20)
        results = mm.load_relevant("task", max_items=5, ctx="dm")
        assert len(results) <= 5
