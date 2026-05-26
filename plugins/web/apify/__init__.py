"""Apify web search + extract + crawl plugin — bundled, auto-loaded.

Uses apify/rag-web-browser for search and apify/website-content-crawler
for both single-page extract and multi-page crawl. Requires APIFY_API_TOKEN.
"""

from __future__ import annotations

from plugins.web.apify.provider import ApifyWebSearchProvider


def register(ctx) -> None:
    """Register the Apify provider with the plugin context."""
    ctx.register_web_search_provider(ApifyWebSearchProvider())