"""Multi-backend LLM client with sync and async support."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI, OpenAI


class LLMClientError(RuntimeError):
    """Raised when the underlying LLM call fails."""


@dataclass
class LLMSettings:
    api_key: str
    base_url: str
    model: str
    temperature: float = 0.2


class LLMClient:
    """Synchronous LLM client for agent conversations and simple completions."""

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        self._client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
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
        except Exception as exc:
            raise LLMClientError(f"LLM API call failed: {exc}") from exc
        return (response.choices[0].message.content or "").strip()

    def chat(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str | Dict[str, Any]] = None,
    ) -> Any:
        """Multi-turn chat with optional function-calling. Returns full response."""
        try:
            kwargs: Dict[str, Any] = {
                "model": self.settings.model,
                "temperature": self.settings.temperature,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            return self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMClientError(f"LLM API call failed: {exc}") from exc


class AsyncLLMClient:
    """Async LLM client for parallel classification."""

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        self._client = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)

    async def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self.settings.model,
                temperature=self.settings.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=False,
            )
        except Exception as exc:
            raise LLMClientError(f"Async LLM API call failed: {exc}") from exc
        return (response.choices[0].message.content or "").strip()


def build_llm_settings(role_config) -> LLMSettings:
    """Convert a LLMRoleConfig dataclass to LLMSettings."""
    return LLMSettings(
        api_key=role_config.resolve_api_key(),
        base_url=role_config.api_base,
        model=role_config.model,
        temperature=role_config.temperature,
    )
