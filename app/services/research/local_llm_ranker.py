import math
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from urllib.parse import quote_plus
from urllib.robotparser import RobotFileParser

import httpx


USER_AGENT = "vibe-check-research-bot/1.0 (+https://localhost)"
HF_API_URL = "https://huggingface.co/api/models"
HF_BASE = "https://huggingface.co"
CACHE_TTL_MINUTES = 20

# Heuristic allow-list for well-known local-capable families.
LOCAL_FAMILY_HINTS = (
    "qwen",
    "llama",
    "mistral",
    "mixtral",
    "gemma",
    "phi",
    "deepseek",
    "yi",
    "nous",
)

LOCAL_TAG_HINTS = {
    "text-generation",
    "gguf",
    "safetensors",
    "llama",
    "mistral",
    "qwen",
    "gemma",
    "phi",
    "transformers",
}

_cached_payload: dict[str, Any] | None = None
_cached_at: datetime | None = None


class LocalLLMRanker:
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

        async with httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        ) as client:
            await self._assert_allowed_by_robots(client, HF_API_URL)
            models = await self._fetch_hf_candidates(client)

        ranked = self._rank(models)

        payload = {
            "generated_at": now.isoformat(),
            "methodology": [
                "Queried public Hugging Face model metadata API for text-generation models sorted by downloads.",
                "Filtered to local-capable/open model families using public tags and model identifiers.",
                "Computed a qualitative score from popularity (downloads/likes), recency, and local deployment signals (for example gguf/instruct tags).",
                "Generated rationale text from transparent, deterministic heuristics so ranking behavior is inspectable.",
            ],
            "legal_ethics": [
                "Only public, easily discoverable metadata endpoints were used. No authentication, paywalls, or bypass techniques.",
                "robots.txt checks are performed before fetching each source path.",
                "Low request volume with in-process caching reduces load and avoids aggressive crawling behavior.",
                "Output includes source links so users can verify claims and context directly.",
            ],
            "items": ranked,
        }

        _cached_payload = payload
        _cached_at = now
        return payload

    async def _assert_allowed_by_robots(self, client: httpx.AsyncClient, target_url: str) -> None:
        parsed = urlparse(target_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        response = await client.get(robots_url)
        response.raise_for_status()

        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        if not parser.can_fetch(USER_AGENT, parsed.path):
            raise RuntimeError(f"Robots policy disallows access to {target_url}")

    async def _fetch_hf_candidates(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        params = {
            "pipeline_tag": "text-generation",
            "sort": "downloads",
            "direction": -1,
            "limit": 120,
        }
        response = await client.get(HF_API_URL, params=params)
        response.raise_for_status()

        raw = response.json() or []
        candidates: list[dict[str, Any]] = []
        for item in raw:
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue

            tags = {str(tag).lower() for tag in (item.get("tags") or [])}
            low_id = model_id.lower()
            if not self._looks_local_capable(low_id, tags):
                continue

            downloads = int(item.get("downloads") or 0)
            likes = int(item.get("likes") or 0)
            last_modified = str(item.get("lastModified") or "")

            candidates.append(
                {
                    "model_id": model_id,
                    "model_name": model_id.split("/")[-1],
                    "downloads": downloads,
                    "likes": likes,
                    "last_modified": last_modified,
                    "tags": sorted(tags),
                }
            )

        return candidates

    def _looks_local_capable(self, model_id: str, tags: set[str]) -> bool:
        if any(hint in model_id for hint in LOCAL_FAMILY_HINTS):
            return True
        if any(tag in LOCAL_TAG_HINTS for tag in tags):
            return True
        return False

    def _freshness_score(self, iso_value: str) -> float:
        if not iso_value:
            return 0.0
        try:
            dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0

        age_days = max((datetime.now(timezone.utc) - dt).days, 0)
        if age_days <= 30:
            return 2.0
        if age_days <= 90:
            return 1.2
        if age_days <= 180:
            return 0.6
        return 0.0

    def _local_fit_score(self, tags: list[str], model_name: str) -> float:
        low_name = model_name.lower()
        score = 0.0
        if "gguf" in tags:
            score += 1.2
        if "instruct" in low_name:
            score += 0.7
        if any(family in low_name for family in ("qwen", "llama", "mistral", "gemma", "phi")):
            score += 0.8
        return score

    def _rank(self, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        for item in models:
            model_id = item["model_id"]
            model_name = item["model_name"]

            # Keep list diverse by avoiding duplicate short names.
            key = model_name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)

            downloads = int(item["downloads"])
            likes = int(item["likes"])
            pop = (2.2 * math.log10(downloads + 10)) + (0.8 * math.log10(likes + 10))
            fresh = self._freshness_score(item["last_modified"])
            local_fit = self._local_fit_score(item["tags"], model_name)
            qualitative = round(pop + fresh + local_fit, 2)

            reasons: list[str] = []
            if downloads > 0:
                reasons.append(f"strong community traction ({downloads:,} downloads)")
            if likes > 0:
                reasons.append(f"active positive feedback ({likes:,} likes)")
            if local_fit >= 1.5:
                reasons.append("local deployment signals (gguf/instruct/family hints)")
            if fresh >= 1.2:
                reasons.append("recently updated")
            rationale = "; ".join(reasons[:4]).capitalize() + "."

            model_url = f"{HF_BASE}/{model_id}"
            family_query = quote_plus(model_name.split("-")[0])
            ranked.append(
                {
                    "rank": 0,
                    "model_name": model_name,
                    "model_id": model_id,
                    "rationale": rationale,
                    "qualitative_score": qualitative,
                    "signals": {
                        "downloads": downloads,
                        "likes": likes,
                        "freshness_score": round(fresh, 2),
                        "local_fit_score": round(local_fit, 2),
                    },
                    "sources": [
                        {"label": "Hugging Face model card", "url": model_url},
                        {"label": "Hugging Face public metadata API", "url": HF_API_URL},
                        {"label": "Ollama library search", "url": f"https://ollama.com/search?q={family_query}"},
                    ],
                }
            )

            if len(ranked) >= 12:
                break

        ranked.sort(key=lambda x: x["qualitative_score"], reverse=True)
        max_raw = ranked[0]["qualitative_score"] if ranked else 1.0
        for idx, row in enumerate(ranked, start=1):
            row["rank"] = idx
            row["qualitative_score"] = round(row["qualitative_score"] / max_raw * 100)
        return ranked
