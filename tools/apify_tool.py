"""Apify Actor execution tools — discover, start, collect."""
from __future__ import annotations

import asyncio  # noqa: F401 — used by _collect_handler
import json     # noqa: F401 — used by handlers
import logging
from typing import Any, Dict, List

from tools.registry import registry

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    """Get attribute or dict key from SDK response objects (apify_client returns either)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    if obj is not None and hasattr(obj, key):
        return getattr(obj, key)
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
    from tools.interrupt import is_interrupted
    if is_interrupted():
        return {"error": "Interrupted"}

    query = (_attr(args, "query") or "").strip() or None
    actor_id = (_attr(args, "actor_id") or "").strip() or None

    if not query and not actor_id:
        return {
            "error": (
                "Provide exactly one of 'query' (to search the Apify Store) "
                "or 'actor_id' (to fetch an Actor's input schema)."
            )
        }

    client = _get_client()

    if actor_id:
        try:
            actor_info = client.actor(actor_id).get()
            if actor_info is None:
                return {
                    "error": (
                        f"Actor '{actor_id}' not found. "
                        "Check the ID format: username~actor-name."
                    )
                }

            builds_result = client.actor(actor_id).builds().list(limit=1, desc=True)
            build_items = _attr(builds_result, "items") or []

            input_schema: Any = None
            readme: Any = None

            if build_items:
                build_item = build_items[0]
                build_id = _attr(build_item, "id")
                build_detail = client.build(build_id).get()
                if build_detail is not None:
                    actor_def = _attr(build_detail, "actorDefinition") or {}
                    raw_schema = _attr(actor_def, "input")
                    if raw_schema:
                        input_schema = json.dumps(raw_schema)
                    else:
                        fallback = _attr(build_detail, "inputSchema")
                        if fallback:
                            input_schema = str(fallback)

                    raw_readme = _attr(actor_def, "readme") or _attr(build_detail, "readme")
                    if raw_readme:
                        readme = str(raw_readme)[:3000]

            username = _attr(actor_info, "username", "")
            name = _attr(actor_info, "name", "")
            return {
                "actor_id": f"{username}~{name}",
                "name": name,
                "description": _attr(actor_info, "description", ""),
                "input_schema": input_schema,
                "readme": readme,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("apify_discover schema fetch error for %s: %s", actor_id, exc)
            return {"error": str(exc)}

    # Store search
    try:
        result = client.store().list(search=query, limit=10)
        items = _attr(result, "items") or []
        actors: List[Dict[str, Any]] = []
        for item in items:
            stats = _attr(item, "stats") or {}
            name = _attr(item, "name", "")
            username = _attr(item, "username", "")
            title = _attr(item, "title") or name
            desc = (_attr(item, "description") or "")[:200]
            run_count = _attr(stats, "totalRuns", 0) or 0
            rating = _attr(stats, "averageRating")
            actors.append({
                "actor_id": f"{username}~{name}",
                "name": title,
                "description": desc,
                "run_count": run_count,
                "rating": rating,
            })
        return {"actors": actors}
    except Exception as exc:  # noqa: BLE001
        logger.warning("apify_discover store search error for '%s': %s", query, exc)
        return {"error": str(exc)}


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
