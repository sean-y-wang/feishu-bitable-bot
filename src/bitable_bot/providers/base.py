"""LLM provider abstraction (design §6). Sync for MVP; handlers run in a worker pool."""

from __future__ import annotations

from typing import Any, Protocol


class LLMError(Exception):
    pass


class LLMProvider(Protocol):
    name: str
    model: str

    def extract(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        """Return raw model text (expected to be JSON)."""
        ...
