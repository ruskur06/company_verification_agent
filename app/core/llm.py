"""LLM client abstraction.

Supports:
- OpenAI-compatible APIs (real mode)
- Mock provider (demo mode, no API key required)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LLMResponse:
    """Normalized response from any LLM provider."""

    def __init__(self, content: str, model: str, is_mock: bool = False):
        self.content = content
        self.model = model
        self.is_mock = is_mock


class BaseLLMProvider(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str) -> LLMResponse:
        """Send a system + user message and return a response."""


class OpenAIProvider(BaseLLMProvider):
    """OpenAI-compatible provider (works with OpenAI, Azure, Groq, Ollama, etc.)."""

    def __init__(self):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError("openai package is required. Run: pip install openai")

        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
        self._model = settings.llm_model

    async def complete(self, system: str, user: str) -> LLMResponse:
        logger.info(f"Calling LLM model={self._model}")
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content or ""
        return LLMResponse(content=content, model=self._model, is_mock=False)


class MockLLMProvider(BaseLLMProvider):
    """Demo provider that returns realistic-looking but clearly marked mock responses."""

    async def complete(self, system: str, user: str) -> LLMResponse:
        logger.warning("Using MOCK LLM provider. Set OPENAI_API_KEY for real analysis.")

        # Extract company name from user prompt for personalized mock
        company_hint = "the company"
        if "Company name:" in user:
            line = [l for l in user.splitlines() if "Company name:" in l]
            if line:
                company_hint = line[0].replace("Company name:", "").strip()

        mock_content = f"""[MOCK ANALYSIS — Demo mode, no real LLM called]

**Short description:** {company_hint} is a company whose details could not be independently verified in this demo run. No real web search or AI analysis was performed.

**Overall assessment:** This report was generated in demo mode without a real LLM or web search API. The preliminary risk score reflects only the DNS and structural signals gathered by deterministic tools. All findings below should be treated as illustrative placeholders. A real analysis requires a valid OPENAI_API_KEY and optionally a WEB_SEARCH_API_KEY.

**Confidence:** low

**Unknowns:**
- Company registration details not verified (mock mode)
- No real web sources were retrieved
- Business history and reputation unknown
- Legal and regulatory status unknown

**Manual verification checklist:**
- Search official company registry for {company_hint}
- Confirm company address and physical presence
- Verify domain ownership via WHOIS
- Check for legal disputes or court records
- Screen against sanctions and watchlists
- Review customer complaints and reviews
- Validate contact information
"""
        return LLMResponse(content=mock_content, model="mock", is_mock=True)


def get_llm_provider() -> BaseLLMProvider:
    """Return the appropriate LLM provider based on configuration."""
    if settings.use_mock_llm:
        logger.info("LLM provider: Mock (OPENAI_API_KEY not set)")
        return MockLLMProvider()
    logger.info(f"LLM provider: OpenAI-compatible ({settings.openai_base_url})")
    return OpenAIProvider()