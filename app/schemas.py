from datetime import datetime
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Reusable sub-types — stable contract for external consumers
# ---------------------------------------------------------------------------

class TopicOut(BaseModel):
    topic: str
    count: int
    signal: float
    headlines: list[str]


class ToolMentionOut(BaseModel):
    name: str
    count: int


class StoryLinkOut(BaseModel):
    title: str
    url: str
    score: int
    comments: int
    source: str
    reason: str = ""
    article_summary: str = ""
    article_summary_ai: str = ""
    comments_summary: str = ""


# ---------------------------------------------------------------------------
# Primary digest shape — the single response type external frontends consume
# ---------------------------------------------------------------------------

class DigestOut(BaseModel):
    id: int
    kind: str
    created_at: datetime
    sources: list[str]
    item_count: int
    llm_provider: str
    run_origin: str = "manual"

    # Narrative summary (empty when llm_provider is heuristic)
    ai_summary: str

    # Scored signals
    excitement_score: float
    skepticism_score: float

    # Structured lists
    today_themes: list[TopicOut]
    excited_about: list[StoryLinkOut]
    skeptical_about: list[StoryLinkOut]
    most_mentioned_tools: list[ToolMentionOut]
    top_links: list[StoryLinkOut]
    best_rabbit_holes: list[StoryLinkOut]

    # Meta
    note: str
    generated_at: str


class DigestListItem(BaseModel):
    id: int
    kind: str
    created_at: datetime
    sources: list[str]
    item_count: int
    llm_provider: str
    run_origin: str = "manual"


class MetricPointOut(BaseModel):
    created_at: datetime
    excitement_score: float | None
    skepticism_score: float | None
    item_count: int | None


class MetricsTimeseriesOut(BaseModel):
    points: list[MetricPointOut]


class RunHistoryPointOut(BaseModel):
    created_at: datetime
    item_count: int | None
    excitement_score: float | None
    skepticism_score: float | None


class RunHistoryOut(BaseModel):
    run_origin: str
    points: list[RunHistoryPointOut]


class DigestSectionOut(BaseModel):
    latest: DigestOut | None


class SnapshotDetailOut(DigestOut):
    data: dict[str, Any]


class SourceRefOut(BaseModel):
    label: str
    url: str


class LocalModelRankItemOut(BaseModel):
    rank: int
    model_name: str
    model_id: str
    rationale: str
    qualitative_score: float
    signals: dict[str, float | int | str]
    sources: list[SourceRefOut]


class LocalModelRankingOut(BaseModel):
    generated_at: str
    methodology: list[str]
    legal_ethics: list[str]
    items: list[LocalModelRankItemOut]


class LlmVibeCheckOut(BaseModel):
    generated_at: str
    scope: str
    llm_provider: str
    ai_summary: str


class ResearchOverviewOut(BaseModel):
    scope: str
    ranking: LocalModelRankingOut
    vibe: LlmVibeCheckOut


class HealthOut(BaseModel):
    status: str
    app: str
