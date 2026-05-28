"""Tests for tools/apify_tool.py — all mocked, no live Actor calls."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_client(monkeypatch):
    """Patch _get_apify_client to return a MagicMock client.

    NOTE: _attr() checks hasattr() first, which always returns True on MagicMock.
    The 'default' argument in _attr() is never used on MagicMock objects.
    Always explicitly set every attribute you want to read in your tests.
    """
    client = MagicMock()
    monkeypatch.setattr(
        "plugins.web.apify.provider._get_apify_client",
        lambda: client,
    )
    return client


@pytest.fixture(autouse=True)
def not_interrupted(monkeypatch):
    """Default: is_interrupted() returns False."""
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)


# ---------------------------------------------------------------------------
# apify_discover — store search
# ---------------------------------------------------------------------------

class TestDiscoverStoreSearch:
    def test_returns_actors_list(self, mock_client):
        actor_mock = MagicMock()
        actor_mock.username = "apify"
        actor_mock.name = "instagram-scraper"
        actor_mock.title = "Instagram Scraper"
        actor_mock.description = "Scrapes Instagram profiles."
        stats_mock = MagicMock()
        stats_mock.totalRuns = 50000
        stats_mock.averageRating = 4.7
        actor_mock.stats = stats_mock

        list_result = MagicMock()
        list_result.items = [actor_mock]
        mock_client.store.return_value.list.return_value = list_result

        from tools.apify_tool import _discover_handler
        result = _discover_handler({"query": "instagram scraper"})

        assert "actors" in result
        assert len(result["actors"]) == 1
        a = result["actors"][0]
        assert a["actor_id"] == "apify~instagram-scraper"
        assert a["name"] == "Instagram Scraper"
        assert a["run_count"] == 50000
        assert a["rating"] == 4.7
        mock_client.store.return_value.list.assert_called_once_with(
            search="instagram scraper", limit=10
        )

    def test_description_truncated_to_200_chars(self, mock_client):
        actor_mock = MagicMock()
        actor_mock.username = "apify"
        actor_mock.name = "test-actor"
        actor_mock.title = "Test"
        actor_mock.description = "x" * 300
        actor_mock.stats = MagicMock(totalRuns=0, averageRating=None)

        list_result = MagicMock()
        list_result.items = [actor_mock]
        mock_client.store.return_value.list.return_value = list_result

        from tools.apify_tool import _discover_handler
        result = _discover_handler({"query": "test"})

        assert len(result["actors"][0]["description"]) == 200

    def test_store_search_api_error_returns_error_dict(self, mock_client):
        mock_client.store.return_value.list.side_effect = RuntimeError("API error")

        from tools.apify_tool import _discover_handler
        result = _discover_handler({"query": "test"})

        assert "error" in result
        assert "API error" in result["error"]


# ---------------------------------------------------------------------------
# apify_discover — actor schema fetch
# ---------------------------------------------------------------------------

class TestDiscoverActorSchema:
    def _setup_build_mock(self, mock_client, *, input_schema=None, readme=None):
        """Helper: wire up actor + build mock chain."""
        actor_info = MagicMock()
        actor_info.username = "apify"
        actor_info.name = "google-search-scraper"
        actor_info.description = "Scrapes Google Search."
        mock_client.actor.return_value.get.return_value = actor_info

        build_item = MagicMock()
        build_item.id = "build-abc"
        builds_list = MagicMock()
        builds_list.items = [build_item]
        mock_client.actor.return_value.builds.return_value.list.return_value = builds_list

        build_detail = MagicMock()
        actor_def = MagicMock()
        actor_def.input = input_schema  # dict or None
        actor_def.readme = readme
        build_detail.actorDefinition = actor_def
        build_detail.inputSchema = None
        build_detail.readme = None
        mock_client.build.return_value.get.return_value = build_detail

        return actor_info, build_detail

    def test_returns_actor_schema(self, mock_client):
        schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        self._setup_build_mock(mock_client, input_schema=schema, readme="# README content")

        from tools.apify_tool import _discover_handler
        result = _discover_handler({"actor_id": "apify~google-search-scraper"})

        assert result["actor_id"] == "apify~google-search-scraper"
        assert result["name"] == "google-search-scraper"
        assert result["description"] == "Scrapes Google Search."
        assert json.loads(result["input_schema"]) == schema
        assert result["readme"] == "# README content"

    def test_readme_truncated_to_3000_chars(self, mock_client):
        self._setup_build_mock(mock_client, readme="R" * 4000)

        from tools.apify_tool import _discover_handler
        result = _discover_handler({"actor_id": "apify~google-search-scraper"})

        assert len(result["readme"]) == 3000

    def test_falls_back_to_build_input_schema_string(self, mock_client):
        """When actorDefinition.input is None, fall back to build.inputSchema string."""
        _, build_detail = self._setup_build_mock(mock_client, input_schema=None)
        build_detail.inputSchema = '{"type":"object"}'
        build_detail.actorDefinition.input = None

        from tools.apify_tool import _discover_handler
        result = _discover_handler({"actor_id": "apify~google-search-scraper"})

        assert result["input_schema"] == '{"type":"object"}'

    def test_actor_not_found_returns_error(self, mock_client):
        mock_client.actor.return_value.get.return_value = None

        from tools.apify_tool import _discover_handler
        result = _discover_handler({"actor_id": "apify~nonexistent"})

        assert "error" in result
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# apify_discover — validation
# ---------------------------------------------------------------------------

class TestDiscoverValidation:
    def test_missing_both_params_returns_error(self, mock_client):
        from tools.apify_tool import _discover_handler
        result = _discover_handler({})
        assert "error" in result
        assert "query" in result["error"]
        assert "actor_id" in result["error"]

    def test_empty_string_params_treated_as_missing(self, mock_client):
        from tools.apify_tool import _discover_handler
        result = _discover_handler({"query": "  ", "actor_id": ""})
        assert "error" in result

    def test_interrupted_returns_error(self, mock_client, monkeypatch):
        monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: True)
        from tools.apify_tool import _discover_handler
        result = _discover_handler({"query": "test"})
        assert result == {"error": "Interrupted"}
        mock_client.store.assert_not_called()


# ---------------------------------------------------------------------------
# apify_start
# ---------------------------------------------------------------------------

class TestStart:
    def _make_run_mock(self, run_id="r1", dataset_id="d1", status="QUEUED"):
        run = MagicMock()
        run.id = run_id
        run.default_dataset_id = dataset_id
        run.status = status
        return run

    def test_single_run_returns_run_ref(self, mock_client):
        mock_client.actor.return_value.start.return_value = self._make_run_mock()

        from tools.apify_tool import _start_handler
        result = _start_handler({
            "runs": [{"actor_id": "apify~test", "input": {"key": "val"}}]
        })

        assert "runs" in result
        assert len(result["runs"]) == 1
        r = result["runs"][0]
        assert r["run_id"] == "r1"
        assert r["actor_id"] == "apify~test"
        assert r["dataset_id"] == "d1"
        assert r["status"] == "QUEUED"
        mock_client.actor.return_value.start.assert_called_once_with(
            run_input={"key": "val"}
        )

    def test_label_included_when_provided(self, mock_client):
        mock_client.actor.return_value.start.return_value = self._make_run_mock()

        from tools.apify_tool import _start_handler
        result = _start_handler({
            "runs": [{"actor_id": "apify~test", "input": {}, "label": "my-run"}]
        })

        assert result["runs"][0]["label"] == "my-run"

    def test_label_absent_when_not_provided(self, mock_client):
        mock_client.actor.return_value.start.return_value = self._make_run_mock()

        from tools.apify_tool import _start_handler
        result = _start_handler({
            "runs": [{"actor_id": "apify~test", "input": {}}]
        })

        assert "label" not in result["runs"][0]

    def test_batch_start_two_runs(self, mock_client):
        run1 = self._make_run_mock(run_id="r1", dataset_id="d1")
        run2 = self._make_run_mock(run_id="r2", dataset_id="d2")
        mock_client.actor.return_value.start.side_effect = [run1, run2]

        from tools.apify_tool import _start_handler
        result = _start_handler({
            "runs": [
                {"actor_id": "apify~actor-a", "input": {}},
                {"actor_id": "apify~actor-b", "input": {}},
            ]
        })

        assert len(result["runs"]) == 2
        assert result["runs"][0]["run_id"] == "r1"
        assert result["runs"][1]["run_id"] == "r2"

    def test_per_run_api_error_goes_to_errors(self, mock_client):
        mock_client.actor.return_value.start.side_effect = RuntimeError("not found")

        from tools.apify_tool import _start_handler
        result = _start_handler({
            "runs": [{"actor_id": "apify~bad-actor", "input": {}}]
        })

        assert result["runs"] == []
        assert len(result["errors"]) == 1
        assert "not found" in result["errors"][0]["error"]

    def test_interrupted_returns_early(self, mock_client, monkeypatch):
        monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: True)

        from tools.apify_tool import _start_handler
        result = _start_handler({"runs": [{"actor_id": "apify~test", "input": {}}]})

        assert result == {"error": "Interrupted"}
        mock_client.actor.assert_not_called()


# ---------------------------------------------------------------------------
# apify_collect — non-terminal states and errors
# ---------------------------------------------------------------------------

class TestCollectNonTerminal:
    @pytest.mark.asyncio
    async def test_running_run_goes_to_pending(self, mock_client):
        run_info = MagicMock()
        run_info.status = "RUNNING"
        mock_client.run.return_value.get.return_value = run_info

        from tools.apify_tool import _collect_handler
        result = await _collect_handler({
            "runs": [{"run_id": "r1", "actor_id": "apify~test", "dataset_id": "d1"}]
        })

        assert result["all_done"] is False
        assert len(result["pending"]) == 1
        assert result["pending"][0]["run_id"] == "r1"
        assert result["pending"][0]["status"] == "RUNNING"
        assert result["completed"] == []
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_queued_run_goes_to_pending(self, mock_client):
        run_info = MagicMock()
        run_info.status = "QUEUED"
        mock_client.run.return_value.get.return_value = run_info

        from tools.apify_tool import _collect_handler
        result = await _collect_handler({
            "runs": [{"run_id": "r1", "actor_id": "apify~test", "dataset_id": "d1"}]
        })

        assert result["all_done"] is False
        assert result["pending"][0]["status"] == "QUEUED"

    @pytest.mark.asyncio
    async def test_failed_run_goes_to_errors(self, mock_client):
        run_info = MagicMock()
        run_info.status = "FAILED"
        mock_client.run.return_value.get.return_value = run_info

        from tools.apify_tool import _collect_handler
        result = await _collect_handler({
            "runs": [{"run_id": "r1", "actor_id": "apify~test", "dataset_id": "d1"}]
        })

        assert result["all_done"] is True  # no pending
        assert result["errors"][0]["status"] == "FAILED"
        assert "FAILED" in result["errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_aborted_run_goes_to_errors(self, mock_client):
        run_info = MagicMock()
        run_info.status = "ABORTED"
        mock_client.run.return_value.get.return_value = run_info

        from tools.apify_tool import _collect_handler
        result = await _collect_handler({
            "runs": [{"run_id": "r1", "actor_id": "apify~test", "dataset_id": "d1"}]
        })

        assert result["errors"][0]["status"] == "ABORTED"

    @pytest.mark.asyncio
    async def test_run_not_found_goes_to_errors(self, mock_client):
        mock_client.run.return_value.get.return_value = None

        from tools.apify_tool import _collect_handler
        result = await _collect_handler({
            "runs": [{"run_id": "r1", "actor_id": "apify~test", "dataset_id": "d1"}]
        })

        assert "not found" in result["errors"][0]["error"]

    @pytest.mark.asyncio
    async def test_label_preserved_in_pending(self, mock_client):
        run_info = MagicMock()
        run_info.status = "RUNNING"
        mock_client.run.return_value.get.return_value = run_info

        from tools.apify_tool import _collect_handler
        result = await _collect_handler({
            "runs": [{"run_id": "r1", "actor_id": "apify~test", "dataset_id": "d1", "label": "instagram"}]
        })

        assert result["pending"][0]["label"] == "instagram"


# ---------------------------------------------------------------------------
# apify_collect — succeeded path + external content wrapping
# ---------------------------------------------------------------------------

class TestCollectSucceeded:
    @pytest.mark.asyncio
    async def test_succeeded_fetches_dataset_and_wraps_content(self, mock_client):
        run_info = MagicMock()
        run_info.status = "SUCCEEDED"
        mock_client.run.return_value.get.return_value = run_info

        items = [{"title": "Result 1", "url": "https://example.com"}]
        dataset_result = MagicMock()
        dataset_result.items = items
        mock_client.dataset.return_value.list_items.return_value = dataset_result

        from tools.apify_tool import _collect_handler
        result = await _collect_handler({
            "runs": [{"run_id": "r1", "actor_id": "apify~test", "dataset_id": "d1"}]
        })

        assert result["all_done"] is True
        assert len(result["completed"]) == 1
        c = result["completed"][0]
        assert c["status"] == "SUCCEEDED"
        assert c["result_count"] == 1
        assert "<<<EXTERNAL_UNTRUSTED_CONTENT>>>" in c["data"]
        assert "<<<END_EXTERNAL_UNTRUSTED_CONTENT>>>" in c["data"]
        assert "Result 1" in c["data"]
        mock_client.dataset.return_value.list_items.assert_called_once_with(limit=100)

    @pytest.mark.asyncio
    async def test_dataset_content_truncated_at_50000_chars(self, mock_client):
        run_info = MagicMock()
        run_info.status = "SUCCEEDED"
        mock_client.run.return_value.get.return_value = run_info

        # Create items whose JSON serialization exceeds 50k chars
        items = [{"data": "x" * 1000} for _ in range(100)]
        dataset_result = MagicMock()
        dataset_result.items = items
        mock_client.dataset.return_value.list_items.return_value = dataset_result

        from tools.apify_tool import _collect_handler
        result = await _collect_handler({
            "runs": [{"run_id": "r1", "actor_id": "apify~test", "dataset_id": "d1"}]
        })

        raw_data = result["completed"][0]["data"]
        # Strip markers to measure just the content length
        content = raw_data.replace("<<<EXTERNAL_UNTRUSTED_CONTENT>>>\n", "").replace(
            "\n<<<END_EXTERNAL_UNTRUSTED_CONTENT>>>", ""
        )
        assert "[…truncated]" in content

    @pytest.mark.asyncio
    async def test_all_done_true_when_only_succeeded(self, mock_client):
        run_info = MagicMock()
        run_info.status = "SUCCEEDED"
        mock_client.run.return_value.get.return_value = run_info
        mock_client.dataset.return_value.list_items.return_value = MagicMock(items=[])

        from tools.apify_tool import _collect_handler
        result = await _collect_handler({
            "runs": [{"run_id": "r1", "actor_id": "apify~test", "dataset_id": "d1"}]
        })

        assert result["all_done"] is True
        assert result["pending"] == []
