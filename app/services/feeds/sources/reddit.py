"""Reddit research-feed source via anonymous /new.json endpoints.

Note: this is intentionally separate from app/services/sources/reddit.py
which serves the digest pipeline (different shape, different cadence).
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.feeds.schemas import FeedItem
from app.services.feeds.sources.base import FeedSource, fingerprint


class RedditFeedSource(FeedSource):
    name = "reddit"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _get_subreddit(
        self, client: httpx.AsyncClient, sub: str
    ) -> list[dict] | None:
        url = f"https://www.reddit.com/r/{sub}/new.json?limit=25"
        try:
            resp = await client.get(url, timeout=settings.feeds_request_timeout_s)
            if resp.status_code == 200:
                payload = resp.json()
                return [c["data"] for c in payload.get("data", {}).get("children", [])]
        except (httpx.HTTPError, ValueError, KeyError, OSError):
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
            for sub in selectors:
                rows = await self._get_subreddit(client, sub)
                if not rows:
                    continue
                for row in rows:
                    permalink = row.get("permalink") or ""
                    link = (
                        f"https://www.reddit.com{permalink}"
                        if permalink.startswith("/")
                        else (row.get("url") or "")
                    )
                    if not link:
                        continue
                    created = row.get("created_utc")
                    published = (
                        datetime.fromtimestamp(created, tz=timezone.utc)
                        if isinstance(created, (int, float))
                        else None
                    )
                    out.append(
                        FeedItem(
                            title=(row.get("title") or "").strip(),
                            link=link,
                            summary=(row.get("selftext") or "")[:500],
                            source=f"reddit:{sub}",
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
