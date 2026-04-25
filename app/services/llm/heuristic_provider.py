import json

from app.services.llm.base import LLMProvider


class HeuristicProvider(LLMProvider):
    """Signal-based provider: builds a summary from structured digest data without LLM calls."""

    label = "heuristic"

    async def generate_text(self, prompt: str) -> str:
        return _build_heuristic_summary(prompt)


def _extract_json_list(text: str, label: str) -> list:
    """Robustly extract a JSON array following `label: [` in text."""
    marker = f"{label}: ["
    idx = text.find(marker)
    if idx == -1:
        return []
    start = idx + len(marker) - 1  # points at '['
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except Exception:
                    return []
    return []


def _build_heuristic_summary(prompt: str) -> str:
    is_preview = "morning analyst" in prompt
    is_summary = "evening analyst" in prompt

    if is_preview:
        themes = _extract_json_list(prompt, "Current early-morning themes")
        excited = _extract_json_list(prompt, "Stories generating excitement")
        skeptical = _extract_json_list(prompt, "Stories generating skepticism")
        tools = _extract_json_list(prompt, "Trending tools")
    elif is_summary:
        themes = _extract_json_list(prompt, "Today's actual themes")
        excited = _extract_json_list(prompt, "What excited the community")
        skeptical = _extract_json_list(prompt, "What generated skepticism/debate")
        tools = _extract_json_list(prompt, "Most-mentioned tools")
    else:
        themes = _extract_json_list(prompt, "Top themes")
        excited = _extract_json_list(prompt, "Excited about")
        skeptical = _extract_json_list(prompt, "Skeptical about")
        tools = _extract_json_list(prompt, "Most mentioned tools")

    parts: list[str] = []

    if is_preview:
        marker = "Last 5:01 PM PT summary:"
        prev_line = ""
        if marker in prompt:
            prev_line = prompt.split(marker, 1)[1].split("\n", 1)[0].strip()

        if not prev_line or prev_line.lower() in {"none", "none yet"}:
            parts.append("Nothing major is new since 5:01.")
        else:
            parts.append("Compared with the last 5:01 summary, the signal mix looks mostly similar with a few topic shifts.")

        p1 = "AI tooling and model-release threads will stay near the top"
        p2 = "at least one high-comment debate will center on reliability, risk, or implementation tradeoffs"
        p3 = "developer workflow tools and infra stacks will keep showing up in top links"

        if themes and themes[0].get("topic"):
            p1 = f"{themes[0].get('topic')} discussion will likely dominate the front page"
        if skeptical and skeptical[0].get("title"):
            p2 = f"debate will likely cluster around \"{skeptical[0].get('title')}\" and adjacent skepticism threads"
        if tools:
            tool_names = [t.get("name", "") for t in tools[:3] if t.get("name")]
            if tool_names:
                p3 = f"tools like {', '.join(tool_names)} will likely remain highly visible in top stories"

        parts.append(f"1. {p1}.")
        parts.append(f"2. {p2}.")
        parts.append(f"3. {p3}.")
        return "\n".join(parts)

    theme_names = [t.get("topic", "") for t in themes[:3] if t.get("topic")]
    if theme_names:
        parts.append(f"Today's top themes on Hacker News: {', '.join(theme_names)}.")

    if excited:
        top = excited[0]
        title = top.get("title", "")
        score = top.get("score", 0)
        if title:
            parts.append(f"The community is most excited about \"{title}\" ({score} pts).")

    if skeptical:
        top = skeptical[0]
        title = top.get("title", "")
        comments = top.get("comments", 0)
        if title:
            parts.append(f"Most debate: \"{title}\" ({comments} comments).")

    tool_names = [t.get("name", "") for t in tools[:3] if t.get("name")]
    if tool_names:
        parts.append(f"Trending tools: {', '.join(tool_names)}.")

    if not parts:
        return "Hacker News digest captured. No strong signals detected in this snapshot."

    return " ".join(parts)
