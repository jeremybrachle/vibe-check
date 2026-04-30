"""arXiv research-feed source via the public Atom API."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.feeds.schemas import FeedItem
from app.services.feeds.sources.base import FeedSource, fingerprint


_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_LINK_RE = re.compile(r"<id>(.*?)</id>", re.DOTALL)
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
_PUBLISHED_RE = re.compile(r"<published>(.*?)</published>", re.DOTALL)


def _parse_atom(xml: str, cat: str) -> list[FeedItem]:
    items: list[FeedItem] = []
    for entry in _ENTRY_RE.findall(xml):
        title_m = _TITLE_RE.search(entry)
        link_m = _LINK_RE.search(entry)
        if not (title_m and link_m):
            continue
        summary_m = _SUMMARY_RE.search(entry)
        published_m = _PUBLISHED_RE.search(entry)
        link = link_m.group(1).strip()
        published: datetime | None = None
        if published_m:
            try:
                published = datetime.fromisoformat(
                    published_m.group(1).strip().replace("Z", "+00:00")
                )
            except ValueError:
                published = None
        items.append(
            FeedItem(
                title=" ".join(title_m.group(1).split()).strip(),
                link=link,
                summary=" ".join((summary_m.group(1) if summary_m else "").split())[:500],
                source=f"arxiv:{cat}",
                published_utc=published,
                fingerprint=fingerprint(link),
            )
        )
    return items


class ArxivFeedSource(FeedSource):
    name = "arxiv"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _get(self, client: httpx.AsyncClient, cat: str) -> str | None:
        url = (
            "http://export.arxiv.org/api/query"
            f"?search_query=cat:{cat}&sortBy=submittedDate&sortOrder=descending&max_results=30"
        )
        try:
            resp = await client.get(url, timeout=settings.feeds_request_timeout_s)
            if resp.status_code == 200:
                return resp.text
        except (httpx.HTTPError, OSError):
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
            for i, cat in enumerate(selectors):
                if i > 0:
                    # arXiv ToU: be polite (>=3s between requests)
                    await asyncio.sleep(settings.feeds_arxiv_min_delay_s)
                xml = await self._get(client, cat)
                if not xml:
                    continue
                out.extend(_parse_atom(xml, cat))
            if since is not None:
                if since.tzinfo is None:
                    since = since.replace(tzinfo=timezone.utc)
                out = [it for it in out if it.published_utc is None or it.published_utc >= since]
            return out
        finally:
            if owned:
                await client.aclose()
