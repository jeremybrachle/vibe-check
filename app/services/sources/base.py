from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class Story:
    source: str
    feed: str
    external_id: str
    title: str
    url: str
    score: int
    comment_count: int
    published_at: datetime | None
    author: str | None = None
    text: str | None = None
    top_comments: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(Protocol):
    name: str

    async def fetch_stories(self) -> list[Story]:
        ...
