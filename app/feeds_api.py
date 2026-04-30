"""Research-feed API endpoints, mounted under /api/v1/feeds/.

Sits alongside the existing /api/v1/digest/* surface. Public (no auth) —
same posture as /digest. Frontends can hit this directly.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.services.feeds import cache
from app.services.feeds.schemas import FeedItem, FeedResponse, FeedsHealthOut
from app.services.feeds.sources import (
    ArxivFeedSource,
    HackerNewsFeedSource,
    RedditFeedSource,
)
from app.services.feeds.topics import registry


router = APIRouter(prefix="/api/v1/feeds", tags=["research-feeds"])


def _enabled_sources() -> list[str]:
    out: list[str] = []
    if settings.feeds_enable_arxiv:
        out.append("arxiv")
    if settings.feeds_enable_reddit:
        out.append("reddit")
    if settings.feeds_enable_hn:
        out.append("hn")
    return out


@router.get("/health", response_model=FeedsHealthOut)
def feeds_health() -> FeedsHealthOut:
    return FeedsHealthOut(
        sources_enabled=_enabled_sources(),
        topics=registry().names,
    )


@router.get("/topics")
def feeds_topics() -> dict:
    return {"topics": registry().names}


def _parse_since(since: str | None) -> datetime:
    if since:
        try:
            dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid 'since' value: {exc}")
    return datetime.now(tz=timezone.utc) - timedelta(hours=settings.feeds_default_lookback_hours)


async def _gather(
    selectors_by_source: dict[str, list[str]],
    since: datetime,
) -> list[FeedItem]:
    tasks = []
    if settings.feeds_enable_arxiv and selectors_by_source.get("arxiv"):
        tasks.append(ArxivFeedSource().fetch(selectors_by_source["arxiv"], since))
    if settings.feeds_enable_reddit and selectors_by_source.get("reddit"):
        tasks.append(RedditFeedSource().fetch(selectors_by_source["reddit"], since))
    if settings.feeds_enable_hn and selectors_by_source.get("hn"):
        tasks.append(HackerNewsFeedSource().fetch(selectors_by_source["hn"], since))
    if not tasks:
        return []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    items: list[FeedItem] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        items.extend(r)
    return items


def _dedupe(items: list[FeedItem]) -> list[FeedItem]:
    seen: set[str] = set()
    out: list[FeedItem] = []
    for it in items:
        if it.fingerprint in seen:
            continue
        seen.add(it.fingerprint)
        out.append(it)
    return out


@router.get("/feed", response_model=FeedResponse)
async def feed(
    topic: str = Query(..., min_length=1, max_length=64),
    since: str | None = Query(None, description="ISO-8601; default = last 24h"),
) -> FeedResponse:
    if topic not in registry():
        raise HTTPException(
            status_code=404,
            detail=f"unknown topic '{topic}'. Known: {registry().names}",
        )
    selectors = registry().get(topic) or {}
    since_dt = _parse_since(since)

    try:
        items = await _gather(selectors, since_dt)
    except Exception as exc:
        cached = cache.read(topic, reason=f"upstream error: {exc}")
        if cached:
            return cached
        raise HTTPException(status_code=503, detail=f"upstream error: {exc}")

    items = _dedupe(items)[: settings.feeds_max_items_per_response]

    if not items:
        cached = cache.read(topic, reason="no fresh items returned")
        if cached:
            return cached

    response = FeedResponse(
        topic=topic,
        generated_utc=datetime.now(tz=timezone.utc),
        items=items,
    )
    if items:
        try:
            cache.write(topic, response)
        except OSError:
            pass  # cache is best-effort
    return response
