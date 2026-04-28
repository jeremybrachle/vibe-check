import asyncio
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Snapshot
from app.services.pipeline import DigestPipeline


scheduler = AsyncIOScheduler()
manual_refresh_lock = asyncio.Lock()
manual_refresh_due_at: datetime | None = None
manual_refresh_trigger_count: int = 0
manual_refresh_click_window_ends_at: datetime | None = None
last_super_manual_trigger_at: datetime | None = None
manual_refresh_running_since: datetime | None = None
manual_refresh_started_windows: list[datetime] = []

MANUAL_QUEUE_WINDOW_MINUTES = 20
MANUAL_QUEUE_THRESHOLD = 10
SUPER_MANUAL_COOLDOWN_MINUTES = 60
MANUAL_REFRESH_LIMIT_WINDOW_MINUTES = 120
MANUAL_REFRESH_LIMIT_COUNT = 999  # temporarily unlimited for testing


def _prune_manual_started(now: datetime) -> None:
    cutoff = now - timedelta(minutes=MANUAL_REFRESH_LIMIT_WINDOW_MINUTES)
    manual_refresh_started_windows[:] = [t for t in manual_refresh_started_windows if t >= cutoff]


def _next_manual_window_at(now: datetime) -> datetime | None:
    _prune_manual_started(now)
    if len(manual_refresh_started_windows) < MANUAL_REFRESH_LIMIT_COUNT:
        return None
    earliest = min(manual_refresh_started_windows)
    return earliest + timedelta(minutes=MANUAL_REFRESH_LIMIT_WINDOW_MINUTES)


async def _run_job(kind: str, run_origin: str = "scheduled") -> None:
    db: Session = SessionLocal()
    try:
        pipeline = DigestPipeline(db)
        await pipeline.run_cycle(kind=kind, run_origin=run_origin)
    finally:
        db.close()


async def _run_daily_with_backstop(kind: str, lookback_minutes: int = 120) -> None:
    db: Session = SessionLocal()
    try:
        latest = (
            db.execute(select(Snapshot).where(Snapshot.kind == kind).order_by(desc(Snapshot.created_at)).limit(1))
            .scalars()
            .first()
        )
        if latest and latest.created_at >= (datetime.utcnow() - timedelta(minutes=lookback_minutes)):
            return

        pipeline = DigestPipeline(db)
        await pipeline.run_cycle(kind=kind, run_origin="scheduled")
    finally:
        db.close()


async def _process_manual_refresh_queue() -> None:
    global manual_refresh_due_at, manual_refresh_trigger_count, manual_refresh_running_since, manual_refresh_click_window_ends_at

    should_run = False
    started_at: datetime | None = None
    async with manual_refresh_lock:
        if manual_refresh_due_at and datetime.utcnow() >= manual_refresh_due_at:
            should_run = True
            started_at = datetime.utcnow()
            _prune_manual_started(started_at)
            manual_refresh_started_windows.append(started_at)
            manual_refresh_running_since = started_at
            manual_refresh_due_at = None
            # NOTE: do NOT reset trigger_count / click_window here.
            # The click counter is independent of the queue window — it
            # accumulates across multiple queued runs so the user can hit
            # 10 total clicks for the super-duper override.

    if not should_run:
        return

    try:
        await _run_job(kind="regular", run_origin="queued_manual")
    finally:
        async with manual_refresh_lock:
            manual_refresh_running_since = None


