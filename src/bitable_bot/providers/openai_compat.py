"""OpenAI-compatible provider: OpenAI, Qwen(DashScope), DeepSeek, Kimi, GLM, vLLM, Ollama, ..."""

from __future__ import annotations

import random
import time
from typing import Any

import structlog

from ..config.models import ProviderConfig
from .base import LLMError

log = structlog.get_logger(__name__)


class OpenAICompatibleProvider:
    def __init__(self, name: str, cfg: ProviderConfig, client: Any = None, max_attempts: int = 3,
                 sleep=time.sleep) -> None:
        self.name = name
        self.model = cfg.model
        self.cfg = cfg
        self.supports_json_schema = cfg.supports_json_schema
        self.max_attempts = max_attempts
        self._sleep = sleep
        if client is None:
            from openai import OpenAI

            client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "EMPTY", timeout=cfg.timeout,
                            max_retries=0)
        self.client = client

    def _formats(self, schema: dict[str, Any]) -> list[dict[str, Any] | None]:
        fmts: list[dict[str, Any] | None] = []
        if self.supports_json_schema:
            fmts.append({"type": "json_schema", "json_schema": {"name": "extraction", "strict": True, "schema": schema}})
        fmts += [{"type": "json_object"}, None]
        return fmts

    def extract(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, BadRequestError

        formats = self._formats(schema)
        fi = 0
        attempt = 0
        while True:
            kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": self.cfg.temperature}
            if formats[fi] is not None:
                kwargs["response_format"] = formats[fi]
            t0 = time.monotonic()
            try:
                resp = self.client.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content or ""
                usage = getattr(resp, "usage", None)
                log.info("llm.ok", provider=self.name, model=self.model, ms=int((time.monotonic() - t0) * 1000),
                         tokens=getattr(usage, "total_tokens", None))
                return content
            except BadRequestError as e:
                # response_format unsupported -> degrade (json_schema -> json_object -> prompt only)
                if fi + 1 < len(formats):
                    log.info("llm.degrade_format", provider=self.name, error=str(e)[:200])
                    fi += 1
                    continue
                raise LLMError(f"{self.name}: {e}") from e
            except (APIConnectionError, APITimeoutError, APIStatusError) as e:
                status = getattr(e, "status_code", None)
                retryable = status is None or status == 429 or status >= 500
                attempt += 1
                if not retryable or attempt >= self.max_attempts:
                    raise LLMError(f"{self.name}: {e}") from e
                delay = min(8.0, 0.5 * 2 ** (attempt - 1)) + random.random() * 0.1
                log.warning("llm.retry", provider=self.name, status=status, delay=round(delay, 2))
                self._sleep(delay)


def build_providers(cfg) -> dict[str, OpenAICompatibleProvider]:
    return {name: OpenAICompatibleProvider(name, p) for name, p in cfg.llm.providers.items()}
