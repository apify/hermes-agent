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
        except Exception as exc:  # noqa: BLE001
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


def _run_website_content_crawler(url: str, output_formats: List[str]) -> Optional[Dict[str, Any]]:
    """Blocking call to apify/website-content-crawler for a single URL.

    Returns the first dataset item (single-page crawl), or None if no items.
    Intended to be called via asyncio.to_thread from extract().
    """
    client = _get_apify_client()
    run = client.actor("apify/website-content-crawler").start(
        run_input={
            "startUrls": [{"url": url}],
            "maxCrawlPages": 1,
            "outputFormats": output_formats,
        }
    )
    logger.info("Apify website-content-crawler started — https://console.apify.com/actors/runs/%s", run.id)
    run = client.run(run.id).wait_for_finish()
    if run is None:
        return None
    logger.info("Apify website-content-crawler: %s", run.status)
    dataset_id = run.default_dataset_id
    if not dataset_id:
        return None
    items = client.dataset(dataset_id).list_items().items
    if not items:
        return None
    item = items[0]
    return item if isinstance(item, dict) else None


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
            run = client.actor("apify/rag-web-browser").start(
                run_input={
                    "query": query,
                    "maxResults": limit,
                    "requestTimeoutSecs": 60,
                }
            )
            logger.info("Apify rag-web-browser started — https://console.apify.com/actors/runs/%s", run.id)
            run = client.run(run.id).wait_for_finish()
            if run is None:
                return {"success": False, "error": "Apify actor run returned no result"}
            logger.info("Apify rag-web-browser: %s", run.status)

            dataset_id = run.default_dataset_id
            if not dataset_id:
                return {"success": False, "error": "Apify run missing defaultDatasetId"}

            items = client.dataset(dataset_id).list_items().items
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
