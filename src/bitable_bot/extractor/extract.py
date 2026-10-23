"""Call a provider, parse & structurally check the JSON, retry with error feedback (max 2)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from ..config.models import Role
from ..providers.base import LLMError, LLMProvider
from .schema import build_schema

log = structlog.get_logger(__name__)

_CODE_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


class ExtractionError(Exception):
    pass


@dataclass
class Extraction:
    fields: dict[str, Any]
    missing: list[str] = field(default_factory=list)
    confidence: dict[str, Any] = field(default_factory=dict)


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    for candidate in (text, *(m.group(1) for m in _CODE_BLOCK.finditer(text))):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    raise ValueError("模型输出不是合法 JSON 对象")


def check_shape(obj: dict[str, Any], role: Role) -> Extraction:
    fields = obj.get("fields")
    if not isinstance(fields, dict):
        # tolerate flat output {key: value}
        if any(f.key in obj for f in role.fields):
            fields = obj
        else:
            raise ValueError("缺少 fields 对象")
    allowed = {f.key for f in role.fields}
    # whitelist: drop any key not declared by the template
    clean = {k: v for k, v in fields.items() if k in allowed}
    missing = [m for m in obj.get("missing") or [] if m in allowed] if isinstance(obj.get("missing"), list) else []
    conf = obj.get("confidence") if isinstance(obj.get("confidence"), dict) else {}
    return Extraction(clean, missing, conf)


class Extractor:
    def __init__(self, providers: dict[str, LLMProvider], default: str, max_retries: int = 2) -> None:
        self.providers = providers
        self.default = default
        self.max_retries = max_retries

    def extract(self, role: Role, messages: list[dict[str, str]]) -> Extraction:
        provider = self.providers[role.llm or self.default]
        schema = build_schema(role)
        msgs = list(messages)
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = provider.extract(msgs, schema)
            except LLMError as e:
                raise ExtractionError(str(e)) from e
            try:
                return check_shape(parse_json(raw), role)
            except ValueError as e:
                last_err = e
                log.warning("extract.parse_failed", attempt=attempt, error=str(e))
                msgs = msgs + [
                    {"role": "assistant", "content": raw[:2000]},
                    {"role": "user", "content": f"上次输出无效：{e}。请严格只输出符合要求的 JSON 对象。"},
                ]
        raise ExtractionError(f"LLM 输出解析失败：{last_err}")
