"""Hacker News research-feed source via Algolia HN search."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.config import settings
from app.services.feeds.schemas import FeedItem
from app.services.feeds.sources.base import FeedSource, fingerprint


class HackerNewsFeedSource(FeedSource):
    name = "hn"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _search(
        self, client: httpx.AsyncClient, query: str
    ) -> list[dict] | None:
        url = (
            "https://hn.algolia.com/api/v1/search_by_date"
            f"?query={quote(query)}&tags=story&hitsPerPage=20"
        )
        try:
            resp = await client.get(url, timeout=settings.feeds_request_timeout_s)
            if resp.status_code == 200:
                return resp.json().get("hits", [])
        except (httpx.HTTPError, ValueError, OSError):
            pass
        return None

    async def fetch(
        self,
        selectors: list[str],
        since: datetime | None,
    ) -> list[FeedItem]:
        if not selectors:
            return []

        owned = self._client is None
        client = self._client or httpx.AsyncClient(
            headers={"User-Agent": settings.feeds_user_agent}
        )
        try:
            out: list[FeedItem] = []
            for query in selectors:
                hits = await self._search(client, query)
                if not hits:
                    continue
                for hit in hits:
                    obj_id = hit.get("objectID")
                    link = hit.get("url") or (
                        f"https://news.ycombinator.com/item?id={obj_id}" if obj_id else ""
                    )
                    if not link:
                        continue
                    created_at = hit.get("created_at")
                    published: datetime | None = None
                    if isinstance(created_at, str):
                        try:
                            published = datetime.fromisoformat(
                                created_at.replace("Z", "+00:00")
                            )
                        except ValueError:
                            published = None
                    out.append(
                        FeedItem(
                            title=(hit.get("title") or hit.get("story_title") or "").strip(),
                            link=link,
                            summary="",
                            source=f"hn:{query}",
                            published_utc=published,
                            fingerprint=fingerprint(link),
                        )
                    )
            if since is not None:
                if since.tzinfo is None:
                    since = since.replace(tzinfo=timezone.utc)
                out = [it for it in out if it.published_utc is None or it.published_utc >= since]
            return out
        finally:
            if owned:
                await client.aclose()
