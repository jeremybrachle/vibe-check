import json
from datetime import datetime
from enum import Enum

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Snapshot
from app.schemas import (
    DigestListItem,
    DigestSectionOut,
    DigestOut,
    HealthOut,
    LlmVibeCheckOut,
    LocalModelRankingOut,
    MetricPointOut,
    MetricsTimeseriesOut,
    RunHistoryOut,
    RunHistoryPointOut,
    SnapshotDetailOut,
    ResearchOverviewOut,
    StoryLinkOut,
    ToolMentionOut,
    TopicOut,
)
from app.services.llm.factory import get_llm_provider
from app.services.pipeline import DigestPipeline
from app.services.research.cloud_llm_ranker import CloudLLMRanker
from app.services.research.local_llm_ranker import LocalLLMRanker
from app.scheduler import cancel_manual_refresh_trigger, get_manual_refresh_queue_state, queue_manual_refresh_trigger, scheduler


router = APIRouter(prefix="/api/v1", tags=["vibe-check"])
local_llm_ranker = LocalLLMRanker()
cloud_llm_ranker = CloudLLMRanker()
admin_override_started_at: datetime | None = None


class DigestKind(str, Enum):
    regular = "regular"
    daily_summary = "daily_summary"
    daily_preview = "daily_preview"


class RunOrigin(str, Enum):
    manual = "manual"
    scheduled = "scheduled"


class ResearchScope(str, Enum):
    local = "local"
    cloud = "cloud"


class SourceScope(str, Enum):
    hackernews = "hackernews"
    reddit = "reddit"


def _require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """If ADMIN_TOKEN is configured, verify the request includes a matching header."""
    expected = settings.admin_token.get_secret_value()
    if not expected:
        return  # token check disabled
    if x_admin_token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")


def _require_local_admin_runtime() -> None:
    """Admin refresh actions are local-only; cloud deploys stay on strict cadence."""
    env = (settings.app_env or "").strip().lower()
    if env in {"production", "prod", "aws"}:
        raise HTTPException(status_code=403, detail="Admin refresh is disabled in deployed environments.")


def _scheduler_job_kind(job) -> str:
    return (job.kwargs or {}).get("kind", "regular")


def _is_user_visible_scheduler_job(job) -> bool:
    if job.id == "manual_refresh_queue_worker":
        return False
    if job.id.endswith("_backup"):
        return False
    if "_test_" in job.id:
        return False
    return True


def _project_job_times(job, count: int) -> list[datetime]:
    if not job.next_run_time:
        return []

    results: list[datetime] = []
    next_time = job.next_run_time
    previous_time = None
    trigger = getattr(job, "trigger", None)

    for _ in range(max(1, count)):
        if not next_time:
            break
        results.append(next_time)

        if not trigger or not hasattr(trigger, "get_next_fire_time"):
            break
        previous_time = next_time
        next_time = trigger.get_next_fire_time(previous_time, previous_time)

    return results


