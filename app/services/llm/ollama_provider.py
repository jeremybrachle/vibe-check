import json

import httpx

from app.config import settings
from app.services.llm.base import LLMProvider


class OllamaProvider(LLMProvider):
    label = "ollama"

    async def generate_text(self, prompt: str) -> str:
        payload = {
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/generate",
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

        text = data.get("response", "").strip()
        if not text:
            raise RuntimeError("Empty response from Ollama")
        return text
