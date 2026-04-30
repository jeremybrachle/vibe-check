from datetime import datetime, timezone

import pytest

from app.config import settings
from app.services.feeds import cache
from app.services.feeds.schemas import FeedItem, FeedResponse


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "feeds_cache_dir", tmp_path / "feeds_cache")


def _sample_response() -> FeedResponse:
    return FeedResponse(
        topic="demo",
        generated_utc=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
        items=[
            FeedItem(
                title="Hello",
                link="https://example.com/1",
                summary="hi",
                source="arxiv:cs.LG",
                published_utc=datetime(2026, 4, 30, 11, 0, tzinfo=timezone.utc),
                fingerprint="abc123abc123",
            )
        ],
    )


def test_cache_roundtrip():
    cache.write("demo", _sample_response())
    got = cache.read("demo", reason="test")
    assert got is not None
    assert got.cached is True
    assert got.cache_reason == "test"
    assert got.topic == "demo"
    assert len(got.items) == 1
    assert got.items[0].link == "https://example.com/1"


def test_cache_miss_returns_none():
    assert cache.read("never-written", reason="nope") is None