def _snapshot_to_digest(snapshot: Snapshot) -> DigestOut:
    """Convert a persisted Snapshot row to the clean external API shape."""
    data = json.loads(snapshot.data_json)

    today_themes = [
        TopicOut(
            topic=t["topic"],
            count=t["count"],
            signal=t["signal"],
            headlines=t.get("headlines", []),
        )
        for t in (data.get("today_themes") or [])
    ]

    most_mentioned_tools = [
        ToolMentionOut(name=t["name"], count=t["count"])
        for t in (data.get("most_mentioned_tools") or [])
    ]

    def _to_story_link(item: dict) -> StoryLinkOut:
        return StoryLinkOut(
            title=item.get("title", ""),
            url=item.get("url", ""),
            score=int(item.get("score", 0)),
            comments=int(item.get("comment_count", item.get("comments", 0))),
            source=item.get("source", snapshot.source_set.split(",")[0]),
            reason=item.get("reason", ""),
            article_summary=item.get("article_summary", ""),
            article_summary_ai=item.get("article_summary_ai", ""),
            comments_summary=item.get("comments_summary", ""),
        )

    def _to_story_link_list(value: list) -> list[StoryLinkOut]:
        links: list[StoryLinkOut] = []
        for item in value or []:
            if isinstance(item, dict):
                links.append(_to_story_link(item))
            elif isinstance(item, str):
                # Backward compatibility for older snapshots that stored title strings only.
                links.append(
                    StoryLinkOut(
                        title=item,
                        url="",
                        score=0,
                        comments=0,
                        source=snapshot.source_set.split(",")[0],
                        article_summary=item,
                        comments_summary="No comment summary available in this older snapshot.",
                    )
                )
        return links

    top_links = [_to_story_link(x) for x in (data.get("top_links") or [])]
    best_rabbit_holes = [_to_story_link(x) for x in (data.get("best_rabbit_holes") or [])]

    return DigestOut(
        id=snapshot.id,
        kind=snapshot.kind,
        created_at=snapshot.created_at,
        sources=snapshot.source_set.split(","),
        item_count=snapshot.item_count,
        llm_provider=snapshot.llm_provider,
        run_origin=data.get("run_origin", "manual"),
        ai_summary=snapshot.summary_text,
        excitement_score=float(data.get("excitement_score", 0.0)),
        skepticism_score=float(data.get("skepticism_score", 0.0)),
        today_themes=today_themes,
        excited_about=_to_story_link_list(data.get("excited_about") or []),
        skeptical_about=_to_story_link_list(data.get("skeptical_about") or []),
        most_mentioned_tools=most_mentioned_tools,
        top_links=top_links,
        best_rabbit_holes=best_rabbit_holes,
        note=data.get("note", ""),
        generated_at=data.get("generated_at", snapshot.created_at.isoformat()),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok", app=settings.app_name)


@router.get("/digest/latest", response_model=DigestOut)
def latest_digest(
    source: SourceScope | None = Query(default=None),
    db: Session = Depends(get_db),
) -> DigestOut:
    q = select(Snapshot).order_by(desc(Snapshot.created_at))
    if source:
        q = q.where(Snapshot.source_set == source.value)
    snapshot = db.execute(q.limit(1)).scalars().first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="No digests yet. Trigger a refresh first.")
    return _snapshot_to_digest(snapshot)


