"""
llm_client.py — Provider-agnostic LLM client
==============================================

Wraps both **Gemini** and **OpenAI (ChatGPT)** behind a common async
interface so ``evaluator.py`` never touches SDK-specific code.

Supported providers
-------------------
- ``gemini``  — uses ``google-genai`` SDK, supports thinking mode
- ``openai``  — uses ``openai`` SDK, no thinking mode (thinking = None)

Provider selection
------------------
Set the environment variable for whichever provider you want to use:

- ``GEMINI_API_KEY``  → selects Gemini
- ``OPENAI_API_KEY``  → selects OpenAI

If both are set, ``GEMINI_API_KEY`` takes precedence.  Override with
``LLM_PROVIDER=openai`` or ``LLM_PROVIDER=gemini`` to force a choice.

Exports
-------
- ``get_provider_name() -> str``
- ``call_rubric_generation(system_prompt, user_prompt) -> str``
- ``call_evaluation(system_prompt, user_prompt) -> (thinking, output)``
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


# ──────────────────────────────────────────────────────────────────────
# Configuration defaults (can be overridden per-provider)
# ──────────────────────────────────────────────────────────────────────

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
GEMINI_THINKING_BUDGET = 10_000


# ──────────────────────────────────────────────────────────────────────
# Abstract base class
# ──────────────────────────────────────────────────────────────────────

class _LLMProvider(ABC):
    """Common interface that every provider must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'gemini', 'openai')."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The exact model string being used (e.g. 'gemini-2.5-flash')."""

    @abstractmethod
    async def call_rubric_generation(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """
        Call 1 — rubric generation (no thinking mode).

        Returns the raw output text from the model.
        """

    @abstractmethod
    async def call_evaluation(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str | None, str]:
        """
        Call 2 — evaluation + hints.

        Returns ``(thinking_text, output_text)``.
        ``thinking_text`` is ``None`` if the provider does not support
        thinking mode.
        """


# ──────────────────────────────────────────────────────────────────────
# Gemini provider
# ──────────────────────────────────────────────────────────────────────

class _GeminiProvider(_LLMProvider):
    """Google Gemini via the ``google-genai`` SDK."""

    def __init__(self, api_key: str) -> None:
        from google import genai  # lazy import
        self._client = genai.Client(api_key=api_key)

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return GEMINI_MODEL

    async def call_rubric_generation(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        from google.genai import types

        response = await self._client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
            ),
        )
        return response.text

    async def call_evaluation(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str | None, str]:
        from google.genai import types

        response = await self._client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                thinking_config=types.ThinkingConfig(
                    thinking_budget=GEMINI_THINKING_BUDGET,
                    include_thoughts=True,
                ),
            ),
        )

        # Separate thinking tokens from output text
        thinking_parts: list[str] = []
        output_parts: list[str] = []

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if getattr(part, "thought", False):
                    thinking_parts.append(part.text)
                else:
                    output_parts.append(part.text)

        thinking_text = "\n".join(thinking_parts) if thinking_parts else None
        output_text = "\n".join(output_parts)
        return thinking_text, output_text


# ──────────────────────────────────────────────────────────────────────
# OpenAI provider
# ──────────────────────────────────────────────────────────────────────

class _OpenAIProvider(_LLMProvider):
    """OpenAI ChatGPT via the ``openai`` SDK."""

    def __init__(self, api_key: str) -> None:
        from openai import AsyncOpenAI  # lazy import
        self._client = AsyncOpenAI(api_key=api_key)

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return OPENAI_MODEL

    async def call_rubric_generation(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        response = await self._client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content

    async def call_evaluation(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str | None, str]:
        # OpenAI does not expose thinking tokens, so thinking = None
        response = await self._client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        output_text = response.choices[0].message.content
        return None, output_text


# ──────────────────────────────────────────────────────────────────────
# Provider resolution — pick the right provider based on env vars
# ──────────────────────────────────────────────────────────────────────

_cached_provider: _LLMProvider | None = None


def _resolve_provider() -> _LLMProvider:
    """
    Determine which provider to use based on environment variables.

    Priority:
    1. ``LLM_PROVIDER`` env var (explicit override: 'gemini' or 'openai')
    2. Whichever API key is set (``GEMINI_API_KEY`` first, then ``OPENAI_API_KEY``)

    Raises ``EnvironmentError`` if no API key is found.
    """
    global _cached_provider
    if _cached_provider is not None:
        return _cached_provider

    forced = os.environ.get("LLM_PROVIDER", "").strip().lower()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if forced == "gemini":
        if not gemini_key:
            raise EnvironmentError(
                "LLM_PROVIDER is set to 'gemini' but GEMINI_API_KEY "
                "is not set."
            )
        _cached_provider = _GeminiProvider(gemini_key)

    elif forced == "openai":
        if not openai_key:
            raise EnvironmentError(
                "LLM_PROVIDER is set to 'openai' but OPENAI_API_KEY "
                "is not set."
            )
        _cached_provider = _OpenAIProvider(openai_key)

    elif gemini_key:
        _cached_provider = _GeminiProvider(gemini_key)

    elif openai_key:
        _cached_provider = _OpenAIProvider(openai_key)

    else:
        raise EnvironmentError(
            "No LLM API key found. Set one of:\n"
            "  - GEMINI_API_KEY  (for Google Gemini)\n"
            "  - OPENAI_API_KEY  (for OpenAI ChatGPT)\n"
            "Or set LLM_PROVIDER to force a provider."
        )

    return _cached_provider


# ──────────────────────────────────────────────────────────────────────
# Public API — these are what evaluator.py calls
# ──────────────────────────────────────────────────────────────────────

def get_provider_name() -> str:
    """Return the name of the active LLM provider ('gemini' or 'openai')."""
    return _resolve_provider().name


def get_model_name() -> str:
    """Return the configured model string for the active provider."""
    return _resolve_provider().model_name


async def call_rubric_generation(
    system_prompt: str,
    user_prompt: str,
) -> str:
    """
    Call 1 — Generate a rubric (no thinking mode).

    Returns the raw output text from the model.
    """
    provider = _resolve_provider()
    return await provider.call_rubric_generation(system_prompt, user_prompt)


async def call_evaluation(
    system_prompt: str,
    user_prompt: str,
) -> tuple[str | None, str]:
    """
    Call 2 — Evaluate a submission (with thinking mode if supported).

    Returns ``(thinking_text, output_text)``.
    ``thinking_text`` is ``None`` for providers that do not support it.
    """
    provider = _resolve_provider()
    return await provider.call_evaluation(system_prompt, user_prompt)
