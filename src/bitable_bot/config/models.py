"""Pydantic configuration models (design §5 + 增量：角色提示词 / 进展查询)."""

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


class QueryConfig(BaseModel):
    """Progress / similarity lookup against this role's Bitable table."""

    search_fields: list[str] = Field(default_factory=list)  # field keys; defaulted to [title] when queryable
    reply_fields: list[str] = Field(default_factory=list)
    max_candidates: int = Field(20, ge=1, le=50)
    max_reply: int = Field(3, ge=1, le=10)
    fallback_limit: int = Field(30, ge=1, le=100)
    query_prompt: str | None = None


class Role(BaseModel):
    command: str
    aliases: list[str] = Field(default_factory=list)
    name: str
    description: str | None = None  # helps natural-language role detection
    llm: str | None = None
    allowed_chats: list[str] = Field(default_factory=list)
    allowed_users: list[str] = Field(default_factory=list)
    target: Target
    confirm_before_write: bool = False
    # ---- role-level prompts (增量 A) ----
    system_prompt: str | None = None
    guide_prompt: str | None = None
    reply_style: str | None = None
    min_content_chars: int = Field(0, ge=0)  # 0=off; set 8–12 with guide_prompt
    receipt_title: str | None = None
    # ---- progress query (增量 B) ----
    queryable: bool = False
    query: QueryConfig = Field(default_factory=QueryConfig)
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
        if self.queryable:
            if not self.query.search_fields:
                self.query.search_fields = ["title"] if any(f.key == "title" for f in self.fields) else []
            if not self.query.reply_fields:
                prefer = ["title", "status", "priority", "proposer", "due"]
                keys = {f.key for f in self.fields}
                self.query.reply_fields = [k for k in prefer if k in keys] or [f.key for f in self.fields[:5]]
            by_key = {f.key for f in self.fields}
            for k in (*self.query.search_fields, *self.query.reply_fields):
                if k not in by_key:
                    raise ValueError(f"role {self.command}: query field {k!r} not in fields")
            if not self.query.search_fields:
                raise ValueError(f"role {self.command}: queryable requires search_fields")
        return self

    @property
    def commands(self) -> list[str]:
        return [self.command, *self.aliases]

    def field_by_key(self, key: str) -> FieldSpec | None:
        for f in self.fields:
            if f.key == key:
                return f
        return None


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


class IntentConfig(BaseModel):
    """Natural-language role detection when no slash command is given."""

    enabled: bool = True
    min_confidence: float = Field(0.7, ge=0, le=1)
    llm: str | None = None
    # Words in a slash-command body that tip the flow toward progress query.
    query_hints: list[str] = Field(
        default_factory=lambda: [
            "进展",
            "怎样了",
            "怎么样了",
            "上线了吗",
            "上线了没",
            "查一下",
            "查下",
            "查询",
            "有没有做过",
            "有没有提过",
            "找一下",
            "搜一下",
            "状态如何",
            "做到哪了",
        ]
    )


class ContextConfig(BaseModel):
    """Conversation context pulled when the bot is triggered."""

    enabled: bool = True
    recent_messages: int = Field(10, ge=0, le=50)
    include_parent: bool = True
    include_thread: bool = True
    thread_messages: int = Field(20, ge=0, le=50)
    max_chars: int = Field(3000, ge=200)
    name_cache_seconds: int = 600


class SessionConfig(BaseModel):
    timeout_minutes: int = 30
    max_followups: int = 2


class AppConfig(BaseModel):
    version: int = 1
    timezone: str = "Asia/Shanghai"
    llm: LLMConfig
    session: SessionConfig = Field(default_factory=SessionConfig)
    intent: IntentConfig = Field(default_factory=IntentConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    p2p_enabled: bool = True
    ack_after_seconds: float = Field(2.0, ge=0)  # 0 = disable "收到，正在整理…"
    first_guide_enabled: bool = True
    first_guide_text: str = "提示：直接说需求即可，不必打命令。例如「AIHub 货架页加个导出，挺急」。"
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
        if self.intent.llm and self.intent.llm not in self.llm.providers:
            raise ValueError(f"intent.llm {self.intent.llm!r} not in providers")
        return self

    def find_role(self, command: str) -> Role | None:
        for r in self.roles:
            if command in r.commands:
                return r
        return None

    def match_role(self, text: str) -> Role | None:
        """Resolve a user's role choice: command / alias (with or without slash) or role name."""
        t = text.strip().replace("／", "/").strip("「」“”\"'。.！! ")
        if not t:
            return None
        for r in self.roles:
            names = [r.name, *r.commands, *(c[1:] for c in r.commands)]
            if t in names or t.casefold() in (n.casefold() for n in names):
                return r
        hits = [r for r in self.roles if r.name in t or any(c in t for c in r.commands)]
        return hits[0] if len(hits) == 1 else None

    def queryable_roles(self) -> list[Role]:
        return [r for r in self.roles if r.queryable]
