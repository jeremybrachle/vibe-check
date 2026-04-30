"""Source ABC for research feeds.

Each source MUST:
  - Be safe to call without network (return [] on failure, never raise).
  - Cap its own output (don't return 10k items).
  - Tag each item's `source` field with a stable identifier of the form
    "<name>:<selector>".
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from hashlib import sha1

from app.services.feeds.schemas import FeedItem


def fingerprint(link: str) -> str:
    return sha1(link.encode("utf-8")).hexdigest()[:12]


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class FeedSource(ABC):
    name: str

    @abstractmethod
    async def fetch(
        self,
        selectors: list[str],
        since: datetime | None,
    ) -> list[FeedItem]:
        ...