async def queue_manual_refresh_trigger() -> dict:
    global manual_refresh_due_at, manual_refresh_trigger_count, last_super_manual_trigger_at, manual_refresh_click_window_ends_at

    now = datetime.utcnow()
    auto_trigger = False
    due_at: datetime | None = None
    cooldown_remaining_seconds = 0
    window_blocked_until: datetime | None = None
    is_running = False

    async with manual_refresh_lock:
        _prune_manual_started(now)
        is_running = manual_refresh_running_since is not None

        if manual_refresh_click_window_ends_at is None or now >= manual_refresh_click_window_ends_at:
            manual_refresh_click_window_ends_at = now + timedelta(hours=4)  # 4-hour click accumulation window
            manual_refresh_trigger_count = 1
        else:
            manual_refresh_trigger_count += 1

        if manual_refresh_trigger_count >= MANUAL_QUEUE_THRESHOLD:
            cooldown_until = (
                last_super_manual_trigger_at + timedelta(minutes=SUPER_MANUAL_COOLDOWN_MINUTES)
                if last_super_manual_trigger_at
                else None
            )
            if cooldown_until and now < cooldown_until:
                cooldown_remaining_seconds = max(0, int((cooldown_until - now).total_seconds()))
                manual_refresh_trigger_count = MANUAL_QUEUE_THRESHOLD
                due_at = manual_refresh_due_at
            else:
                auto_trigger = True
                last_super_manual_trigger_at = now
                manual_refresh_due_at = None
                manual_refresh_trigger_count = 0
                manual_refresh_click_window_ends_at = None

        if not auto_trigger and cooldown_remaining_seconds == 0:
            window_blocked_until = _next_manual_window_at(now)
            if not is_running and not window_blocked_until and (manual_refresh_due_at is None or now >= manual_refresh_due_at):
                manual_refresh_due_at = now + timedelta(minutes=MANUAL_QUEUE_WINDOW_MINUTES)

            due_at = manual_refresh_due_at

    if cooldown_remaining_seconds > 0:
        seconds_until_due = max(0, int((due_at - now).total_seconds())) if due_at else 0
        return {
            "status": "cooldown",
            "message": "Super duper manual trigger is limited to once per hour.",
            "due_at": due_at.isoformat() if due_at else None,
            "trigger_count": MANUAL_QUEUE_THRESHOLD,
            "seconds_until_due": seconds_until_due,
            "threshold": MANUAL_QUEUE_THRESHOLD,
            "cooldown_seconds_remaining": cooldown_remaining_seconds,
        }

    if window_blocked_until and not auto_trigger and not is_running and due_at is None:
        wait_seconds = max(0, int((window_blocked_until - now).total_seconds()))
        return {
            "status": "rate_limited",
            "message": "Manual refresh limit reached. Next refresh must wait for the next 2-hour window unless you trigger super duper mode.",
            "next_window_at": window_blocked_until.isoformat(),
            "seconds_until_next_window": wait_seconds,
            "window_minutes": MANUAL_REFRESH_LIMIT_WINDOW_MINUTES,
            "max_refreshes": MANUAL_REFRESH_LIMIT_COUNT,
            "trigger_count": manual_refresh_trigger_count,
            "threshold": MANUAL_QUEUE_THRESHOLD,
        }

    if is_running and not auto_trigger:
        return {
            "status": "in_progress",
            "message": "A queued refresh already started and is currently running.",
            "started_at": manual_refresh_running_since.isoformat() if manual_refresh_running_since else None,
            "trigger_count": manual_refresh_trigger_count,
            "threshold": MANUAL_QUEUE_THRESHOLD,
        }

    if auto_trigger:
        db: Session = SessionLocal()
        try:
            pipeline = DigestPipeline(db)
            snapshot = await pipeline.run_cycle(kind="regular", run_origin="super_manual")
            return {
                "status": "auto_triggered",
                "message": "Congratulations, you're impatient. Super duper manual trigger activated.",
                "snapshot_id": snapshot.id,
                "kind": snapshot.kind,
                "created_at": snapshot.created_at.isoformat(),
            }
        finally:
            db.close()

    seconds_until_due = max(0, int((due_at - now).total_seconds())) if due_at else 0
    return {
        "status": "queued",
        "message": "Manual refresh queued. It will run after the 20-minute queue window.",
        "due_at": due_at.isoformat() if due_at else None,
        "trigger_count": manual_refresh_trigger_count,
        "seconds_until_due": seconds_until_due,
        "threshold": MANUAL_QUEUE_THRESHOLD,
    }


