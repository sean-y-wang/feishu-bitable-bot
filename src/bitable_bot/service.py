"""Business pipeline: route → session → extract → validate → follow-up / write → reply → audit."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from .config.models import AppConfig, FieldSpec, Role
from .extractor import ExtractionError, Extractor, build_messages
from .router import IncomingMessage, Kind, clean_text, extract_text, help_text, route
from .session import Session, Store
from .validator import Context, validate
from .writer import BitableWriter, FeishuAPI, FeishuAPIError, WriteError

log = structlog.get_logger(__name__)


def _fmt(spec: FieldSpec, v: Any, people: dict[str, str], tz: str) -> str:
    if spec.type == "date":
        return datetime.fromtimestamp(v / 1000, UTC).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d") if isinstance(v, int) else str(v)
    if spec.type == "user":
        rev = {oid: n for n, oid in people.items()}
        return "、".join(rev.get(u["id"], u["id"]) for u in v)
    if spec.type == "multi_select":
        return "、".join(v)
    if spec.type == "url":
        return v["link"]
    if spec.type == "checkbox":
        return "是" if v else "否"
    s = str(v)
    return s if len(s) <= 80 else s[:77] + "..."


class BotService:
    def __init__(self, cfg: AppConfig, api: FeishuAPI, store: Store, extractor: Extractor,
                 writer: BitableWriter, bot_open_id: str | None = None, clock=time.time) -> None:
        self.cfg = cfg
        self.api = api
        self.store = store
        self.extractor = extractor
        self.writer = writer
        self.bot_open_id = bot_open_id
        self.clock = clock

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

    def _handle(self, msg: IncomingMessage) -> None:
        if self.is_duplicate(msg):
            log.info("dedup.skip", event_id=msg.event_id, message_id=msg.message_id)
            return
        if msg.message_type not in ("text", "post"):
            self._reply(msg.message_id, "暂只支持文字消息。发送 /help 查看用法。")
            return
        if self.cfg.chats_allowlist and msg.chat_type != "p2p" and msg.chat_id not in self.cfg.chats_allowlist:
            self._reply(msg.message_id, "抱歉，本群未开通记录功能，请联系管理员。")
            return
        text, people = clean_text(extract_text(msg.message_type, msg.content), msg.mentions, self.bot_open_id)
        r = route(text, self.cfg)
        if r.kind is Kind.HELP:
            self._reply(msg.message_id, help_text(self.cfg, msg.chat_id))
        elif r.kind is Kind.CANCEL:
            sessions = self.store.sessions_for(msg.chat_id, msg.sender_open_id)
            for s in sessions:
                self.store.delete_session(s)
            self._reply(msg.message_id, "已取消未完成的记录。" if sessions else "当前没有未完成的记录。")
        elif r.kind is Kind.UNKNOWN:
            self._reply(msg.message_id, f"未知命令 {r.command}。\n" + help_text(self.cfg, msg.chat_id))
        elif r.kind is Kind.ROLE:
            assert r.role is not None
            self._start(msg, r.role, r.body, people)
        else:
            s = self._find_session(msg)
            if s is None:
                self._reply(msg.message_id, "请使用命令开头，例如：\n" + help_text(self.cfg, msg.chat_id))
                return
            role = self.cfg.find_role(s.role)
            if role is None:  # config changed
                self.store.delete_session(s)
                return
            if not text:
                self._reply(msg.message_id, "请直接回复需要补充的内容。", thread=True)
                return
            s.followups.append(text)
            self._process(msg, role, s, people)

    # ------------------------------------------------------------------ flow
    def _permitted(self, msg: IncomingMessage, role: Role) -> bool:
        if role.allowed_chats and msg.chat_id not in role.allowed_chats:
            return False
        return not role.allowed_users or msg.sender_open_id in role.allowed_users

    def _start(self, msg: IncomingMessage, role: Role, body: str, people: dict[str, str]) -> None:
        if not self._permitted(msg, role):
            self._reply(msg.message_id, f"抱歉，你在这里没有使用「{role.name}」的权限。")
            return
        if not body:
            req = "、".join(f.field for f in role.fields if f.required)
            self._reply(msg.message_id, f"请在命令后附上内容，例如：{role.command} ……（需要包含：{req}）")
            return
        existing = self.store.get_record(msg.message_id)
        if existing:
            self._reply(msg.message_id, f"这条消息已记录过：{existing[1]}")
            return
        for old in self.store.sessions_for(msg.chat_id, msg.sender_open_id):
            if old.role == role.command:
                self.store.delete_session(old)  # one open session per (chat,user,role)
        s = Session(chat_id=msg.chat_id, user_id=msg.sender_open_id, role=role.command,
                    source_message_id=msg.message_id, text=body)
        self._process(msg, role, s, people)

    def _find_session(self, msg: IncomingMessage) -> Session | None:
        sessions = self.store.sessions_for(msg.chat_id, msg.sender_open_id)
        for s in sessions:  # 1) reply inside the bot's follow-up thread
            if msg.root_id and msg.root_id == s.source_message_id or msg.parent_id and (
                msg.parent_id in s.ask_message_ids or msg.parent_id == s.source_message_id
            ):
                return s
        return sessions[0] if sessions else None  # 2) @bot again without a command

    def _context(self, msg: IncomingMessage, people: dict[str, str], all_people: dict[str, str]) -> Context:
        cache: dict[str, list[tuple[str, str]]] = {}

        def lookup(name: str) -> list[str]:
            if msg.chat_type == "p2p":
                return []
            if "m" not in cache:
                try:
                    cache["m"] = self.api.list_chat_members(msg.chat_id)
                except FeishuAPIError as e:
                    log.warning("chat_members.failed", code=e.code)
                    cache["m"] = []
            return [oid for n, oid in cache["m"] if n == name]

        return Context(sender_open_id=msg.sender_open_id, now=datetime.fromtimestamp(self.clock(), UTC),
                       tz=self.cfg.timezone, mentions=all_people, member_lookup=lookup)

    def _process(self, msg: IncomingMessage, role: Role, s: Session, people: dict[str, str]) -> None:
        reply_to = s.source_message_id
        all_people = {**s.raw_fields.get("__people__", {}), **people}
        ctx = self._context(msg, people, all_people)
        provider = self.extractor.providers[role.llm or self.extractor.default]
        prev = {k: v for k, v in s.raw_fields.items() if not k.startswith("__")}
        messages = build_messages(role, s.text, now=ctx.now, tz=ctx.tz, mentions=all_people,
                                  previous=prev or None, followups=s.followups)
        t0 = time.monotonic()
        audit = dict(chat_id=msg.chat_id, sender=msg.sender_open_id, command=role.command,
                     text=s.text if self.cfg.audit_store_text else None,
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
                if spec.key in result.values:
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

        s.raw_fields = {**merged, "__people__": all_people}
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
            name = role.name if role else s.role
            self._reply(s.source_message_id, f"「{name}」补充信息超时，已放弃本次记录，未写入。", thread=True)
        return len(expired)

    def _reply(self, message_id: str, text: str, thread: bool = False) -> str | None:
        try:
            return self.api.reply_text(message_id, text, in_thread=thread)
        except FeishuAPIError as e:
            log.error("reply.failed", code=e.code, msg=e.msg)
            return None
