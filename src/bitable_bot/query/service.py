"""Progress-query orchestration: keywords → Bitable search → LLM rerank → reply text."""

from __future__ import annotations

import structlog

from ..config.models import AppConfig, Role
from ..providers.base import LLMProvider
from ..writer.feishu_api import FeishuAPI
from .keywords import extract_keywords
from .search import search_candidates
from .summarize import rerank_and_summarize

log = structlog.get_logger(__name__)


class QueryService:
    def __init__(
        self,
        api: FeishuAPI,
        cfg: AppConfig,
        providers: dict[str, LLMProvider],
        default_provider: str,
        base_url: str = "https://feishu.cn",
    ) -> None:
        self.api = api
        self.cfg = cfg
        self.providers = providers
        self.default = default_provider
        self.base_url = base_url

    def run(self, role: Role, query_text: str, keywords: list[str] | None = None) -> tuple[str, list[str]]:
        """Return (reply_text, hit_record_ids). Never writes a session or a record."""
        if not role.queryable:
            return f"「{role.name}」暂不支持查询进展，请换一个可查询的角色，或直接登记新内容。", []
        kws = extract_keywords(query_text, keywords)
        log.info("query.start", role=role.command, keywords=kws)
        candidates, source = search_candidates(self.api, role, kws)
        if not candidates:
            text = (
                f"在「{role.name}」里没找到和「{query_text.strip() or ' '.join(kws)}」相近的记录。"
                f"你可以换个关键词再问，或直接 {role.command} … 登记新需求。"
            )
            return text, []
        provider = self.providers[role.llm or self.default]
        reply, chosen = rerank_and_summarize(
            provider, role, query_text or " ".join(kws), candidates, base_url=self.base_url
        )
        log.info("query.done", role=role.command, source=source, hits=len(chosen))
        return reply, [c["record_id"] for c in chosen]
