from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

from dotenv import load_dotenv
from openai import OpenAI


DEFAULT_PROVIDER = "openai"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class LLMClient:
    """Small multi-provider wrapper with a stable complete() interface."""

    def __init__(self, model: str | None = None) -> None:
        load_dotenv()
        self.provider = os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER).strip().lower()

        if self.provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set. Configure it in the environment or .env.")
            self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
            self.client = OpenAI(api_key=api_key)
            return

        if self.provider == "deepseek":
            api_key = os.getenv("DEEPSEEK_API_KEY")
            if not api_key:
                raise RuntimeError("DEEPSEEK_API_KEY is not set. Configure it in the environment or .env.")
            self.model = model or os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
            self.client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
            return

        raise RuntimeError("LLM_PROVIDER must be one of: openai, deepseek.")

    def complete(self, messages: Sequence[Mapping[str, Any]]) -> str:
        if self.provider == "deepseek":
            return self._complete_deepseek(messages)
        return self._complete_responses(messages)

    def _complete_responses(self, messages: Sequence[Mapping[str, Any]]) -> str:
        response = self.client.responses.create(
            model=self.model,
            input=list(messages),
        )
        return response.output_text

    def _complete_deepseek(self, messages: Sequence[Mapping[str, Any]]) -> str:
        try:
            return self._complete_responses(messages)
        except Exception:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self._to_chat_messages(messages),
            )
            content = response.choices[0].message.content
            return content or ""

    @staticmethod
    def _to_chat_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
        chat_messages: list[dict[str, str]] = []
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            chat_messages.append({"role": role, "content": content})
        return chat_messages
