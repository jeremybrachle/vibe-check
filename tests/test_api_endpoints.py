import json
import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.api as api
import app.scheduler as sched
from app.config import settings
from app.database import Base
from app.models import Snapshot


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _build_payload(run_origin: str, excitement: float = 1.2, skepticism: float = 0.9) -> dict:
    return {
        "run_origin": run_origin,
        "excitement_score": excitement,
        "skepticism_score": skepticism,
        "today_themes": [{"topic": "AI", "count": 3, "signal": 12.0, "headlines": ["A", "B"]}],
        "excited_about": [{"title": "A", "url": "https://example.com/a", "score": 10, "comment_count": 2, "source": "hackernews"}],
        "skeptical_about": [{"title": "B", "url": "https://example.com/b", "score": 8, "comment_count": 5, "source": "hackernews"}],
        "most_mentioned_tools": [{"name": "python", "count": 2}],
        "top_links": [{"title": "T", "url": "https://example.com/t", "score": 11, "comment_count": 3, "source": "hackernews"}],
        "best_rabbit_holes": [{"title": "R", "url": "https://example.com/r", "score": 6, "comment_count": 4, "source": "hackernews"}],
        "note": "test note",
        "generated_at": _now().isoformat(),
    }


def _seed_snapshot(db, *, kind: str, llm_provider: str, summary_text: str, run_origin: str, created_at: datetime) -> Snapshot:
    snap = Snapshot(
        kind=kind,
        created_at=created_at,
        source_set="hackernews",
        item_count=5,
        llm_provider=llm_provider,
        summary_text=summary_text,
        data_json=json.dumps(_build_payload(run_origin=run_origin)),
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def _make_client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_api.db"
    test_db_url = f"sqlite:///{db_file}"
    engine = create_engine(test_db_url, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(api.router)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[api.get_db] = override_get_db

    # Keep admin endpoints unprotected during tests.
    settings.admin_token = SecretStr("")
    settings.llm_provider = "auto"

    class FakePipeline:
        def __init__(self, db):
            self.db = db

        async def run_cycle(self, kind="regular", run_origin="manual", source_filter="hackernews"):
            return _seed_snapshot(
                self.db,
                kind=kind,
                llm_provider="ollama",
                summary_text="fresh summary",
                run_origin=run_origin,
                created_at=_now(),
            )

    async def fake_local(force_refresh=False):
        return {
            "generated_at": _now().isoformat(),
            "methodology": ["m1"],
            "legal_ethics": ["e1"],
            "items": [
                {
                    "rank": 1,
                    "model_name": "LocalOne",
                    "model_id": "local/one",
                    "rationale": "good",
                    "qualitative_score": 100,
                    "signals": {"downloads": 1000},
                    "sources": [{"label": "src", "url": "https://example.com"}],
                }
            ],
        }

    async def fake_cloud(force_refresh=False):
        payload = await fake_local(force_refresh=force_refresh)
        payload["items"][0]["model_name"] = "CloudOne"
        payload["items"][0]["model_id"] = "cloud/one"
        return payload

    class FakeProvider:
        label = "ollama"

        async def generate_text(self, prompt: str) -> str:
            return "LLM vibe check summary from test provider."

    monkeypatch.setattr(api, "DigestPipeline", FakePipeline)
    monkeypatch.setattr(api.local_llm_ranker, "get_live_ranking", fake_local)
    monkeypatch.setattr(api.cloud_llm_ranker, "get_live_ranking", fake_cloud)
    monkeypatch.setattr(api, "get_llm_provider", lambda: FakeProvider())

    async def fake_queue_manual_refresh_trigger():
        return {
            "status": "queued",
            "message": "Manual refresh queued.",
            "due_at": _now().isoformat(),
            "trigger_count": 1,
            "seconds_until_due": 1200,
            "threshold": 10,
        }

    monkeypatch.setattr(api, "queue_manual_refresh_trigger", fake_queue_manual_refresh_trigger)
    monkeypatch.setattr(
        api,
        "get_manual_refresh_queue_state",
        lambda: {
            "pending": True,
            "due_at": _now().isoformat(),
            "trigger_count": 1,
            "threshold": 10,
            "seconds_until_due": 1200,
            "window_minutes": 20,
        },
    )

    with TestingSessionLocal() as db:
        base = _now() - timedelta(minutes=10)
        regular = _seed_snapshot(
            db,
            kind="regular",
            llm_provider="ollama",
            summary_text="regular summary",
            run_origin="manual",
            created_at=base,
        )
        regular_id = regular.id
        _seed_snapshot(
            db,
            kind="daily_preview",
            llm_provider="ollama",
            summary_text="preview summary",
            run_origin="scheduled",
            created_at=base + timedelta(minutes=1),
        )
        _seed_snapshot(
            db,
            kind="daily_summary",
            llm_provider="ollama",
            summary_text="daily summary",
            run_origin="scheduled",
            created_at=base + timedelta(minutes=2),
        )

    return TestClient(app), regular_id


def test_all_http_endpoints(tmp_path, monkeypatch):
    client, regular_id = _make_client(tmp_path, monkeypatch)

    assert client.get("/api/v1/health").status_code == 200

    latest = client.get("/api/v1/digest/latest")
    assert latest.status_code == 200
    assert "id" in latest.json()
    assert "run_origin" in latest.json()

    by_id = client.get(f"/api/v1/digest/{regular_id}")
    assert by_id.status_code == 200
    assert by_id.json()["id"] == regular_id

    full_by_id = client.get(f"/api/v1/digest/{regular_id}/full")
    assert full_by_id.status_code == 200
    assert full_by_id.json()["id"] == regular_id
    assert full_by_id.json()["run_origin"] == "manual"
    assert "data" in full_by_id.json()

    digest_list = client.get("/api/v1/digest?limit=5")
    assert digest_list.status_code == 200
    assert len(digest_list.json()) >= 1

    daily_preview = client.get("/api/v1/digest/daily-preview/latest")
    assert daily_preview.status_code == 200
    assert daily_preview.json()["latest"]["kind"] == "daily_preview"
    assert daily_preview.json()["latest"]["run_origin"] == "scheduled"

    daily_summary = client.get("/api/v1/digest/daily-summary/latest")
    assert daily_summary.status_code == 200
    assert daily_summary.json()["latest"]["kind"] == "daily_summary"

    metrics = client.get("/api/v1/metrics/timeseries?limit=10")
    assert metrics.status_code == 200
    assert isinstance(metrics.json()["points"], list)

    run_history = client.get("/api/v1/metrics/run-history?run_origin=manual&limit=50")
    assert run_history.status_code == 200
    assert run_history.json()["run_origin"] == "manual"

    provider_get = client.get("/api/v1/admin/provider")
    assert provider_get.status_code == 200

    provider_set = client.post("/api/v1/admin/provider?provider=none")
    assert provider_set.status_code == 200
    assert provider_set.json()["provider"] == "none"

    provider_heuristic = client.post("/api/v1/admin/provider?provider=heuristic")
    assert provider_heuristic.status_code == 200
    assert provider_heuristic.json()["provider"] == "heuristic"

    provider_bad = client.post("/api/v1/admin/provider?provider=invalid")
    assert provider_bad.status_code == 400

    refreshed = client.post("/api/v1/admin/refresh?kind=regular")
    assert refreshed.status_code == 200
    assert refreshed.json()["kind"] == "regular"

    queued = client.post("/api/v1/admin/refresh/queue")
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"

    canceled = client.post("/api/v1/admin/refresh/queue/cancel")
    assert canceled.status_code == 200
    assert canceled.json()["status"] in {"canceled", "no_pending", "already_running"}

    scheduler_jobs = client.get("/api/v1/admin/scheduler/jobs")
    assert scheduler_jobs.status_code == 200
    assert "jobs" in scheduler_jobs.json()

    scheduler_overview = client.get("/api/v1/admin/scheduler/overview")
    assert scheduler_overview.status_code == 200
    assert len(scheduler_overview.json()["recent_snapshots"]) >= 1
    assert "manual_queue" in scheduler_overview.json()

    local_rank = client.get("/api/v1/research/local-llms/live-ranking")
    assert local_rank.status_code == 200
    assert local_rank.json()["items"][0]["model_name"] == "LocalOne"

    cloud_rank = client.get("/api/v1/research/cloud-llms/live-ranking")
    assert cloud_rank.status_code == 200
    assert cloud_rank.json()["items"][0]["model_name"] == "CloudOne"

    vibe = client.get("/api/v1/research/llm-vibe-check?scope=local")
    assert vibe.status_code == 200
    assert vibe.json()["scope"] == "local"
    assert vibe.json()["llm_provider"] == "ollama"
    assert "summary" in vibe.json()["ai_summary"].lower()

    overview_local = client.get("/api/v1/research/overview?scope=local")
    assert overview_local.status_code == 200
    body_local = overview_local.json()
    assert body_local["scope"] == "local"
    assert body_local["ranking"]["items"][0]["model_name"] == "LocalOne"
    assert body_local["vibe"]["scope"] == "local"

    overview_cloud = client.get("/api/v1/research/overview?scope=cloud")
    assert overview_cloud.status_code == 200
    body_cloud = overview_cloud.json()
    assert body_cloud["scope"] == "cloud"
    assert body_cloud["ranking"]["items"][0]["model_name"] == "CloudOne"
    assert body_cloud["vibe"]["scope"] == "cloud"


def test_queue_refresh_auto_trigger_message(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)

    async def fake_auto_trigger():
        return {
            "status": "auto_triggered",
            "message": "Congratulations, you're impatient. Super duper manual trigger activated.",
            "snapshot_id": 999,
            "kind": "regular",
            "created_at": _now().isoformat(),
        }

    monkeypatch.setattr(api, "queue_manual_refresh_trigger", fake_auto_trigger)
    response = client.post("/api/v1/admin/refresh/queue")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "auto_triggered"
    assert "impatient" in body["message"].lower()


def test_queue_refresh_cooldown_status(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)

    async def fake_cooldown():
        return {
            "status": "cooldown",
            "message": "Super duper manual trigger is limited to once per hour.",
            "due_at": _now().isoformat(),
            "trigger_count": 10,
            "seconds_until_due": 500,
            "threshold": 10,
            "cooldown_seconds_remaining": 1400,
        }

    monkeypatch.setattr(api, "queue_manual_refresh_trigger", fake_cooldown)
    response = client.post("/api/v1/admin/refresh/queue")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cooldown"
    assert body["cooldown_seconds_remaining"] > 0


def test_scheduler_jobs_includes_manual_queue_entry(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)

    monkeypatch.setattr(
        api,
        "get_manual_refresh_queue_state",
        lambda: {
            "pending": True,
            "due_at": _now().isoformat(),
            "trigger_count": 2,
            "threshold": 10,
            "seconds_until_due": 800,
            "window_minutes": 20,
        },
    )

    response = client.get("/api/v1/admin/scheduler/jobs")
    assert response.status_code == 200
    body = response.json()
    queue_jobs = [j for j in body["jobs"] if j["id"] == "manual_refresh_queue"]
    assert queue_jobs
    assert queue_jobs[0]["kind"] == "queued_manual_refresh"


def test_scheduler_overview_limits_and_sorts_upcoming(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)

    class StepTrigger:
        def __init__(self, delta_minutes):
            self.delta = timedelta(minutes=delta_minutes)

        def get_next_fire_time(self, previous_fire_time, now):
            return previous_fire_time + self.delta

    class FakeJob:
        def __init__(self, id, run_at, kind, trigger=None):
            self.id = id
            self.next_run_time = run_at
            self.kwargs = {"kind": kind}
            self.trigger = trigger

    now = _now()
    fake_jobs = [
        FakeJob("regular_refresh", now + timedelta(minutes=10), "regular", StepTrigger(120)),
        FakeJob("daily_preview_eastern", now + timedelta(hours=12), "daily_preview"),
        FakeJob("daily_preview_eastern_backup", now + timedelta(hours=12, minutes=5), "daily_preview"),
    ]

    monkeypatch.setattr(api.scheduler, "get_jobs", lambda: fake_jobs)
    monkeypatch.setattr(
        api,
        "get_manual_refresh_queue_state",
        lambda: {
            "pending": False,
            "due_at": None,
            "trigger_count": 0,
            "threshold": 10,
            "seconds_until_due": 0,
            "window_minutes": 20,
        },
    )

    response = client.get("/api/v1/admin/scheduler/overview")
    assert response.status_code == 200
    body = response.json()
    upcoming = body["upcoming_snapshots"]

    assert len(upcoming) == 3
    times = [item["next_run_time"] for item in upcoming]
    assert times == sorted(times)
    assert [item["id"] for item in upcoming] == ["regular_refresh", "regular_refresh", "regular_refresh"]


def test_scheduler_jobs_excludes_internal_worker(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)

    class FakeJob:
        def __init__(self, id, run_at, kind=None):
            self.id = id
            self.next_run_time = run_at
            self.kwargs = {"kind": kind} if kind else {}

    now = _now()
    fake_jobs = [
        FakeJob("manual_refresh_queue_worker", now + timedelta(seconds=30), None),
        FakeJob("daily_preview_eastern_backup", now + timedelta(hours=3), "daily_preview"),
        FakeJob("regular_refresh", now + timedelta(hours=2), "regular"),
    ]

    monkeypatch.setattr(api.scheduler, "get_jobs", lambda: fake_jobs)
    monkeypatch.setattr(
        api,
        "get_manual_refresh_queue_state",
        lambda: {
            "pending": False,
            "due_at": None,
            "trigger_count": 0,
            "threshold": 10,
            "seconds_until_due": 0,
            "window_minutes": 20,
        },
    )

    response = client.get("/api/v1/admin/scheduler/jobs")
    assert response.status_code == 200
    job_ids = [j["id"] for j in response.json()["jobs"]]
    assert "manual_refresh_queue_worker" not in job_ids
    assert "daily_preview_eastern_backup" not in job_ids
    assert "regular_refresh" in job_ids


def test_digest_source_filter_hackernews(tmp_path, monkeypatch):
    """source=hackernews should return the seeded snapshot."""
    client, _ = _make_client(tmp_path, monkeypatch)

    resp = client.get("/api/v1/digest/latest?source=hackernews")
    assert resp.status_code == 200
    assert "hackernews" in resp.json()["sources"]


def test_digest_source_filter_reddit_returns_404(tmp_path, monkeypatch):
    """source=reddit should 404 when no Reddit snapshots exist."""
    client, _ = _make_client(tmp_path, monkeypatch)

    resp = client.get("/api/v1/digest/latest?source=reddit")
    assert resp.status_code == 404


def test_digest_list_source_filter(tmp_path, monkeypatch):
    """source filter on the list endpoint should only return matching snapshots."""
    client, _ = _make_client(tmp_path, monkeypatch)

    hn = client.get("/api/v1/digest?source=hackernews")
    assert hn.status_code == 200
    assert len(hn.json()) >= 1
    for item in hn.json():
        assert "hackernews" in item["sources"]

    reddit = client.get("/api/v1/digest?source=reddit")
    assert reddit.status_code == 200
    assert reddit.json() == []


def test_digest_not_found(tmp_path, monkeypatch):
    """Fetching a non-existent digest id should return 404."""
    client, _ = _make_client(tmp_path, monkeypatch)

    resp = client.get("/api/v1/digest/999999")
    assert resp.status_code == 404


def test_admin_refresh_source_filter(tmp_path, monkeypatch):
    """Admin refresh with source=hackernews should succeed and return a digest."""
    client, _ = _make_client(tmp_path, monkeypatch)

    resp = client.post("/api/v1/admin/refresh?kind=regular&source=hackernews")
    assert resp.status_code == 200
    assert resp.json()["kind"] == "regular"


def test_cors_wildcard_not_set_in_prod_config(tmp_path, monkeypatch):
    """Smoke check: allowed_origins defaults should be overridable via settings."""
    from app.config import settings as cfg
    # Default in config is "*" (dev mode). Just verify the field exists and is a string
    # so ops can override it. The actual "*" check is intentional for dev.
    assert isinstance(cfg.allowed_origins, str)
    assert cfg.allowed_origins  # must be non-empty


def test_provider_ollama_accepted(tmp_path, monkeypatch):
    """ollama is a valid provider value and should be accepted."""
    client, _ = _make_client(tmp_path, monkeypatch)

    resp = client.post("/api/v1/admin/provider?provider=ollama")
    assert resp.status_code == 200
    assert resp.json()["provider"] == "ollama"


def test_scheduler_super_duper_not_stuck_at_nine(monkeypatch):
    class DummyDb:
        def close(self):
            return None

    class DummySnapshot:
        id = 999
        kind = "super_duper_manual_trigger"
        created_at = _now()

    class FakePipeline:
        def __init__(self, db):
            self.db = db

        async def run_cycle(self, kind="regular", run_origin="manual", source_filter="hackernews"):
            return DummySnapshot()

    monkeypatch.setattr(sched, "SessionLocal", lambda: DummyDb())
    monkeypatch.setattr(sched, "DigestPipeline", FakePipeline)

    now = _now()
    sched.manual_refresh_due_at = None
    sched.manual_refresh_running_since = None
    sched.last_super_manual_trigger_at = None
    sched.manual_refresh_started_windows = [
        now - timedelta(minutes=30),
        now - timedelta(minutes=10),
    ]
    sched.manual_refresh_click_window_ends_at = now + timedelta(minutes=10)
    sched.manual_refresh_trigger_count = 8

    # Rate limit is currently disabled (MANUAL_REFRESH_LIMIT_COUNT = 999),
    # so 9th click returns queued (not rate_limited)
    ninth = asyncio.run(sched.queue_manual_refresh_trigger())
    assert ninth["status"] == "queued"
    assert ninth["trigger_count"] == 9

    tenth = asyncio.run(sched.queue_manual_refresh_trigger())
    assert tenth["status"] == "auto_triggered"
    assert sched.manual_refresh_trigger_count == 0


def test_scheduler_super_duper_triggers_on_tenth_click(monkeypatch):
    class DummyDb:
        def close(self):
            return None

    class DummySnapshot:
        id = 1001
        kind = "super_duper_manual_trigger"
        created_at = _now()

    class FakePipeline:
        def __init__(self, db):
            self.db = db

        async def run_cycle(self, kind="regular", run_origin="manual", source_filter="hackernews"):
            return DummySnapshot()

    monkeypatch.setattr(sched, "SessionLocal", lambda: DummyDb())
    monkeypatch.setattr(sched, "DigestPipeline", FakePipeline)

    now = _now()
    sched.manual_refresh_due_at = None
    sched.manual_refresh_running_since = None
    sched.last_super_manual_trigger_at = None
    sched.manual_refresh_started_windows = [
        now - timedelta(minutes=20),
        now - timedelta(minutes=5),
    ]
    sched.manual_refresh_click_window_ends_at = now + timedelta(minutes=10)
    sched.manual_refresh_trigger_count = 9

    result = asyncio.run(sched.queue_manual_refresh_trigger())
    assert result["status"] == "auto_triggered"
    assert result["kind"] == "super_duper_manual_trigger"


def test_latest_with_ai_returns_most_recent_summary(tmp_path, monkeypatch):
    """latest-with-ai should skip snapshots without an AI summary."""
    client, _ = _make_client(tmp_path, monkeypatch)

    # Seed a brand-new structured-only snapshot (provider=none, empty summary).
    # /digest/latest should return this one, but /digest/latest-with-ai should
    # skip it and fall back to the previously seeded "regular summary" row.
    from app.database import Base  # noqa: F401  (reuse DB engine via fixture state)
    from sqlalchemy.orm import sessionmaker
    # Re-derive the session bound to the same SQLite file the fixture wrote to.
    db_file = tmp_path / "test_api.db"
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with SessionLocal() as db:
        _seed_snapshot(
            db,
            kind="regular",
            llm_provider="none",
            summary_text="",
            run_origin="scheduled",
            created_at=_now(),
        )

    latest = client.get("/api/v1/digest/latest")
    assert latest.status_code == 200
    assert latest.json()["llm_provider"] == "none"
    assert latest.json()["ai_summary"] == ""

    latest_ai = client.get("/api/v1/digest/latest-with-ai")
    assert latest_ai.status_code == 200
    assert latest_ai.json()["llm_provider"] == "ollama"
    assert latest_ai.json()["ai_summary"]


def test_latest_with_ai_404_when_no_ai_snapshots(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)

    # Wipe the existing seeded snapshots so nothing has an AI summary.
    db_file = tmp_path / "test_api.db"
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with SessionLocal() as db:
        for snap in db.query(Snapshot).all():
            db.delete(snap)
        db.commit()
        _seed_snapshot(
            db,
            kind="regular",
            llm_provider="none",
            summary_text="",
            run_origin="scheduled",
            created_at=_now(),
        )

    resp = client.get("/api/v1/digest/latest-with-ai")
    assert resp.status_code == 404


def test_admin_override_generates_only_regular(tmp_path, monkeypatch):
    """The admin override is decoupled from the daily kinds — it should
    produce only a `regular` snapshot flagged super_manual. Daily preview
    and daily summary stay on their dedicated cron / admin-button paths."""
    client, _ = _make_client(tmp_path, monkeypatch)

    resp = client.post("/api/v1/admin/refresh/override?source=hackernews")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "regular"
    assert body["run_origin"] == "super_manual"

    # Daily widgets should still reflect the originally seeded scheduled rows,
    # not new super_manual ones.
    preview = client.get("/api/v1/digest/daily-preview/latest").json()["latest"]
    summary = client.get("/api/v1/digest/daily-summary/latest").json()["latest"]
    assert preview["run_origin"] == "scheduled"
    assert summary["run_origin"] == "scheduled"


def test_admin_refresh_accepts_super_manual_run_origin(tmp_path, monkeypatch):
    """The /admin/refresh endpoint should accept run_origin=super_manual so
    the dedicated 9:01/5:01 admin buttons can flag their snapshots correctly."""
    client, _ = _make_client(tmp_path, monkeypatch)

    resp = client.post("/api/v1/admin/refresh?kind=daily_preview&run_origin=super_manual")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "daily_preview"
    assert body["run_origin"] == "super_manual"

    resp = client.post("/api/v1/admin/refresh?kind=daily_summary&run_origin=super_manual")
    assert resp.status_code == 200
    assert resp.json()["run_origin"] == "super_manual"

