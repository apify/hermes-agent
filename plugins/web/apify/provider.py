"""Apify web search + extract + crawl — plugin form.

Subclasses :class:`agent.web_search_provider.WebSearchProvider`. Three
capabilities advertised:

- ``supports_search()``  -> True  (apify/rag-web-browser Actor)
- ``supports_extract()`` -> True  (apify/website-content-crawler, single-page)
- ``supports_crawl()``   -> True  (apify/website-content-crawler, multi-page)

search() is sync; extract() and crawl() are async (each Actor call runs in
``asyncio.to_thread`` with an ``asyncio.wait_for`` guard).

Config keys this provider responds to::

    web:
      search_backend: "apify"     # explicit per-capability
      extract_backend: "apify"    # explicit per-capability
      backend: "apify"            # shared fallback

Env vars::

    APIFY_API_TOKEN=...          # https://apify.com/account/integrations
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from agent.web_search_provider import WebSearchProvider
from tools.apify_client import check_apify_api_key, get_apify_client
from tools.website_policy import check_website_access

logger = logging.getLogger(__name__)

_RAG_ACTOR = "apify/rag-web-browser"
_WCC_ACTOR = "apify/website-content-crawler"

_get_apify_client = get_apify_client  # alias kept for tests that patch this name


# ---------------------------------------------------------------------------
# Actor execution
# ---------------------------------------------------------------------------


def _run_actor_blocking(actor_id: str, run_input: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Start an Apify Actor, wait for completion, return dataset items.

    Blocking — intended to be called via asyncio.to_thread from async methods.
    Returns [] when the Actor succeeds but produces no dataset items.
    Raises RuntimeError on non-SUCCEEDED status so callers can surface the failure.
    Raises ValueError if the client is unconfigured.
    """
    client = _get_apify_client()
    started = client.actor(actor_id).start(run_input=run_input)
    run_id = started.id
    logger.info("Apify %s started — https://console.apify.com/actors/runs/%s", actor_id, run_id)
    run = client.run(run_id).wait_for_finish()
    if run is None:
        raise RuntimeError(
            f"Apify {actor_id} run {run_id}: wait_for_finish returned None — "
            f"check https://console.apify.com/actors/runs/{run_id}"
        )
    if run.status != "SUCCEEDED":
        raise RuntimeError(
            f"Apify {actor_id} run {run_id} finished with status {run.status} — "
            f"check https://console.apify.com/actors/runs/{run_id} for details"
        )
    dataset_id = run.default_dataset_id
    if not dataset_id:
        return []
    return list(client.dataset(dataset_id).list_items().items)


def _run_wcc_crawl(url: str, max_pages: int, max_depth: int) -> List[Dict[str, Any]]:
    """Multi-page crawl via apify/website-content-crawler.

    Intended to be called via asyncio.to_thread from crawl().
    Returns raw dataset items (plain dicts).
    """
    return _run_actor_blocking(
        _WCC_ACTOR,
        {
            "startUrls": [{"url": url}],
            "maxCrawlPages": max_pages,
            "maxCrawlDepth": max_depth,
            "outputFormats": ["markdown"],
            "saveMarkdown": True,
            "saveHtml": False,
        },
    )


def _run_website_content_crawler(url: str, output_formats: List[str]) -> Optional[Dict[str, Any]]:
    """Single-page extract via apify/website-content-crawler.

    Intended to be called via asyncio.to_thread from extract().
    Returns the first dataset item, or None if the Actor returned no items.
    """
    items = _run_actor_blocking(
        _WCC_ACTOR,
        {
            "startUrls": [{"url": url}],
            "maxCrawlPages": 1,
            "outputFormats": output_formats,
        },
    )
    if not items:
        return None
    item = items[0]
    return item if isinstance(item, dict) else None


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


