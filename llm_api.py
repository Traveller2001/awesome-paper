"""Lightweight wrapper around the chosen LLM provider."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

try:
    from openai import OpenAI
except ImportError as exc:  # pragma: no cover - surfaces a helpful error at runtime
    raise ImportError(
        "openai package is required for llm_api. Install it with 'pip install openai'."
    ) from exc


class LLMClientError(RuntimeError):
    """Raised when the underlying LLM call fails."""


@dataclass
class LLMSettings:
    api_key: str
    base_url: str
    model: str
    temperature: float = 0.2


def _settings_from_env() -> LLMSettings:
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise LLMClientError("Environment variable LLM_API_KEY is required to call the LLM API.")

    base_url = os.getenv("LLM_API_BASE", "https://api.deepseek.com").strip()
    model = os.getenv("LLM_MODEL", "deepseek-chat").strip()

    temperature_raw = os.getenv("LLM_TEMPERATURE", "0.2").strip()
    try:
        temperature = float(temperature_raw)
    except ValueError:
        temperature = 0.2

    return LLMSettings(api_key=api_key, base_url=base_url, model=model, temperature=temperature)


class LLMClient:
    """Tiny helper around the OpenAI-compatible chat completions endpoint."""

    def __init__(self, settings: Optional[LLMSettings] = None) -> None:
        self.settings = settings or _settings_from_env()
        self._client = OpenAI(api_key=self.settings.api_key, base_url=self.settings.base_url)

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        """Execute a single-turn completion and return the message content."""

        try:
            response = self._client.chat.completions.create(
                model=self.settings.model,
                temperature=self.settings.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=False,
            )
        except Exception as exc:  # pragma: no cover - network errors bubble up
            raise LLMClientError(f"LLM API call failed: {exc}") from exc

        choice = response.choices[0]
        content = choice.message.content or ""
        return content.strip()