async def cancel_manual_refresh_trigger() -> dict:
    global manual_refresh_due_at

    now = datetime.utcnow()
    async with manual_refresh_lock:
        if manual_refresh_running_since is not None:
            started = manual_refresh_running_since
            return {
                "status": "already_running",
                "message": "Refresh already started and cannot be canceled. It still counts toward the 2-hour limit.",
                "started_at": started.isoformat(),
            }

        if manual_refresh_due_at and manual_refresh_due_at > now:
            due_at = manual_refresh_due_at
            trigger_count = manual_refresh_trigger_count
            manual_refresh_due_at = None
            return {
                "status": "canceled",
                "message": "Queued refresh canceled.",
                "canceled_due_at": due_at.isoformat(),
                "trigger_count": trigger_count,
            }

    return {
        "status": "no_pending",
        "message": "No queued refresh to cancel.",
    }


def get_manual_refresh_queue_state() -> dict:
    global manual_refresh_trigger_count, manual_refresh_click_window_ends_at

    now = datetime.utcnow()
    _prune_manual_started(now)
    if manual_refresh_click_window_ends_at and now >= manual_refresh_click_window_ends_at:
        manual_refresh_click_window_ends_at = None
        manual_refresh_trigger_count = 0
    pending = manual_refresh_due_at is not None and manual_refresh_due_at > now
    seconds_until_due = max(0, int((manual_refresh_due_at - now).total_seconds())) if pending else 0
    cooldown_seconds_remaining = 0
    next_window_at = _next_manual_window_at(now)
    seconds_until_next_window = max(0, int((next_window_at - now).total_seconds())) if next_window_at else 0
    if last_super_manual_trigger_at:
        cooldown_until = last_super_manual_trigger_at + timedelta(minutes=SUPER_MANUAL_COOLDOWN_MINUTES)
        if now < cooldown_until:
            cooldown_seconds_remaining = max(0, int((cooldown_until - now).total_seconds()))
    return {
        "pending": pending,
        "due_at": manual_refresh_due_at.isoformat() if manual_refresh_due_at else None,
        "trigger_count": manual_refresh_trigger_count,
        "threshold": MANUAL_QUEUE_THRESHOLD,
        "seconds_until_due": seconds_until_due,
        "window_minutes": MANUAL_QUEUE_WINDOW_MINUTES,
        "cooldown_seconds_remaining": cooldown_seconds_remaining,
        "running": manual_refresh_running_since is not None,
        "running_since": manual_refresh_running_since.isoformat() if manual_refresh_running_since else None,
        "max_refreshes": MANUAL_REFRESH_LIMIT_COUNT,
        "limit_window_minutes": MANUAL_REFRESH_LIMIT_WINDOW_MINUTES,
        "used_refreshes": len(manual_refresh_started_windows),
        "next_window_at": next_window_at.isoformat() if next_window_at else None,
        "seconds_until_next_window": seconds_until_next_window,
    }


def register_jobs() -> None:
    scheduler.add_job(
        _run_job,
        trigger=CronTrigger(hour="*/2", minute=0, timezone="UTC"),
        kwargs={"kind": "regular"},
        id="regular_refresh",
        replace_existing=True,
    )

    scheduler.add_job(
        _run_daily_with_backstop,
        trigger=CronTrigger(hour=17, minute=1, timezone="America/Los_Angeles"),
        kwargs={"kind": "daily_summary"},
        id="daily_summary_pacific",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    # Backup run 30 minutes later: catches missed 5:01 PM PT execution
    # but skips if the primary run already created a recent snapshot.
    scheduler.add_job(
        _run_daily_with_backstop,
        trigger=CronTrigger(hour=17, minute=31, timezone="America/Los_Angeles"),
        kwargs={"kind": "daily_summary"},
        id="daily_summary_pacific_backup",
        replace_existing=True,
        misfire_grace_time=7200,
    )

    scheduler.add_job(
        _run_daily_with_backstop,
        trigger=CronTrigger(hour=9, minute=1, timezone="America/New_York"),
        kwargs={"kind": "daily_preview"},
        id="daily_preview_eastern",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    # Backup run 30 minutes later: catches missed 9:01 AM ET execution
    # but skips if the primary run already created a recent snapshot.
    scheduler.add_job(
        _run_daily_with_backstop,
        trigger=CronTrigger(hour=9, minute=31, timezone="America/New_York"),
        kwargs={"kind": "daily_preview"},
        id="daily_preview_eastern_backup",
        replace_existing=True,
        misfire_grace_time=7200,
    )
