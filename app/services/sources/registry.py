from app.config import settings
from app.services.sources.base import SourceAdapter
from app.services.sources.hackernews import HackerNewsAdapter
from app.services.sources.reddit import RedditAdapter


def get_enabled_sources(source_filter: str | None = None) -> list[SourceAdapter]:
    # Keep source registration centralized for easy expansion.
    adapters: list[SourceAdapter] = [HackerNewsAdapter()]
    if settings.reddit_enabled:
        adapters.append(RedditAdapter())

    if not source_filter:
        return adapters

    needle = source_filter.strip().lower()
    return [adapter for adapter in adapters if adapter.name.lower() == needle]
