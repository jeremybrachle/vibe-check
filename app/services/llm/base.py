from typing import Protocol


class LLMProvider(Protocol):
    label: str

    async def generate_text(self, prompt: str) -> str:
        ...
