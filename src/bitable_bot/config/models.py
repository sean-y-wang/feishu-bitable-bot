"""Pydantic configuration models (design §5)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

FieldType = Literal["text", "number", "single_select", "multi_select", "date", "checkbox", "user", "url", "phone"]


class Option(BaseModel):
    value: str
    synonyms: list[str] = Field(default_factory=list)


class FieldSpec(BaseModel):
    key: str
    field: str
    type: FieldType
    required: bool = False
    default: Any = None
    description: str | None = None
    options: list[Option] = Field(default_factory=list)
    min: float | None = None
    max: float | None = None
    precision: int | None = None
    max_len: int | None = None

    @field_validator("key")
    @classmethod
    def _key_ident(cls, v: str) -> str:
        if not v.replace("_", "").isalnum():
            raise ValueError(f"field key must be alphanumeric/underscore: {v!r}")
        return v

    @model_validator(mode="after")
    def _options_required(self) -> FieldSpec:
        if self.type in ("single_select", "multi_select") and not self.options:
            raise ValueError(f"field {self.key}: {self.type} requires options")
        return self


class Target(BaseModel):
    app_token: str
    table_id: str


class Role(BaseModel):
    command: str
    aliases: list[str] = Field(default_factory=list)
    name: str
    llm: str | None = None
    allowed_chats: list[str] = Field(default_factory=list)
    allowed_users: list[str] = Field(default_factory=list)
    target: Target
    confirm_before_write: bool = False
    fields: list[FieldSpec]

    @field_validator("command", "aliases")
    @classmethod
    def _slash(cls, v):
        items = v if isinstance(v, list) else [v]
        for c in items:
            if not c.startswith("/") or len(c) < 2 or " " in c:
                raise ValueError(f"command must look like /name: {c!r}")
        return v

    @model_validator(mode="after")
    def _unique_keys(self) -> Role:
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError(f"role {self.command}: duplicate field keys")
        return self

    @property
    def commands(self) -> list[str]:
        return [self.command, *self.aliases]


class ProviderConfig(BaseModel):
    type: Literal["openai_compatible"] = "openai_compatible"
    base_url: str
    api_key: str = ""
    model: str
    supports_json_schema: bool = False
    timeout: float = 30.0
    temperature: float = 0.0


class LLMConfig(BaseModel):
    default: str
    providers: dict[str, ProviderConfig]

    @model_validator(mode="after")
    def _default_exists(self) -> LLMConfig:
        if self.default not in self.providers:
            raise ValueError(f"llm.default {self.default!r} not in providers")
        return self


class SessionConfig(BaseModel):
    timeout_minutes: int = 30
    max_followups: int = 2


class AppConfig(BaseModel):
    version: int = 1
    timezone: str = "Asia/Shanghai"
    llm: LLMConfig
    session: SessionConfig = Field(default_factory=SessionConfig)
    chats_allowlist: list[str] = Field(default_factory=list)
    audit_store_text: bool = True
    roles: list[Role]

    @model_validator(mode="after")
    def _check(self) -> AppConfig:
        seen: set[str] = set()
        for r in self.roles:
            for c in r.commands:
                if c in seen or c in ("/help", "/取消", "/cancel"):
                    raise ValueError(f"duplicate or reserved command: {c}")
                seen.add(c)
            if r.llm and r.llm not in self.llm.providers:
                raise ValueError(f"role {r.command}: unknown llm {r.llm!r}")
        return self

    def find_role(self, command: str) -> Role | None:
        for r in self.roles:
            if command in r.commands:
                return r
        return None
