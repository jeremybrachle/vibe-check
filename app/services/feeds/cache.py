"""File cache for feed responses. Last successful payload per topic is
written to settings.feeds_cache_dir/<topic>.json so that consumers always
get *something* when an upstream is down.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.services.feeds.schemas import FeedItem, FeedResponse


def _path_for(topic: str) -> Path:
    return Path(settings.feeds_cache_dir) / f"{topic}.json"


def write(topic: str, response: FeedResponse) -> None:
    Path(settings.feeds_cache_dir).mkdir(parents=True, exist_ok=True)
    payload = response.model_dump(mode="json")
    payload["cached"] = False
    payload["cache_reason"] = None
    _path_for(topic).write_text(json.dumps(payload), encoding="utf-8")


def read(topic: str, reason: str) -> FeedResponse | None:
    path = _path_for(topic)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    items = [FeedItem.model_validate(it) for it in raw.get("items", [])]
    generated = raw.get("generated_utc")
    try:
        gen_dt = (
            datetime.fromisoformat(generated.replace("Z", "+00:00"))
            if isinstance(generated, str)
            else datetime.now(tz=timezone.utc)
        )
    except ValueError:
        gen_dt = datetime.now(tz=timezone.utc)
    return FeedResponse(
        topic=topic,
        generated_utc=gen_dt,
        items=items,
        cached=True,
        cache_reason=reason,
    )
