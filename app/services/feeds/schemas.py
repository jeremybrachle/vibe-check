"""Schemas for research-feed endpoints. Kept separate from app/schemas.py
so the digest surface and the feeds surface don't accidentally couple.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


SourceTag = str  # "arxiv:math.AP", "reddit:math", "hn:<query>"


class FeedItem(BaseModel):
    title: str
    link: str
    summary: str = ""
    source: SourceTag
    published_utc: datetime | None = None
    fingerprint: str


class FeedResponse(BaseModel):
    topic: str
    generated_utc: datetime
    items: list[FeedItem]
    cached: bool = False
    cache_reason: str | None = None


class FeedsHealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    sources_enabled: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
