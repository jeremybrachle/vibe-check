import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import RawItem, Snapshot
from app.services.analysis import build_digest
from app.services.llm.factory import get_llm_provider
from app.services.sources.base import Story
from app.services.sources.registry import get_enabled_sources


logger = logging.getLogger(__name__)


ARCHITECTURE_NOTE = (
    "The architecture supports source-specific snapshots (Hacker News, Reddit, and more) "
    "plus local/cloud LLM summarization."
)


class DigestPipeline:
    def __init__(self, db: Session):
        self.db = db

    async def run_cycle(self, kind: str = "regular", run_origin: str = "manual", source_filter: str = "hackernews") -> Snapshot:
        stories = await self._pull_all_stories(source_filter=source_filter)
        if not stories:
            raise RuntimeError(f"No stories returned from sources for filter '{source_filter}'")

        self._store_raw(stories)
        digest = build_digest(stories, note=ARCHITECTURE_NOTE)

        context = self._build_context(kind)
        provider = get_llm_provider()
        llm_text, llm_label = await self._generate_llm_summary(digest, context=context, kind=kind, provider=provider)
        if llm_label in {"ollama", "openai"}:
            await self._rewrite_story_article_summaries(digest, provider)

        digest["ai_summary"] = llm_text
        digest["kind"] = kind
        digest["run_origin"] = run_origin
        digest["generated_at"] = datetime.utcnow().isoformat()

        snapshot = Snapshot(
            kind=kind,
            source_set=",".join(sorted({story.source for story in stories})),
            item_count=len(stories),
            llm_provider=llm_label,
            summary_text=llm_text,
            data_json=json.dumps(digest),
        )
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)

        if kind == "daily_summary":
            self._save_daily_summary_externally(snapshot, digest, context.get("previous_daily_preview", ""))

        return snapshot

    async def _pull_all_stories(self, source_filter: str | None = None) -> list[Story]:
        stories: list[Story] = []
        for source in get_enabled_sources(source_filter=source_filter):
            stories.extend(await source.fetch_stories())
        return stories

    def _store_raw(self, stories: list[Story]) -> None:
        now = datetime.utcnow()
        for story in stories:
            self.db.add(
                RawItem(
                    source=story.source,
                    feed=story.feed,
                    external_id=story.external_id,
                    title=story.title,
                    url=story.url,
                    score=story.score,
                    comment_count=story.comment_count,
                    published_at=story.published_at,
                    fetched_at=now,
                    payload_json=json.dumps(story.raw),
                )
            )
        self.db.commit()

    def _build_context(self, kind: str) -> dict[str, Any]:
        recent_snapshots = self.db.execute(select(Snapshot).order_by(desc(Snapshot.created_at)).limit(6)).scalars().all()
        previous_daily_summary = (
            self.db.execute(
                select(Snapshot)
                .where(Snapshot.kind == "daily_summary")
                .order_by(desc(Snapshot.created_at))
                .limit(1)
            )
            .scalars()
            .first()
        )
        previous_daily_preview = (
            self.db.execute(
                select(Snapshot)
                .where(Snapshot.kind == "daily_preview")
                .order_by(desc(Snapshot.created_at))
                .limit(1)
            )
            .scalars()
            .first()
        )

        return {
            "kind": kind,
            "recent_snapshots": [
                {
                    "id": snap.id,
                    "kind": snap.kind,
                    "created_at": snap.created_at.isoformat(),
                    "item_count": snap.item_count,
                    "summary_text": snap.summary_text,
                }
                for snap in recent_snapshots
            ],
            "previous_daily_summary": previous_daily_summary.summary_text if previous_daily_summary else "",
            "previous_daily_preview": previous_daily_preview.summary_text if previous_daily_preview else "",
        }

    async def _generate_llm_summary(
        self,
        digest: dict[str, Any],
        context: dict[str, Any],
        kind: str,
        provider: Any,
    ) -> tuple[str, str]:
        prompt = self._build_prompt(digest=digest, context=context, kind=kind)

        provider_label = getattr(provider, "label", "unknown")
        try:
            text = await provider.generate_text(prompt)
            if not (text or "").strip():
                logger.warning(
                    "LLM provider %r returned empty text for kind=%s; snapshot will have no AI summary.",
                    provider_label,
                    kind,
                )
                return "", "none"
            return text, provider.label
        except Exception as exc:
            # Never fail the pipeline because of LLM setup. Log loudly so the
            # operator can see why ai_summary is blank instead of silently
            # writing "none" snapshots.
            logger.exception(
                "LLM provider %r failed during %s pipeline run: %s",
                provider_label,
                kind,
                exc,
            )
            return "", "none"

    async def _rewrite_story_article_summaries(self, digest: dict[str, Any], provider: Any) -> None:
        sections = ["top_links", "best_rabbit_holes", "excited_about", "skeptical_about"]
        unique_items: dict[tuple[str, str], dict[str, Any]] = {}

        for section in sections:
            for item in digest.get(section, []) or []:
                title = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                if not title:
                    continue
                key = (title, url)
                if key not in unique_items:
                    unique_items[key] = item

        rewritten: dict[tuple[str, str], str] = {}
        for key, item in list(unique_items.items())[:10]:
            prompt = self._build_story_rewrite_prompt(item)
            try:
                response = await provider.generate_text(prompt)
                ai_text = self._extract_story_summary(response)
                if ai_text:
                    rewritten[key] = ai_text
            except Exception:
                continue

        if not rewritten:
            return

        for section in sections:
            for item in digest.get(section, []) or []:
                key = ((item.get("title") or "").strip(), (item.get("url") or "").strip())
                if key in rewritten:
                    item["article_summary_ai"] = rewritten[key]

    def _build_story_rewrite_prompt(self, item: dict[str, Any]) -> str:
        return (
            "You are summarizing one Hacker News story.\n"
            "Return JSON only with key: article_summary_ai.\n"
            "Constraints: 1-2 sentences, factual, <= 45 words, no markdown.\n\n"
            f"Title: {item.get('title', '')}\n"
            f"Article extract: {item.get('article_summary', '')}\n"
            f"Top comments extract: {item.get('comments_summary', '')}\n"
        )

    def _extract_story_summary(self, response: str) -> str:
        text = (response or "").strip()
        if not text:
            return ""

        # Strip fenced blocks if a provider wraps JSON in markdown.
        text = text.replace("```json", "").replace("```", "").strip()

        candidate = ""
        if "{" in text and "}" in text:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            raw_json = match.group(0) if match else text
            try:
                data = json.loads(raw_json)
                candidate = (
                    data.get("article_summary_ai")
                    or data.get("article_summary")
                    or ""
                )
            except Exception:
                candidate = ""

        if not candidate:
            candidate = text

        words = candidate.split()
        if len(words) > 45:
            candidate = " ".join(words[:45]).strip() + "..."
        return candidate.strip()

    def _build_prompt(self, digest: dict[str, Any], context: dict[str, Any], kind: str) -> str:
        themes = json.dumps(digest.get("today_themes", [])[:6])
        excited = json.dumps(digest.get("excited_about", [])[:5])
        skeptical = json.dumps(digest.get("skeptical_about", [])[:5])
        tools = json.dumps(digest.get("most_mentioned_tools", [])[:8])

        if kind == "daily_preview":
            return (
                "You are 'The Vibe Check' morning analyst. Keep this mostly about predictions.\n"
                "Output format:\n"
                "- Start with 1 short update sentence about changes since the last 5:01 PM PT summary. "
                "If there are no material changes, say: 'Nothing major is new since 5:01.'\n"
                "- Then provide exactly 3 numbered predictions for today, based on current data.\n"
                "- Each prediction should be specific, concise, and mention likely excitement/debate where relevant.\n\n"
                f"Last 5:01 PM PT summary: {context.get('previous_daily_summary') or 'none yet'}\n"
                f"Current early-morning themes: {themes}\n"
                f"Stories generating excitement: {excited}\n"
                f"Stories generating skepticism: {skeptical}\n"
                f"Trending tools: {tools}\n"
            )
        elif kind == "daily_summary":
            return (
                "You are 'The Vibe Check' evening analyst. Write 3-5 sentences summarizing what "
                "actually happened in tech today on Hacker News. Compare against the morning "
                "prediction — note what was accurate and what was missed. Highlight what excited "
                "the community and what generated the most debate.\n\n"
                f"This morning's prediction: {context.get('previous_daily_preview') or 'none'}\n"
                f"Today's actual themes: {themes}\n"
                f"What excited the community: {excited}\n"
                f"What generated skepticism/debate: {skeptical}\n"
                f"Most-mentioned tools: {tools}\n"
            )
        else:
            return (
                "You are generating a digest for 'The Vibe Check'.\n"
                "Return 3-5 sentences. Focus on: major themes, what people are excited about, "
                "what they are skeptical about, and whether there are build-worthy opportunities.\n\n"
                f"Top themes: {themes}\n"
                f"Excited about: {excited}\n"
                f"Skeptical about: {skeptical}\n"
                f"Most mentioned tools: {tools}\n"
                f"Context: {json.dumps(context)}\n"
            )

    def _save_daily_summary_externally(self, snapshot: Snapshot, digest: dict[str, Any], preview_summary: str) -> None:
        data_dir = Path(__file__).resolve().parents[2] / "data"
        data_dir.mkdir(exist_ok=True)
        record = {
            "date": snapshot.created_at.strftime("%Y-%m-%d"),
            "created_at": snapshot.created_at.isoformat(),
            "snapshot_id": snapshot.id,
            "excitement_score": digest.get("excitement_score"),
            "skepticism_score": digest.get("skepticism_score"),
            "item_count": snapshot.item_count,
            "top_themes": [t["topic"] for t in digest.get("today_themes", [])[:5]],
            "top_tools": [t["name"] for t in digest.get("most_mentioned_tools", [])[:5]],
            "ai_summary": snapshot.summary_text,
            "morning_preview_summary": preview_summary,
            "llm_provider": snapshot.llm_provider,
        }
        with open(data_dir / "daily_summaries.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
