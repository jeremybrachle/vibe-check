from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_TTL_MINUTES = 20

_cached_payload: dict[str, Any] | None = None
_cached_at: datetime | None = None


class CloudLLMRanker:
    async def get_live_ranking(self, force_refresh: bool = False) -> dict[str, Any]:
        global _cached_payload, _cached_at

        now = datetime.now(timezone.utc)
        if (
            not force_refresh
            and _cached_payload is not None
            and _cached_at is not None
            and now - _cached_at < timedelta(minutes=CACHE_TTL_MINUTES)
        ):
            return _cached_payload

        items = await self._fetch_openrouter_items()
        if not items:
            items = self._fallback_items()

        payload = {
            "generated_at": now.isoformat(),
            "methodology": [
                "Queried public cloud-model metadata (OpenRouter model catalog) when available.",
                "Ranked deterministicly by practical capability signals: context length, prompt/completion price, and provider availability.",
                "Used a fallback seed list if live fetch fails so the cloud tab still loads useful starter data.",
            ],
            "legal_ethics": [
                "Only public model metadata endpoints are used.",
                "No bypass techniques, no credential scraping, and no automated interaction with protected resources.",
                "Each ranked item includes source links for verification.",
            ],
            "items": items,
        }

        _cached_payload = payload
        _cached_at = now
        return payload

    async def _fetch_openrouter_items(self) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.get(OPENROUTER_MODELS_URL, headers={"Accept": "application/json"})
                res.raise_for_status()
                data = res.json() or {}
        except Exception:
            return []

        raw_models = data.get("data") or []
        candidates: list[dict[str, Any]] = []

        def _to_float(value: Any) -> float:
            try:
                if value is None or value == "":
                    return 0.0
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        allowed_prefixes = (
            "openai/",
            "anthropic/",
            "google/",
            "xai/",
            "mistralai/",
            "deepseek/",
            "meta-llama/",
            "cohere/",
            "perplexity/",
        )

        for m in raw_models:
            model_id = str(m.get("id") or "").strip()
            if not model_id:
                continue

            lower = model_id.lower()
            if any(tag in lower for tag in ("embedding", "moderation", "tts", "whisper")):
                continue
            if not lower.startswith(allowed_prefixes):
                continue

            name = model_id.split("/")[-1]
            context_len = int(m.get("context_length") or 0)
            pricing = m.get("pricing") or {}
            p_prompt = _to_float(pricing.get("prompt"))
            p_completion = _to_float(pricing.get("completion"))
            top_provider = m.get("top_provider") or {}
            max_completion_tokens = int(top_provider.get("max_completion_tokens") or 0)

            # Skip rows with zero capability signals and no useful pricing.
            if context_len <= 0 and max_completion_tokens <= 0 and p_prompt <= 0 and p_completion <= 0:
                continue

            score = 50.0
            if context_len > 0:
                score += min(context_len / 32000.0, 2.5) * 15
            if max_completion_tokens > 0:
                score += min(max_completion_tokens / 16000.0, 1.5) * 10
            if p_prompt > 0:
                score += max(0.0, 15 - (p_prompt * 1_000_000 * 0.9))
            if p_completion > 0:
                score += max(0.0, 15 - (p_completion * 1_000_000 * 0.7))

            rationale = (
                f"Context {context_len:,} tokens; completion limit {max_completion_tokens:,}; "
                f"public price signals prompt={p_prompt:g}, completion={p_completion:g}."
            )

            candidates.append(
                {
                    "rank": 0,
                    "model_name": name,
                    "model_id": model_id,
                    "rationale": rationale,
                    "qualitative_score": round(score, 2),
                    "signals": {
                        "context_length": context_len,
                        "max_completion_tokens": max_completion_tokens,
                        "prompt_price_per_token": p_prompt,
                        "completion_price_per_token": p_completion,
                    },
                    "sources": [
                        {"label": "OpenRouter model catalog", "url": OPENROUTER_MODELS_URL},
                        {"label": "OpenRouter model page", "url": f"https://openrouter.ai/models/{model_id}"},
                    ],
                }
            )

        if not candidates:
            return []

        candidates.sort(key=lambda x: x["qualitative_score"], reverse=True)
        top = candidates[:12]
        max_score = top[0]["qualitative_score"] if top else 1.0
        for idx, item in enumerate(top, start=1):
            item["rank"] = idx
            item["qualitative_score"] = round((item["qualitative_score"] / max_score) * 100)
        return top

    def _fallback_items(self) -> list[dict[str, Any]]:
        seeds = [
            ("OpenAI GPT-4.1", "openai/gpt-4.1", 98, "Strong coding/reasoning baseline with broad tooling support."),
            ("Anthropic Claude 3.7 Sonnet", "anthropic/claude-3.7-sonnet", 96, "High-quality long-form reasoning and strong instruction following."),
            ("Google Gemini 2.5 Pro", "google/gemini-2.5-pro", 95, "Large context and strong multimodal coverage in many workflows."),
            ("xAI Grok 3", "xai/grok-3", 90, "Fast iteration speed and strong conversational style for exploratory tasks."),
            ("Mistral Large", "mistralai/mistral-large", 88, "Enterprise-friendly cloud option with capable general reasoning."),
        ]

        rows: list[dict[str, Any]] = []
        for idx, (name, model_id, score, rationale) in enumerate(seeds, start=1):
            rows.append(
                {
                    "rank": idx,
                    "model_name": name,
                    "model_id": model_id,
                    "rationale": rationale,
                    "qualitative_score": score,
                    "signals": {
                        "seeded": True,
                        "note": "Fallback starter list (live provider metadata unavailable)",
                    },
                    "sources": [
                        {"label": "OpenRouter model catalog", "url": OPENROUTER_MODELS_URL},
                    ],
                }
            )
        return rows
