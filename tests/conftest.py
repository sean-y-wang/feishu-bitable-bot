from __future__ import annotations

import json
from pathlib import Path

import pytest

from bitable_bot.config import load_config
from bitable_bot.extractor import Extractor
from bitable_bot.router import IncomingMessage, Mention
from bitable_bot.service import BotService
from bitable_bot.session import Store
from bitable_bot.writer import BitableWriter, FeishuAPIError

ROOT = Path(__file__).resolve().parents[1]
NOW = 1792728000.0  # 2026-10-23 (Fri) 12:00:00 Asia/Shanghai


class Clock:
    def __init__(self, t: float = NOW) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeAPI:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.replies: list[dict] = []
        self.members: list[tuple[str, str]] = []
        self.create_errors: list[FeishuAPIError] = []
        self._n = 0

    def create_record(self, app_token, table_id, fields, client_token):
        if self.create_errors:
            raise self.create_errors.pop(0)
        self.records.append(dict(app_token=app_token, table_id=table_id, fields=fields, client_token=client_token))
        return f"rec{len(self.records)}"

    def reply_text(self, message_id, text, in_thread=False):
        self._n += 1
        mid = f"om_bot{self._n}"
        self.replies.append(dict(message_id=message_id, text=text, in_thread=in_thread, id=mid))
        return mid

    def list_chat_members(self, chat_id):
        return self.members


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, outputs=None) -> None:
        self.outputs = list(outputs or [])
        self.calls: list[list[dict]] = []

    def extract(self, messages, schema):
        self.calls.append(messages)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


@pytest.fixture
def cfg():
    env = {"OPENAI_API_KEY": "k", "DASHSCOPE_API_KEY": "k", "DEEPSEEK_API_KEY": "k", "PRIVATE_LLM_KEY": "k"}
    return load_config(ROOT / "config" / "roles.example.yaml", env=env)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def harness(cfg, clock):
    class H:
        pass

    h = H()
    h.api = FakeAPI()
    h.store = Store(":memory:", clock=clock)
    h.provider = FakeProvider()
    providers = {name: h.provider for name in cfg.llm.providers}
    h.service = BotService(cfg, h.api, h.store, Extractor(providers, cfg.llm.default),
                           BitableWriter(h.api, h.store, sleep=lambda s: None), clock=clock)
    h.clock = clock
    return h


_seq = [0]


def make_msg(text: str, *, sender="ou_alice", chat="oc_1", mentions=None, root_id="", parent_id="",
             message_id=None, event_id=None, chat_type="group") -> IncomingMessage:
    _seq[0] += 1
    ms = [Mention("@_user_1", "记录机器人", "ou_bot")] + [
        Mention(f"@_user_{i + 2}", n, oid) for i, (n, oid) in enumerate(mentions or [])
    ]
    return IncomingMessage(
        event_id=event_id or f"ev{_seq[0]}", message_id=message_id or f"om_{_seq[0]}", chat_id=chat,
        chat_type=chat_type, sender_open_id=sender, message_type="text",
        content=json.dumps({"text": f"@_user_1 {text}"}, ensure_ascii=False),
        mentions=ms, root_id=root_id, parent_id=parent_id,
    )
