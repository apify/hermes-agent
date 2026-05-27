"""Apify Actor execution tools — discover, start, collect."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List

from tools.registry import registry

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    """Get attribute or dict key from SDK response objects (apify_client returns either)."""
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def _get_client() -> Any:
    from plugins.web.apify.provider import _get_apify_client
    return _get_apify_client()


def _check_token() -> bool:
    from plugins.web.apify.provider import check_apify_api_key
    return check_apify_api_key()


# ---------------------------------------------------------------------------
# Handlers (stubs — filled in by later tasks)
# ---------------------------------------------------------------------------

def _discover_handler(args: Dict[str, Any]) -> Dict[str, Any]:
    return {}


def _start_handler(args: Dict[str, Any]) -> Dict[str, Any]:
    return {}


async def _collect_handler(args: Dict[str, Any]) -> Dict[str, Any]:
    return {}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_DISCOVER_SCHEMA: Dict[str, Any] = {
    "name": "apify_discover",
    "description": (
        "Search the Apify Store for Actors by keyword, or fetch an Actor's "
        "input schema and README. Provide 'query' to search, or 'actor_id' "
        "to inspect a specific Actor. Actor IDs use tilde: username~actor-name."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords to search the Apify Store (e.g. 'instagram scraper').",
            },
            "actor_id": {
                "type": "string",
                "description": (
                    "Actor ID to fetch its input schema and README "
                    "(e.g. 'apify~google-search-scraper')."
                ),
            },
        },
    },
}

_START_SCHEMA: Dict[str, Any] = {
    "name": "apify_start",
    "description": (
        "Start one or more Apify Actor runs. Returns run references immediately "
        "(fire-and-forget). Pass the returned run refs to apify_collect to get results."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runs": {
                "type": "array",
                "description": "List of Actor runs to start.",
                "items": {
                    "type": "object",
                    "properties": {
                        "actor_id": {
                            "type": "string",
                            "description": "Actor ID (username~actor-name).",
                        },
                        "input": {
                            "type": "object",
                            "description": "Actor input parameters.",
                        },
                        "label": {
                            "type": "string",
                            "description": "Optional label to identify this run in results.",
                        },
                    },
                    "required": ["actor_id"],
                },
            },
        },
        "required": ["runs"],
    },
}

_COLLECT_SCHEMA: Dict[str, Any] = {
    "name": "apify_collect",
    "description": (
        "Poll the status of Apify Actor runs started with apify_start. "
        "Returns completed results, still-running refs, and errors. "
        "Re-call with the same run refs until all_done is true."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "runs": {
                "type": "array",
                "description": "Run references returned by apify_start.",
                "items": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "actor_id": {"type": "string"},
                        "dataset_id": {"type": "string"},
                        "label": {"type": "string"},
                    },
                    "required": ["run_id", "actor_id", "dataset_id"],
                },
            },
        },
        "required": ["runs"],
    },
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

registry.register(
    name="apify_discover",
    toolset="apify",
    schema=_DISCOVER_SCHEMA,
    handler=lambda args, **kw: _discover_handler(args),
    check_fn=_check_token,
    emoji="🔍",
)

registry.register(
    name="apify_start",
    toolset="apify",
    schema=_START_SCHEMA,
    handler=lambda args, **kw: _start_handler(args),
    check_fn=_check_token,
    emoji="▶️",
)

registry.register(
    name="apify_collect",
    toolset="apify",
    schema=_COLLECT_SCHEMA,
    handler=lambda args, **kw: _collect_handler(args),
    check_fn=_check_token,
    is_async=True,
    emoji="📦",
)
