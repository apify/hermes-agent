from __future__ import annotations

from plugins.web.apify.provider import ApifyWebSearchProvider

def register(ctx) -> None:
    """Register the Apify provider with the plugin context."""
    ctx.register_web_search_provider(ApifyWebSearchProvider())