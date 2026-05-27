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
