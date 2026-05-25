from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from agent.web_search_provider import WebSearchProvider
from tools.website_policy import check_website_access

logger = logging.getLogger(__name__)

_APIFY_CLIENT_CLS_CACHE: Optional[type] = None


def _load_apify_client_cls() -> type:
    """Import and cache apify_client.ApifyClient (lazy, deferred on first use)."""
    global _APIFY_CLIENT_CLS_CACHE
    if _APIFY_CLIENT_CLS_CACHE is None:
        try:
            from tools.lazy_deps import ensure as _lazy_ensure
            _lazy_ensure("search.apify", prompt=False)
        except ImportError:
            pass
        except Exception as exc:
            raise ImportError(str(exc))
        from apify_client import ApifyClient
        _APIFY_CLIENT_CLS_CACHE = ApifyClient
    return _APIFY_CLIENT_CLS_CACHE


def _get_apify_client() -> Any:
    """Return cached ApifyClient, constructing it from APIFY_API_TOKEN.

    Raises ValueError when APIFY_API_TOKEN is not set.
    Cache stored on tools.web_tools._apify_client so tests can reset it via
    ``tools.web_tools._apify_client = None``.
    """
    import tools.web_tools as _wt

    api_token = os.getenv("APIFY_API_TOKEN", "").strip()
    if not api_token:
        raise ValueError(
            "Apify tools are not configured. "
            "Set APIFY_API_TOKEN (get one at https://apify.com/account/integrations)."
        )

    client_config = ("direct", api_token)
    cached = getattr(_wt, "_apify_client", None)
    cached_config = getattr(_wt, "_apify_client_config", None)
    if cached is not None and cached_config == client_config:
        return cached

    ApifyClient = _load_apify_client_cls()
    _wt._apify_client = ApifyClient(token=api_token)
    _wt._apify_client_config = client_config
    return _wt._apify_client


def _reset_client_for_tests() -> None:
    """Drop cached Apify client so tests can re-instantiate cleanly."""
    import tools.web_tools as _wt
    _wt._apify_client = None
    _wt._apify_client_config = None


def _normalize_rag_search_results(items: List[Any], limit: int) -> List[Dict[str, Any]]:
    """Normalize RAG Web Browser dataset items to the registry web search shape."""
    results: List[Dict[str, Any]] = []
    for i, item in enumerate(items[:limit]):
        if not isinstance(item, dict):
            continue
        sr = item.get("searchResult") or {}
        if not isinstance(sr, dict):
            sr = {}
        title = sr.get("title") or item.get("title", "")
        url = sr.get("url") or item.get("url", "")
        description = sr.get("description") or item.get("markdown", "")
        if description and len(description) > 500:
            description = description[:500]
        results.append({
            "title": title,
            "url": url,
            "description": description,
            "position": i + 1,
        })
    return results


class ApifyWebSearchProvider(WebSearchProvider):
    """Apify web search + extract provider.

    search()   — apify/rag-web-browser Actor (sync, 60s timeout in run input)
    extract()  — apify/website-content-crawler Actor (async, per-URL, 60s asyncio guard)
    crawl()    — not supported in v1 (supports_crawl returns False)
    """

    @property
    def name(self) -> str:
        return "apify"

    @property
    def display_name(self) -> str:
        return "Apify"

    def is_available(self) -> bool:
        return bool(os.getenv("APIFY_API_TOKEN", "").strip())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def supports_crawl(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a web search via Apify RAG Web Browser Actor.

        Sync; blocks until the Actor run completes (up to requestTimeoutSecs).
        """
        from tools.interrupt import is_interrupted

        if is_interrupted():
            return {"success": False, "error": "Interrupted"}

        logger.info("Apify search: '%s' (limit=%d)", query, limit)
        try:
            client = _get_apify_client()
            run = client.actor("apify/rag-web-browser").call(
                run_input={
                    "query": query,
                    "maxResults": limit,
                    "requestTimeoutSecs": 60,
                }
            )
            if run is None:
                return {"success": False, "error": "Apify actor run returned no result"}

            dataset_id = run.get("defaultDatasetId")
            if not dataset_id:
                return {"success": False, "error": "Apify run missing defaultDatasetId"}

            items = client.dataset(dataset_id).list_items().items
            web_results = _normalize_rag_search_results(items, limit)
            logger.info("Apify search: found %d results", len(web_results))
            return {"success": True, "data": {"web": web_results}}
        except Exception as exc:
            logger.warning("Apify search error: %s", exc)
            return {"success": False, "error": f"Apify search failed: {exc}"}

    async def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        try:
            _get_apify_client()
        except (ValueError, ImportError) as exc:
            return [{"url": u, "title": "", "content": "", "raw_content": "", "error": str(exc)} for u in urls]
        raise NotImplementedError("extract() implemented in Task 4")

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Apify",
            "badge": "paid · free tier",
            "tag": (
                "JS-rendered, bot-protected, and geo-gated pages via Apify's "
                "residential proxy infrastructure. Uses RAG Web Browser for search "
                "and Website Content Crawler for extract."
            ),
            "env_vars": [
                {
                    "key": "APIFY_API_TOKEN",
                    "prompt": "Apify API token",
                    "url": "https://apify.com/account/integrations",
                },
            ],
        }
