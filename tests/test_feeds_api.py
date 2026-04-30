from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import feeds_api
from app.config import settings
from app.main import app
from app.services.feeds import cache
from app.services.feeds.schemas import FeedItem, FeedResponse


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "feeds_cache_dir", tmp_path / "feeds_cache")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def stub_gather(monkeypatch):
    """Replace the network-using `_gather` with a controllable stub."""

    async def _fake(_selectors, _since):
        return _fake.return_value

    _fake.return_value = []
    monkeypatch.setattr(feeds_api, "_gather", _fake)
    return _fake


def test_feeds_health(client):
    r = client.get("/api/v1/feeds/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "navier_stokes" in body["topics"]
    assert set(body["sources_enabled"]).issubset({"arxiv", "reddit", "hn"})


def test_feeds_topics(client):
    r = client.get("/api/v1/feeds/topics")
    assert r.status_code == 200
    assert "navier_stokes" in r.json()["topics"]


def test_unknown_topic_returns_404(client, stub_gather):
    r = client.get("/api/v1/feeds/feed", params={"topic": "not_real"})
    assert r.status_code == 404


def test_feed_returns_items(client, stub_gather):
    stub_gather.return_value = [
        FeedItem(
            title="Paper A",
            link="https://arxiv.org/abs/1",
            source="arxiv:math.AP",
            published_utc=datetime(2026, 4, 30, tzinfo=timezone.utc),
            fingerprint="aaaaaa111111",
        ),
        FeedItem(
            title="Paper B",
            link="https://arxiv.org/abs/2",
            source="arxiv:math.AP",
            published_utc=datetime(2026, 4, 30, tzinfo=timezone.utc),
            fingerprint="bbbbbb222222",
        ),
    ]
    r = client.get("/api/v1/feeds/feed", params={"topic": "navier_stokes"})
    assert r.status_code == 200
    body = r.json()
    assert body["topic"] == "navier_stokes"
    assert body["cached"] is False
    assert len(body["items"]) == 2


def test_feed_dedupes_by_fingerprint(client, stub_gather):
    stub_gather.return_value = [
        FeedItem(title="A", link="https://x/1", source="arxiv:cs.CC", fingerprint="dup0000aaaa"),
        FeedItem(title="A dup", link="https://x/1", source="reddit:compsci", fingerprint="dup0000aaaa"),
        FeedItem(title="B", link="https://x/2", source="hn:foo", fingerprint="uniq0000bbbb"),
    ]
    r = client.get("/api/v1/feeds/feed", params={"topic": "p_vs_np"})
    assert r.status_code == 200
    fps = [it["fingerprint"] for it in r.json()["items"]]
    assert fps == ["dup0000aaaa", "uniq0000bbbb"]


def test_feed_falls_back_to_cache_on_empty_result(client, stub_gather):
    cache.write(
        "riemann",
        FeedResponse(
            topic="riemann",
            generated_utc=datetime(2026, 4, 29, tzinfo=timezone.utc),
            items=[
                FeedItem(
                    title="cached",
                    link="https://example/c",
                    source="arxiv:math.NT",
                    fingerprint="cache000000c",
                )
            ],
        ),
    )
    stub_gather.return_value = []
    r = client.get("/api/v1/feeds/feed", params={"topic": "riemann"})
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is True
    assert body["cache_reason"] == "no fresh items returned"
    assert body["items"][0]["title"] == "cached"


def test_feed_invalid_since_returns_400(client, stub_gather):
    r = client.get(
        "/api/v1/feeds/feed", params={"topic": "navier_stokes", "since": "not-a-date"}
    )
    assert r.status_code == 400
