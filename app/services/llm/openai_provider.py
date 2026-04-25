import json

import httpx

from app.config import settings
from app.services.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    label = "openai"

    async def generate_text(self, prompt: str) -> str:
        api_key = settings.openai_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.openai_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You summarize tech-news digests for product builders.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                content=json.dumps(payload),
            )
            response.raise_for_status()
            data = response.json()

        try:
            return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("Invalid OpenAI response format") from exc
