"""Thin wrapper over lark-oapi OpenAPI calls used by the bot. Everything else talks to the Protocol."""

from __future__ import annotations

import json
from typing import Any, Protocol


class FeishuAPIError(Exception):
    def __init__(self, code: int, msg: str, http_status: int | None = None) -> None:
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg
        self.http_status = http_status


class FeishuAPI(Protocol):
    def create_record(self, app_token: str, table_id: str, fields: dict[str, Any], client_token: str) -> str: ...

    def reply_text(self, message_id: str, text: str, in_thread: bool = False) -> str: ...

    def list_chat_members(self, chat_id: str) -> list[tuple[str, str]]: ...


class LarkFeishuAPI:
    """Production implementation backed by lark_oapi.Client (handles tenant_access_token)."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @staticmethod
    def _check(resp: Any) -> None:
        if not resp.success():
            raw = getattr(resp, "raw", None)
            raise FeishuAPIError(resp.code, resp.msg, getattr(raw, "status_code", None))

    def create_record(self, app_token: str, table_id: str, fields: dict[str, Any], client_token: str) -> str:
        from lark_oapi.api.bitable.v1 import AppTableRecord, CreateAppTableRecordRequest

        req = (
            CreateAppTableRecordRequest.builder()
            .app_token(app_token)
            .table_id(table_id)
            .user_id_type("open_id")
            .client_token(client_token)
            .request_body(AppTableRecord.builder().fields(fields).build())
            .build()
        )
        resp = self.client.bitable.v1.app_table_record.create(req)
        self._check(resp)
        return resp.data.record.record_id

    def reply_text(self, message_id: str, text: str, in_thread: bool = False) -> str:
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .reply_in_thread(in_thread)
                .build()
            )
            .build()
        )
        resp = self.client.im.v1.message.reply(req)
        self._check(resp)
        return resp.data.message_id

    def list_chat_members(self, chat_id: str) -> list[tuple[str, str]]:
        from lark_oapi.api.im.v1 import GetChatMembersRequest

        out: list[tuple[str, str]] = []
        page_token = None
        while True:
            b = GetChatMembersRequest.builder().chat_id(chat_id).member_id_type("open_id").page_size(100)
            if page_token:
                b = b.page_token(page_token)
            resp = self.client.im.v1.chat_members.get(b.build())
            self._check(resp)
            out += [(m.name, m.member_id) for m in resp.data.items or []]
            if not resp.data.has_more:
                return out
            page_token = resp.data.page_token