def _normalize_rag_search_results(items: List[Any], limit: int) -> List[Dict[str, Any]]:
    """Normalize RAG Web Browser dataset items to the registry web search shape."""
    results: List[Dict[str, Any]] = []
    for item in items[:limit]:
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
            "position": len(results) + 1,
        })
    return results


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class ApifyWebSearchProvider(WebSearchProvider):
    """Apify web search + extract + crawl provider.

    search()   — apify/rag-web-browser Actor (sync, 60s timeout in run input)
    extract()  — apify/website-content-crawler Actor (async, per-URL, 60s asyncio guard)
    crawl()    — apify/website-content-crawler Actor (async, multi-page, 300s ceiling)
    """

    @property
    def name(self) -> str:
        return "apify"

    @property
    def display_name(self) -> str:
        return "Apify"

    def is_available(self) -> bool:
        return check_apify_api_key()

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def supports_crawl(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a web search via Apify RAG Web Browser Actor.

        Sync; blocks until the Actor run completes (up to requestTimeoutSecs).
        """
        from tools.interrupt import is_interrupted

        if is_interrupted():
            return {"success": False, "error": "Interrupted"}

        logger.info("Apify search: '%s' (limit=%d)", query, limit)
        try:
            items = _run_actor_blocking(
                _RAG_ACTOR,
                {
                    "query": query,
                    "maxResults": limit,
                    "requestTimeoutSecs": 60,
                },
            )
            web_results = _normalize_rag_search_results(items, limit)
            logger.info("Apify search: found %d results", len(web_results))
            return {"success": True, "data": {"web": web_results}}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Apify search error: %s", exc)
            return {"success": False, "error": f"Apify search failed: {exc}"}

    async def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Extract content from URLs via Apify Website Content Crawler.

        Each URL is crawled in a background thread (asyncio.to_thread) with a
        60s asyncio.wait_for guard. Website-access policy is checked before
        each Actor call. Per-URL failures are returned as error items, not raised.

        kwargs:
          format: "markdown" | "html" | None (default: both)
        """
        from tools.interrupt import is_interrupted as _is_interrupted

        if _is_interrupted():
            return [{"url": u, "error": "Interrupted", "title": ""} for u in urls]

        fmt = kwargs.get("format")
        if fmt == "markdown":
            output_formats = ["markdown"]
        elif fmt == "html":
            output_formats = ["html"]
        else:
            output_formats = ["markdown", "html"]

        results: List[Dict[str, Any]] = []

        for url in urls:
            if _is_interrupted():
                results.append({"url": url, "error": "Interrupted", "title": ""})
                continue

            blocked = check_website_access(url)
            if blocked:
                logger.info(
                    "Blocked web_extract for %s by rule %s",
                    blocked["host"],
                    blocked["rule"],
                )
                results.append(
                    {
                        "url": url,
                        "title": "",
                        "content": "",
                        "error": blocked["message"],
                        "blocked_by_policy": {
                            "host": blocked["host"],
                            "rule": blocked["rule"],
                            "source": blocked["source"],
                        },
                    }
                )
                continue

            try:
                logger.info("Apify extracting: %s", url)
                try:
                    item = await asyncio.wait_for(
                        asyncio.to_thread(
                            _run_website_content_crawler,
                            url,
                            output_formats,
                        ),
                        timeout=60,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Apify WCC timed out for %s", url)
                    results.append(
                        {
                            "url": url,
                            "title": "",
                            "content": "",
                            "error": (
                                "Extract timed out after 60s — page may be too large "
                                "or unresponsive. Try browser_navigate instead."
                            ),
                        }
                    )
                    continue

                if item is None:
                    results.append(
                        {"url": url, "title": "", "content": "", "error": "Actor returned no content"}
                    )
                    continue

                final_url = item.get("url", url)

                final_blocked = check_website_access(final_url)
                if final_blocked:
                    logger.info(
                        "Blocked redirected web_extract for %s by rule %s",
                        final_blocked["host"],
                        final_blocked["rule"],
                    )
                    results.append(
                        {
                            "url": final_url,
                            "title": item.get("title", ""),
                            "content": "",
                            "raw_content": "",
                            "error": final_blocked["message"],
                            "blocked_by_policy": {
                                "host": final_blocked["host"],
                                "rule": final_blocked["rule"],
                                "source": final_blocked["source"],
                            },
                        }
                    )
                    continue

                content_markdown = item.get("markdown")
                content_html = item.get("html")
                title = item.get("title", "")
                metadata = item.get("metadata") or {}

                if fmt == "markdown" or (fmt is None and content_markdown):
                    chosen_content = content_markdown or ""
                else:
                    chosen_content = content_html or content_markdown or ""

                results.append(
                    {
                        "url": final_url,
                        "title": title,
                        "content": chosen_content,
                        "raw_content": chosen_content,
                        "metadata": metadata,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Apify extract failed for %s: %s", url, exc)
                results.append(
                    {
                        "url": url,
                        "title": "",
                        "content": "",
                        "raw_content": "",
                        "error": str(exc),
                    }
                )

        return results

    async def crawl(self, url: str, **kwargs: Any) -> Dict[str, Any]:
        """Crawl a seed URL via Apify Website Content Crawler.

        Multi-page crawl wrapped in asyncio.to_thread with a 300s ceiling.
        Per-page URLs are re-checked against website-access policy. Per-page
        failures are returned as error items, not raised.

        kwargs:
          instructions: str — logged and dropped (WCC has no NL instructions param)
          limit: int — max pages to crawl (default 20)
          depth: "basic" → maxCrawlDepth=2, "advanced" → maxCrawlDepth=5,
                 int → direct, None → 0 (unlimited, capped by limit)
        """
        from tools.interrupt import is_interrupted as _is_interrupted

        if _is_interrupted():
            return {"results": [{"url": url, "title": "", "content": "", "error": "Interrupted"}]}

        instructions = kwargs.get("instructions")
        try:
            limit = int(kwargs.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        depth_raw = kwargs.get("depth")

        if depth_raw == "basic":
            max_depth = 2
        elif depth_raw == "advanced":
            max_depth = 5
        elif isinstance(depth_raw, int):
            max_depth = depth_raw
        else:
            max_depth = 0

        if instructions:
            logger.info("Apify crawl: 'instructions' ignored (not supported by WCC)")

        logger.info("Apify crawl: %s (limit=%d, depth=%s)", url, limit, depth_raw or "unlimited")

        try:
            items = await asyncio.wait_for(
                asyncio.to_thread(_run_wcc_crawl, url, limit, max_depth),
                timeout=300,
            )
        except asyncio.TimeoutError:
            logger.warning("Apify crawl timed out for %s", url)
            return {"results": [{"url": url, "title": "", "content": "",
                                 "error": "Crawl timed out after 300s"}]}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Apify crawl error: %s", exc)
            return {"results": [{"url": url, "title": "", "content": "", "error": str(exc)}]}

        pages: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            page_url = item.get("url", url)
            title = item.get("title", "")

            blocked = check_website_access(page_url)
            if blocked:
                logger.info(
                    "Blocked crawled page %s by rule %s",
                    blocked["host"],
                    blocked["rule"],
                )
                pages.append({
                    "url": page_url,
                    "title": title,
                    "content": "",
                    "raw_content": "",
                    "error": blocked["message"],
                    "blocked_by_policy": {
                        "host": blocked["host"],
                        "rule": blocked["rule"],
                        "source": blocked["source"],
                    },
                })
                continue

            content = item.get("markdown") or item.get("text") or ""
            pages.append({
                "url": page_url,
                "title": title,
                "content": content,
                "raw_content": content,
                "metadata": item.get("metadata") or {},
            })

        logger.info("Apify crawl: %d pages collected", len(pages))
        return {"results": pages}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Apify",
            "badge": "paid",
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
