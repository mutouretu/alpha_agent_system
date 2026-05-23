from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

from dotenv import load_dotenv
from openai import OpenAI


DEFAULT_MODEL = "gpt-5.5"


class LLMClient:
    """Small wrapper around the OpenAI Responses API."""

    def __init__(self, model: str | None = None) -> None:
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Configure it in the environment or .env.")

        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        self.client = OpenAI(api_key=api_key)

    def complete(self, messages: Sequence[Mapping[str, Any]]) -> str:
        response = self.client.responses.create(
            model=self.model,
            input=list(messages),
        )
        return response.output_text
