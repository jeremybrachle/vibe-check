from app.config import settings
from app.services.llm.base import LLMProvider
from app.services.llm.heuristic_provider import HeuristicProvider
from app.services.llm.none_provider import NoneProvider
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openai_provider import OpenAIProvider


def get_llm_provider() -> LLMProvider:
    provider = settings.llm_provider.lower().strip()

    if provider == "none":
        return NoneProvider()
    if provider == "heuristic":
        return HeuristicProvider()
    if provider == "openai":
        return OpenAIProvider()
    if provider == "ollama":
        return OllamaProvider()

    # auto mode: prefer OpenAI when key exists, otherwise try local Ollama, then no summary.
    if settings.openai_api_key.get_secret_value():
        return OpenAIProvider()
    if settings.ollama_base_url and settings.ollama_model:
        return OllamaProvider()
    return NoneProvider()
