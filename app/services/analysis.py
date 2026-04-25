import html
import re
from collections import Counter, defaultdict
from typing import Any

from app.services.sources.base import Story


TOPIC_KEYWORDS = {
    "AI": ["ai", "llm", "gpt", "model", "machine learning", "neural", "inference"],
    "Programming": ["python", "rust", "go", "javascript", "typescript", "framework", "library"],
    "Security": ["security", "vulnerability", "cve", "exploit", "auth", "encryption"],
    "Startups": ["startup", "funding", "yc", "founder", "business", "saas"],
    "Hardware": ["chip", "gpu", "cpu", "hardware", "embedded", "robotics"],
    "Science": ["science", "research", "paper", "biology", "physics", "chemistry"],
}

EXCITEMENT_WORDS = {"breakthrough", "amazing", "impressive", "fast", "huge", "wow", "love"}
SKEPTICISM_WORDS = {"hype", "concern", "risk", "problem", "slow", "skeptical", "doubt"}
TOOL_HINT_WORDS = {
    "openai",
    "ollama",
    "langchain",
    "llama",
    "postgres",
    "sqlite",
    "redis",
    "kubernetes",
    "react",
    "fastapi",
    "docker",
    "anthropic",
    "github",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _pick_topics(story: Story) -> list[str]:
    blob = _normalize(" ".join([story.title, story.text or "", " ".join(story.top_comments)]))
    matched: list[str] = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in blob for keyword in keywords):
            matched.append(topic)
    return matched or ["Programming"]


def _score_signal(story: Story) -> float:
    return float(story.score) + (0.4 * float(story.comment_count))


def _sentiment_hits(story: Story) -> tuple[int, int]:
    blob = _normalize(" ".join([story.title, story.text or "", " ".join(story.top_comments)]))
    excitement = sum(1 for word in EXCITEMENT_WORDS if word in blob)
    skepticism = sum(1 for word in SKEPTICISM_WORDS if word in blob)
    return excitement, skepticism


def _plain_text(value: str) -> str:
    decoded = html.unescape(value or "")
    no_tags = re.sub(r"<[^>]+>", " ", decoded)
    return re.sub(r"\s+", " ", no_tags).strip()


def _summarize_words(value: str, max_words: int = 28) -> str:
    words = _plain_text(value).split()
    if not words:
        return ""
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "..."


def _article_summary(story: Story) -> str:
    if story.text:
        return _summarize_words(story.text)
    return _summarize_words(story.title)


def _comments_summary(story: Story) -> str:
    comments = [c for c in story.top_comments if _plain_text(c)]
    if not comments:
        return "No top comments were captured for this snapshot."
    top = comments[:2]
    joined = " ".join(_summarize_words(c, max_words=18) for c in top)
    return joined or "Top comments exist but did not contain enough plain text to summarize."


def _excited_reason(story: Story) -> str:
    blob = _normalize(" ".join([story.title, story.text or "", " ".join(story.top_comments)]))
    hits = [word for word in sorted(EXCITEMENT_WORDS) if word in blob]
    parts: list[str] = []
    if hits:
        parts.append(f"positive signals: {', '.join(hits)}")
    if story.score >= 200:
        parts.append(f"high community endorsement ({story.score} pts)")
    elif story.score >= 50:
        parts.append(f"notable score ({story.score} pts)")
    if story.comment_count >= 50:
        parts.append(f"active discussion ({story.comment_count} comments)")
    if not parts:
        parts.append(f"strong engagement ({story.score} pts, {story.comment_count} comments)")
    return "; ".join(parts).capitalize() + "."


def _skeptical_reason(story: Story) -> str:
    blob = _normalize(" ".join([story.title, story.text or "", " ".join(story.top_comments)]))
    hits = [word for word in sorted(SKEPTICISM_WORDS) if word in blob]
    parts: list[str] = []
    if hits:
        parts.append(f"cautionary signals: {', '.join(hits)}")
    if story.comment_count >= 100:
        parts.append(f"high debate volume ({story.comment_count} comments)")
    elif story.comment_count >= 30:
        parts.append(f"active discussion ({story.comment_count} comments)")
    ratio = story.comment_count / max(story.score, 1)
    if ratio > 0.4:
        parts.append("elevated comment-to-score ratio suggests pushback")
    if not parts:
        parts.append(f"discussion activity ({story.comment_count} comments, {story.score} pts)")
    return "; ".join(parts).capitalize() + "."


def _to_story_link_payload(story: Story, reason: str = "") -> dict[str, Any]:
    return {
        "title": story.title,
        "url": story.url,
        "score": story.score,
        "comments": story.comment_count,
        "source": story.source,
        "reason": reason,
        "article_summary": _article_summary(story),
        "comments_summary": _comments_summary(story),
    }


def build_digest(stories: list[Story], note: str) -> dict[str, Any]:
    topic_buckets: dict[str, list[Story]] = defaultdict(list)
    tool_counter: Counter[str] = Counter()

    total_signal = 0.0
    excitement_signal = 0.0
    skepticism_signal = 0.0

    for story in stories:
        story_topics = _pick_topics(story)
        for topic in story_topics:
            topic_buckets[topic].append(story)

        signal = _score_signal(story)
        total_signal += max(signal, 1.0)

        excitement_hits, skepticism_hits = _sentiment_hits(story)
        excitement_signal += signal * (1 + excitement_hits)
        skepticism_signal += signal * (1 + skepticism_hits)

        words = re.findall(r"[a-zA-Z0-9\-\.]+", _normalize(story.title))
        for word in words:
            if word in TOOL_HINT_WORDS:
                tool_counter[word] += 1

    sorted_topics = sorted(
        (
            {
                "topic": topic,
                "count": len(items),
                "signal": round(sum(_score_signal(item) for item in items), 2),
                "headlines": [item.title for item in sorted(items, key=_score_signal, reverse=True)[:5]],
            }
            for topic, items in topic_buckets.items()
        ),
        key=lambda x: x["signal"],
        reverse=True,
    )

    scored_stories = sorted(stories, key=_score_signal, reverse=True)
    rabbit_holes = [_to_story_link_payload(s) for s in scored_stories[:6]]

    excitement_score = round((excitement_signal / max(total_signal, 1.0)), 2)
    skepticism_score = round((skepticism_signal / max(total_signal, 1.0)), 2)

    excited_about = [_to_story_link_payload(story, reason=_excited_reason(story)) for story in scored_stories[:5]]
    skeptical_about = [
        _to_story_link_payload(story, reason=_skeptical_reason(story))
        for story in sorted(stories, key=lambda s: s.comment_count, reverse=True)[:5]
    ]

    return {
        "note": note,
        "counts": {
            "stories": len(stories),
            "topics": len(sorted_topics),
        },
        "today_themes": sorted_topics,
        "excitement_score": excitement_score,
        "skepticism_score": skepticism_score,
        "excited_about": excited_about,
        "skeptical_about": skeptical_about,
        "most_mentioned_tools": [{"name": name, "count": count} for name, count in tool_counter.most_common(10)],
        "best_rabbit_holes": rabbit_holes,
        "top_links": [_to_story_link_payload(story) for story in scored_stories[:12]],
    }
