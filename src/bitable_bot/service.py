"""Business pipeline.

trigger (@bot in group / any p2p message / reply in an open follow-up thread)
→ route (slash command = explicit role; otherwise LLM intent detection)
→ collect context (recent chat, quoted message, thread) → extract → validate
→ follow-up / write → reply → audit.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from .config.models import AppConfig, FieldSpec, Role
from .context import ContextCollector, ConversationContext
from .extractor import ExtractionError, Extractor, build_messages, classify
from .router import IncomingMessage, Kind, bot_mentioned, clean_text, extract_text, help_text, route
from .session import Session, Store
from .validator import Context, validate
from .writer import BitableWriter, FeishuAPI, FeishuAPIError, WriteError

log = structlog.get_logger(__name__)

PICK = "__pick__"  # pseudo-role of a "please confirm which role" session
YES = {"是", "对", "确认", "好", "好的", "嗯", "y", "yes", "ok", "是的", "对的", "没错"}
_HIDDEN_IN_RECEIPT = {"$context", "$raw_text"}


def _fmt(spec: FieldSpec, v: Any, people: dict[str, str], tz: str) -> str:
    if spec.type == "date":
        if isinstance(v, int):
            return datetime.fromtimestamp(v / 1000, UTC).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")
        return str(v)
    if spec.type == "user":
        rev = {oid: n for n, oid in people.items()}
        return "、".join(rev.get(u["id"], u["id"]) for u in v)
    if spec.type == "multi_select":
        return "、".join(v)
    if spec.type == "url":
        return v["link"]
    if spec.type == "checkbox":
        return "是" if v else "否"
    s = " ".join(str(v).split())
    return s if len(s) <= 80 else s[:77] + "..."


class BotService:
    def __init__(self, cfg: AppConfig, api: FeishuAPI, store: Store, extractor: Extractor,
                 writer: BitableWriter, bot_open_id: str | None = None, clock=time.time,
                 collector: ContextCollector | None = None) -> None:
        self.cfg = cfg
        self.api = api
        self.store = store
        self.extractor = extractor
        self.writer = writer
        self.bot_open_id = bot_open_id
        self.clock = clock
        self.collector = collector or ContextCollector(api, cfg, bot_open_id=bot_open_id, clock=clock)

    # ------------------------------------------------------------------ entry
    def is_duplicate(self, msg: IncomingMessage) -> bool:
        """event_id first, message_id second (event_id may change on redelivery). TTL 24h."""
        dup_event = bool(msg.event_id) and self.store.seen(f"evt:{msg.event_id}")
        dup_msg = self.store.seen(f"msg:{msg.message_id}")
        return dup_event or dup_msg

    def handle(self, msg: IncomingMessage) -> None:
        try:
            self._handle(msg)
        except Exception:  # never let one message kill the worker
            log.exception("handle.failed", message_id=msg.message_id)
            self._reply(msg.message_id, "处理消息时出现内部错误，请稍后重试。")

    def _thread_session(self, msg: IncomingMessage, sessions: list[Session]) -> Session | None:
        for s in sessions:
            if (msg.root_id and msg.root_id == s.source_message_id) or (
                msg.parent_id and (msg.parent_id in s.ask_message_ids or msg.parent_id == s.source_message_id)
            ):
                return s
        return None

    def _handle(self, msg: IncomingMessage) -> None:
        if self.is_duplicate(msg):
            log.info("dedup.skip", event_id=msg.event_id, message_id=msg.message_id)
            return
        p2p = msg.chat_type == "p2p"
        if p2p and not self.cfg.p2p_enabled:
            return
        raw = extract_text(msg.message_type, msg.content)
        sessions = self.store.sessions_for(msg.chat_id, msg.sender_open_id)
        thread_s = self._thread_session(msg, sessions)
        addressed = p2p or bot_mentioned(msg.mentions, raw, self.bot_open_id)
        if not addressed and thread_s is None:
            return  # ordinary group chatter (received via im:message.group_msg): ignore silently
        if msg.message_type not in ("text", "post"):
            self._reply(msg.message_id, "暂只支持文字消息。发送 /help 查看用法。")
            return
        if self.cfg.chats_allowlist and not p2p and msg.chat_id not in self.cfg.chats_allowlist:
            self._reply(msg.message_id, "抱歉，本群未开通记录功能，请联系管理员。")
            return
        text, people = clean_text(raw, msg.mentions, self.bot_open_id, strip_leading=not p2p)
        r = route(text, self.cfg)
        if r.kind is Kind.HELP:
            self._reply(msg.message_id, help_text(self.cfg, msg.chat_id))
        elif r.kind is Kind.CANCEL:
            for s in sessions:
                self.store.delete_session(s)
            self._reply(msg.message_id, "已取消未完成的记录。" if sessions else "当前没有未完成的记录。")
        elif r.kind is Kind.UNKNOWN:
            self._reply(msg.message_id, f"未知命令 {r.command}。\n" + help_text(self.cfg, msg.chat_id))
        elif r.kind is Kind.ROLE:
            assert r.role is not None
            self._start(msg, r.role, r.body, people, raw_text=text)
        else:
            s = thread_s or (sessions[0] if sessions else None)
            if s is None:
                self._detect(msg, text, people)
            elif s.role == PICK:
                self._continue_pick(msg, s, text, people)
            else:
                self._continue_followup(msg, s, text, people)

    # ------------------------------------------------------------------ intent
    def _permitted(self, msg: IncomingMessage, role: Role) -> bool:
        if role.allowed_chats and msg.chat_id not in role.allowed_chats:
            return False
        return not role.allowed_users or msg.sender_open_id in role.allowed_users

    def _detect(self, msg: IncomingMessage, text: str, people: dict[str, str]) -> None:
        if not self.cfg.intent.enabled:
            self._reply(msg.message_id, "请使用命令开头，例如：\n" + help_text(self.cfg, msg.chat_id))
            return
        roles = [r for r in self.cfg.roles if self._permitted(msg, r)]
        if not roles:
            self._reply(msg.message_id, "抱歉，你在这里没有可用的记录角色。")
            return
        conv = self.collector.collect(msg)
        if not text and not conv.excerpt:
            self._reply(msg.message_id, "想记录什么？直接说内容即可，例如：\n" + help_text(self.cfg, msg.chat_id))
            return
        provider = self.extractor.providers[self.cfg.intent.llm or self.extractor.default]
        intent = classify(provider, self.cfg, roles, text, conv.excerpt)
        log.info("intent.result", role=intent.role.command if intent.role else None,
                 confidence=intent.confidence, reason=intent.reason)
        if intent.role is not None and intent.confidence >= self.cfg.intent.min_confidence:
            self._start(msg, intent.role, text, people, raw_text=text, conv=conv)
            return
        s = Session(chat_id=msg.chat_id, user_id=msg.sender_open_id, role=PICK, source_message_id=msg.message_id,
                    text=text, raw_fields={"__ctx__": conv.to_dict(), "__people__": people, "__raw__": text,
                                           "__candidate__": intent.role.command if intent.role else ""},
                    expires_at=self.clock() + self.cfg.session.timeout_minutes * 60)
        self._ask_pick(msg.message_id, s, roles, intent.role)

    def _ask_pick(self, reply_to: str, s: Session, roles: list[Role], guess: Role | None, retry: bool = False) -> None:
        head = "没识别出你选的角色。" if retry else (
            f"我猜你想记录到「{guess.name}」，对吗？" if guess else "我不确定要记录到哪张表。")
        opts = "\n".join(f"• {r.name}（{r.command}）" for r in roles)
        tail = "回复“是”确认，或回复角色名/命令；/取消 放弃。" if guess else "请回复角色名或命令；/取消 放弃。"
        ask_id = self._reply(reply_to, f"{head}\n可选：\n{opts}\n{tail}", thread=True)
        if ask_id:
            s.ask_message_ids.append(ask_id)
        self.store.save_session(s)

    def _continue_pick(self, msg: IncomingMessage, s: Session, text: str, people: dict[str, str]) -> None:
        role = self.cfg.match_role(text)
        cand = self.cfg.find_role(s.raw_fields.get("__candidate__") or "")
        if role is None and cand is not None and text.strip().strip("。.!！~").lower() in YES:
            role = cand
        roles = [r for r in self.cfg.roles if self._permitted(msg, r)]
        if role is None or role not in roles:
            s.followups.append(text)
            if len(s.followups) > self.cfg.session.max_followups:
                self.store.delete_session(s)
                self._reply(s.source_message_id, "未能确定角色，已放弃本次记录。可用 /help 查看命令后重新发送。", thread=True)
                return
            self._ask_pick(s.source_message_id, s, roles, cand, retry=True)
            return
        self.store.delete_session(s)
        conv = ConversationContext.from_dict(s.raw_fields.get("__ctx__"))
        self._start(msg, role, s.text, {**s.raw_fields.get("__people__", {}), **people},
                    raw_text=s.raw_fields.get("__raw__", s.text), conv=conv, source_id=s.source_message_id)

    # ------------------------------------------------------------------ extraction flow
    def _start(self, msg: IncomingMessage, role: Role, body: str, people: dict[str, str], *, raw_text: str,
               conv: ConversationContext | None = None, source_id: str | None = None) -> None:
        source_id = source_id or msg.message_id
        if not self._permitted(msg, role):
            self._reply(source_id, f"抱歉，你在这里没有使用「{role.name}」的权限。")
            return
        existing = self.store.get_record(source_id)
        if existing:
            self._reply(source_id, f"这条消息已记录过：{existing[1]}")
            return
        if conv is None:
            conv = self.collector.collect(msg)
        if not body and not conv.excerpt:
            req = "、".join(f.field for f in role.fields if f.required)
            self._reply(source_id, f"请在命令后附上内容，例如：{role.command} ……（需要包含：{req}）")
            return
        for old in self.store.sessions_for(msg.chat_id, msg.sender_open_id):
            if old.role in (role.command, PICK):
                self.store.delete_session(old)  # one open session per (chat,user,role)
        s = Session(chat_id=msg.chat_id, user_id=msg.sender_open_id, role=role.command,
                    source_message_id=source_id, text=body,
                    raw_fields={"__ctx__": conv.to_dict(), "__people__": people, "__raw__": raw_text})
        self._process(msg, role, s, people)

    def _continue_followup(self, msg: IncomingMessage, s: Session, text: str, people: dict[str, str]) -> None:
        role = self.cfg.find_role(s.role)
        if role is None:  # config changed
            self.store.delete_session(s)
            return
        if not text:
            self._reply(msg.message_id, "请直接回复需要补充的内容。", thread=True)
            return
        s.followups.append(text)
        self._process(msg, role, s, people)

    def _context(self, msg: IncomingMessage, all_people: dict[str, str], conv: ConversationContext,
                 raw_text: str) -> Context:
        def lookup(name: str) -> list[str]:
            if msg.chat_type == "p2p":
                return []
            return [oid for oid, n in self.collector.chat_members(msg.chat_id).items() if n == name]

        return Context(sender_open_id=msg.sender_open_id, now=datetime.fromtimestamp(self.clock(), UTC),
                       tz=self.cfg.timezone, sender_name=conv.sender_name, chat_name=conv.chat_name,
                       message_link=conv.message_link, raw_text=raw_text, context_text=conv.excerpt,
                       mentions=all_people, member_lookup=lookup)

    def _process(self, msg: IncomingMessage, role: Role, s: Session, people: dict[str, str]) -> None:
        reply_to = s.source_message_id
        all_people = {**s.raw_fields.get("__people__", {}), **people}
        conv = ConversationContext.from_dict(s.raw_fields.get("__ctx__"))
        raw_text = s.raw_fields.get("__raw__", s.text)
        ctx = self._context(msg, all_people, conv, raw_text)
        provider = self.extractor.providers[role.llm or self.extractor.default]
        prev = {k: v for k, v in s.raw_fields.items() if not k.startswith("__")}
        messages = build_messages(role, s.text, now=ctx.now, tz=ctx.tz, sender_name=conv.sender_name,
                                  mentions=all_people, previous=prev or None, followups=s.followups,
                                  context=conv.excerpt)
        t0 = time.monotonic()
        audit = dict(chat_id=msg.chat_id, sender=msg.sender_open_id, command=role.command,  # noqa: C408
                     text=raw_text if self.cfg.audit_store_text else None,
                     provider=getattr(provider, "name", None), model=getattr(provider, "model", None))
        try:
            ext = self.extractor.extract(role, messages)
        except ExtractionError as e:
            log.error("extract.failed", error=str(e))
            self.store.audit(**audit, latency_ms=int((time.monotonic() - t0) * 1000), result=f"llm_error:{e}"[:200])
            if s.followups:  # keep session so the user can retry by replying again
                s.followups.pop()
            self._reply(reply_to, "AI 字段提取暂时失败，请稍后在此话题中回复重试，或 /取消。", thread=True)
            return
        audit["latency_ms"] = int((time.monotonic() - t0) * 1000)
        merged = dict(prev)
        merged.update({k: v for k, v in ext.fields.items() if v not in (None, "", [])})
        result = validate(role.fields, merged, ctx)

        if result.complete:
            try:
                wr = self.writer.write(role, result.values, s.source_message_id)
            except WriteError as e:
                self.store.delete_session(s)
                self.store.audit(**audit, fields=result.values, result=f"write_error:{e.code}")
                self._reply(reply_to, e.user_message)
                return
            self.store.delete_session(s)
            self.store.audit(**audit, fields=result.values, record_id=wr.record_id,
                             result="duplicate" if wr.duplicated else "ok")
            lines = [f"✅ 已记录到「{role.name}」"]
            for spec in role.fields:
                if spec.key in result.values and spec.default not in _HIDDEN_IN_RECEIPT:
                    lines.append(f"{spec.field}：{_fmt(spec, result.values[spec.key], all_people, ctx.tz)}")
            lines.append(wr.url)
            self._reply(reply_to, "\n".join(lines), thread=bool(s.followups))
            return

        if len(s.followups) >= self.cfg.session.max_followups:
            self.store.delete_session(s)
            self.store.audit(**audit, fields=result.values, result="abandoned:max_followups")
            names = "、".join(f.field for f in role.fields if f.key in result.missing)
            self._reply(reply_to, f"仍缺少必填字段：{names}，已放弃本次记录，未写入。请补全后重新发送 {role.command}。",
                        thread=True)
            return

        s.raw_fields = {**merged, "__people__": all_people, "__ctx__": conv.to_dict(), "__raw__": raw_text}
        s.missing = list(result.missing)
        s.expires_at = self.clock() + self.cfg.session.timeout_minutes * 60
        lines = [f"还需要补充以下信息才能记录到「{role.name}」（直接在此话题回复即可，/取消 可放弃）："]
        for spec in role.fields:
            if spec.key in result.missing:
                hint = result.missing[spec.key] or spec.description or ""
                lines.append(f"• {spec.field}" + (f"（{hint}）" if hint else ""))
        ask_id = self._reply(reply_to, "\n".join(lines), thread=True)
        if ask_id:
            s.ask_message_ids.append(ask_id)
        self.store.save_session(s)
        self.store.audit(**audit, fields=result.values, result="followup")

    # ------------------------------------------------------------------ misc
    def expire_sessions(self) -> int:
        expired = self.store.pop_expired_sessions()
        for s in expired:
            role = self.cfg.find_role(s.role)
            name = role.name if role else "记录"
            self._reply(s.source_message_id, f"「{name}」补充信息超时，已放弃本次记录，未写入。", thread=True)
        return len(expired)

    def _reply(self, message_id: str, text: str, thread: bool = False) -> str | None:
        try:
            return self.api.reply_text(message_id, text, in_thread=thread)
        except FeishuAPIError as e:
            log.error("reply.failed", code=e.code, msg=e.msg)
            return None
