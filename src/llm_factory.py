from typing import Optional

from langchain_core.language_models import BaseChatModel

from src.config import settings


def get_llm() -> Optional[BaseChatModel]:
    """Returns an LLM client based on configuration.

    If provider keys are not configured, returns None so the system can continue in
    deterministic (rule-based) mode.
    """

    if settings.llm_provider == "none":
        return None

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            return None
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            temperature=0,
        )

    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            return None
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.gemini_api_key,
            temperature=0,
        )

    raise ValueError(f"Unsupported llm_provider: {settings.llm_provider}")
