"""Topic registry loader for research feeds.

Topics live in topics.yaml so adding/removing one is a config edit, not a
code change. Loaded once on startup; tests can call load_registry(path)
directly with a custom path.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.config import settings


class TopicRegistry:
    def __init__(self, data: dict):
        self._topics: dict[str, dict[str, list[str]]] = data.get("topics", {}) or {}

    @property
    def names(self) -> list[str]:
        return sorted(self._topics.keys())

    def get(self, topic: str) -> dict[str, list[str]] | None:
        return self._topics.get(topic)

    def __contains__(self, topic: str) -> bool:
        return topic in self._topics


def load_registry(path: Path | None = None) -> TopicRegistry:
    target = Path(path) if path else settings.feeds_topics_file
    with open(target, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return TopicRegistry(data)


@lru_cache(maxsize=1)
def registry() -> TopicRegistry:
    """Cached default registry. Tests should call load_registry(path) directly."""
    return load_registry()


def reset_registry_cache() -> None:
    """Used by tests / hot config swaps."""
    registry.cache_clear()
