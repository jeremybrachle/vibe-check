from app.services.llm.base import LLMProvider


class NoneProvider(LLMProvider):
    """Explicit opt-out: ai_summary will always be an empty string."""

    label = "none"

    async def generate_text(self, prompt: str) -> str:
        return ""