@router.get("/digest/{digest_id}", response_model=DigestOut)
def get_digest(digest_id: int, db: Session = Depends(get_db)) -> DigestOut:
    snapshot = db.get(Snapshot, digest_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Digest not found")
    return _snapshot_to_digest(snapshot)


@router.get("/digest/{digest_id}/full", response_model=SnapshotDetailOut)
def get_snapshot_detail(digest_id: int, db: Session = Depends(get_db)) -> SnapshotDetailOut:
    snapshot = db.get(Snapshot, digest_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Digest not found")

    digest = _snapshot_to_digest(snapshot)
    payload = json.loads(snapshot.data_json)
    return SnapshotDetailOut(
        **digest.model_dump(),
        data=payload,
    )


@router.get("/digest", response_model=list[DigestListItem])
def list_digests(
    limit: int = Query(default=20, ge=1, le=200),
    kind: DigestKind | None = Query(default=None),
    source: SourceScope | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[DigestListItem]:
    q = select(Snapshot).order_by(desc(Snapshot.created_at))
    if kind:
        q = q.where(Snapshot.kind == kind)
    if source:
        q = q.where(Snapshot.source_set == source.value)
    rows = db.execute(q.limit(limit)).scalars().all()
    items: list[DigestListItem] = []
    for row in rows:
        payload = json.loads(row.data_json)
        items.append(
            DigestListItem(
                id=row.id,
                kind=row.kind,
                created_at=row.created_at,
                sources=row.source_set.split(","),
                item_count=row.item_count,
                llm_provider=row.llm_provider,
                run_origin=payload.get("run_origin", "manual"),
            )
        )
    return items


@router.get("/metrics/timeseries", response_model=MetricsTimeseriesOut)
def metrics_timeseries(
    limit: int = Query(default=30, ge=1, le=240),
    kind: DigestKind | None = Query(default=None),
    run_origin: RunOrigin | None = Query(default=None),
    db: Session = Depends(get_db),
) -> MetricsTimeseriesOut:
    q = select(Snapshot).order_by(desc(Snapshot.created_at))
    if kind:
        q = q.where(Snapshot.kind == kind)

    rows = db.execute(q.limit(limit)).scalars().all()
    rows.reverse()

    points: list[MetricPointOut] = []
    for row in rows:
        payload = json.loads(row.data_json)
        if run_origin and payload.get("run_origin") != run_origin.value:
            continue
        excitement = payload.get("excitement_score")
        skepticism = payload.get("skepticism_score")

        points.append(
            MetricPointOut(
                created_at=row.created_at,
                excitement_score=float(excitement) if excitement is not None else None,
                skepticism_score=float(skepticism) if skepticism is not None else None,
                item_count=row.item_count if row.item_count is not None else None,
            )
        )

    return MetricsTimeseriesOut(points=points)


def _latest_by_kind(kind: DigestKind, db: Session, source: SourceScope | None = None) -> Snapshot | None:
    q = select(Snapshot).where(Snapshot.kind == kind.value)
    if source:
        q = q.where(Snapshot.source_set == source.value)
    return db.execute(q.order_by(desc(Snapshot.created_at)).limit(1)).scalars().first()


@router.get("/digest/daily-preview/latest", response_model=DigestSectionOut)
def latest_daily_preview(
    source: SourceScope | None = Query(default=None),
    db: Session = Depends(get_db),
) -> DigestSectionOut:
    snapshot = _latest_by_kind(DigestKind.daily_preview, db, source=source)
    return DigestSectionOut(latest=_snapshot_to_digest(snapshot) if snapshot else None)


@router.get("/digest/daily-summary/latest", response_model=DigestSectionOut)
def latest_daily_summary(
    source: SourceScope | None = Query(default=None),
    db: Session = Depends(get_db),
) -> DigestSectionOut:
    snapshot = _latest_by_kind(DigestKind.daily_summary, db, source=source)
    return DigestSectionOut(latest=_snapshot_to_digest(snapshot) if snapshot else None)


@router.post(
    "/admin/refresh",
    response_model=DigestOut,
    dependencies=[Depends(_require_admin), Depends(_require_local_admin_runtime)],
)
async def refresh_now(
    kind: DigestKind = Query(default=DigestKind.regular, description="regular | daily_summary | daily_preview"),
    source: SourceScope = Query(default=SourceScope.hackernews, description="hackernews | reddit"),
    db: Session = Depends(get_db),
) -> DigestOut:
    pipeline = DigestPipeline(db)
    snapshot = await pipeline.run_cycle(kind=kind.value, run_origin="manual", source_filter=source.value)
    return _snapshot_to_digest(snapshot)


@router.post(
    "/admin/refresh/override",
    response_model=DigestOut,
    dependencies=[Depends(_require_admin), Depends(_require_local_admin_runtime)],
)
async def admin_override_refresh(
    source: SourceScope = Query(default=SourceScope.hackernews, description="hackernews | reddit"),
    db: Session = Depends(get_db),
) -> DigestOut:
    """Single-click admin override (local-only), replacing the old 10-click flow."""
    global admin_override_started_at

    if admin_override_started_at is not None:
        raise HTTPException(status_code=409, detail="Admin override is already running.")

    admin_override_started_at = datetime.utcnow()
    try:
        pipeline = DigestPipeline(db)
        snapshot = await pipeline.run_cycle(
            kind="super_duper_manual_trigger",
            run_origin="super_manual",
            source_filter=source.value,
        )
        return _snapshot_to_digest(snapshot)
    finally:
        admin_override_started_at = None


@router.post("/admin/refresh/queue", dependencies=[Depends(_require_admin), Depends(_require_local_admin_runtime)])
async def queue_refresh() -> dict:
    return await queue_manual_refresh_trigger()


@router.post("/admin/refresh/queue/cancel", dependencies=[Depends(_require_admin), Depends(_require_local_admin_runtime)])
async def cancel_queue_refresh() -> dict:
    return await cancel_manual_refresh_trigger()


@router.get("/metrics/run-history", response_model=RunHistoryOut)
def run_history(
    run_origin: RunOrigin = Query(default=RunOrigin.manual),
    limit: int = Query(default=120, ge=1, le=500),
    db: Session = Depends(get_db),
) -> RunHistoryOut:
    rows = db.execute(select(Snapshot).order_by(desc(Snapshot.created_at)).limit(limit)).scalars().all()
    rows.reverse()

    points: list[RunHistoryPointOut] = []
    for row in rows:
        payload = json.loads(row.data_json)
        if payload.get("run_origin") != run_origin.value:
            continue
        points.append(
            RunHistoryPointOut(
                created_at=row.created_at,
                item_count=row.item_count if row.item_count is not None else None,
                excitement_score=float(payload.get("excitement_score")) if payload.get("excitement_score") is not None else None,
                skepticism_score=float(payload.get("skepticism_score")) if payload.get("skepticism_score") is not None else None,
            )
        )

    return RunHistoryOut(run_origin=run_origin.value, points=points)


@router.get("/research/local-llms/live-ranking", response_model=LocalModelRankingOut)
async def local_llm_live_ranking(force_refresh: bool = Query(default=False)) -> LocalModelRankingOut:
    payload = await local_llm_ranker.get_live_ranking(force_refresh=force_refresh)
    return LocalModelRankingOut(**payload)


@router.get("/research/cloud-llms/live-ranking", response_model=LocalModelRankingOut)
async def cloud_llm_live_ranking(force_refresh: bool = Query(default=False)) -> LocalModelRankingOut:
    payload = await cloud_llm_ranker.get_live_ranking(force_refresh=force_refresh)
    return LocalModelRankingOut(**payload)


def _fallback_research_vibe(scope: str, items: list[dict]) -> str:
    if not items:
        return "No ranking data is available yet, so there is no clear model momentum signal right now."

    names = [str(x.get("model_name", "")).strip() for x in items[:3] if x.get("model_name")]
    top = items[0]
    top_name = str(top.get("model_name", "top model"))
    spread = 0
    if len(items) > 1:
        try:
            spread = int(top.get("qualitative_score", 0)) - int(items[1].get("qualitative_score", 0))
        except Exception:
            spread = 0

    return (
        f"{scope.title()} model vibe: {top_name} is currently setting the pace, with "
        f"{', '.join(names) if names else 'the top ranked models'} leading the shortlist. "
        f"The front of the ranking is {'tight' if spread < 6 else 'clearly separated'}, "
        "so this looks like an active field where short-term movement is likely after the next metadata refresh."
    )


async def _build_research_vibe(scope: ResearchScope, generated_at: str, items: list[dict]) -> LlmVibeCheckOut:
    provider = get_llm_provider()

    prompt = (
        "You are writing a short 'vibe check' summary for an LLM ranking widget. "
        "Write 3-4 concise sentences in plain text with no markdown. "
        "Mention leadership at the top, one risk/tradeoff, and one practical recommendation.\n\n"
        f"Scope: {scope.value}\n"
        f"Top items: {json.dumps(items[:6])}\n"
    )

    summary_text = ""
    try:
        summary_text = (await provider.generate_text(prompt) or "").strip()
    except Exception:
        summary_text = ""

    if not summary_text:
        summary_text = _fallback_research_vibe(scope.value, items)

    return LlmVibeCheckOut(
        generated_at=generated_at,
        scope=scope.value,
        llm_provider=getattr(provider, "label", "none"),
        ai_summary=summary_text,
    )


@router.get("/research/llm-vibe-check", response_model=LlmVibeCheckOut)
async def research_llm_vibe_check(
    scope: ResearchScope = Query(default=ResearchScope.local),
    force_refresh: bool = Query(default=False),
) -> LlmVibeCheckOut:
    payload = (
        await local_llm_ranker.get_live_ranking(force_refresh=force_refresh)
        if scope == ResearchScope.local
        else await cloud_llm_ranker.get_live_ranking(force_refresh=force_refresh)
    )
    items = payload.get("items") or []
    return await _build_research_vibe(
        scope=scope,
        generated_at=payload.get("generated_at", ""),
        items=items,
    )


@router.get("/research/overview", response_model=ResearchOverviewOut)
async def research_overview(
    scope: ResearchScope = Query(default=ResearchScope.local),
    force_refresh: bool = Query(default=False),
) -> ResearchOverviewOut:
    payload = (
        await local_llm_ranker.get_live_ranking(force_refresh=force_refresh)
        if scope == ResearchScope.local
        else await cloud_llm_ranker.get_live_ranking(force_refresh=force_refresh)
    )

    items = payload.get("items") or []
    ranking = LocalModelRankingOut(**payload)
    vibe = await _build_research_vibe(
        scope=scope,
        generated_at=payload.get("generated_at", ""),
        items=items,
    )

    return ResearchOverviewOut(scope=scope.value, ranking=ranking, vibe=vibe)


# ---------------------------------------------------------------------------
# Provider toggle (runtime, in-memory only)
# ---------------------------------------------------------------------------

_VALID_PROVIDERS = {"none", "heuristic", "openai", "ollama", "auto"}


@router.get("/admin/provider")
def get_provider() -> dict:
    return {"provider": settings.llm_provider}


@router.post("/admin/provider", dependencies=[Depends(_require_admin)])
def set_provider(provider: str = Query(..., description="none | heuristic | openai | ollama | auto")) -> dict:
    if provider not in _VALID_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Invalid provider. Choose from: {', '.join(sorted(_VALID_PROVIDERS))}")
    settings.llm_provider = provider
    return {"provider": settings.llm_provider}


@router.get("/admin/scheduler/jobs")
def scheduler_jobs() -> dict:
    jobs = []
    for job in scheduler.get_jobs():
        if not _is_user_visible_scheduler_job(job):
            continue
        jobs.append(
            {
                "id": job.id,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "kind": _scheduler_job_kind(job),
            }
        )

    queue_state = get_manual_refresh_queue_state()
    if queue_state["pending"]:
        jobs.append(
            {
                "id": "manual_refresh_queue",
                "next_run_time": queue_state["due_at"],
                "kind": "queued_manual_refresh",
            }
        )

    return {
        "running": bool(scheduler.running),
        "jobs": jobs,
    }


@router.get("/admin/scheduler/overview")
def scheduler_overview(db: Session = Depends(get_db)) -> dict:
    recent_rows = db.execute(select(Snapshot).order_by(desc(Snapshot.created_at)).limit(3)).scalars().all()
    recent = [
        {
            "id": row.id,
            "kind": row.kind,
            "created_at": row.created_at.isoformat(),
            "llm_provider": row.llm_provider,
        }
        for row in recent_rows
    ]

    upcoming = []
    for job in scheduler.get_jobs():
        if not _is_user_visible_scheduler_job(job):
            continue

        kind = _scheduler_job_kind(job)
        projection_count = 3 if job.id == "regular_refresh" and kind == "regular" else 1
        for next_time in _project_job_times(job, projection_count):
            upcoming.append(
                {
                    "id": job.id,
                    "kind": kind,
                    "next_run_time": next_time.isoformat(),
                }
            )

    queue_state = get_manual_refresh_queue_state()
    if queue_state["pending"]:
        upcoming.append(
            {
                "id": "manual_refresh_queue",
                "kind": "queued_manual_refresh",
                "next_run_time": queue_state["due_at"],
            }
        )

    if admin_override_started_at is not None:
        upcoming.append(
            {
                "id": "admin_override_running",
                "kind": "admin_override_running",
                "next_run_time": admin_override_started_at.isoformat(),
            }
        )

    upcoming.sort(key=lambda item: item["next_run_time"])

    return {
        "running": bool(scheduler.running),
        "recent_snapshots": recent,
        "upcoming_snapshots": upcoming[:3],
        "manual_queue": queue_state,
        "admin_override": {
            "running": admin_override_started_at is not None,
            "started_at": admin_override_started_at.isoformat() if admin_override_started_at else None,
        },
    }
