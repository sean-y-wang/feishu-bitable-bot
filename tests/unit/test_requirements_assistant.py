"""Built-in 需求小助手 (dedicated requirements scenario)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bitable_bot.config import ConfigError, load_config
from bitable_bot.extractor import Extractor
from bitable_bot.main import BUILTIN_SCENARIOS, Settings, resolve_config_path
from bitable_bot.query import QueryService
from bitable_bot.receipt import confirm_card
from bitable_bot.router import help_text
from bitable_bot.service import PHASE_CONFIRM, PHASE_MODIFY, BotService
from bitable_bot.session import Store
from bitable_bot.writer import BitableWriter

from ..conftest import Clock, FakeAPI, FakeProvider, make_msg

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def req_cfg():
    env = {
        "OPENAI_API_KEY": "k",
        "DASHSCOPE_API_KEY": "k",
        "DEEPSEEK_API_KEY": "k",
        "PRIVATE_LLM_KEY": "k",
    }
    c = load_config(ROOT / "config" / "roles.requirements.yaml", env=env)
    c.first_guide_enabled = False
    c.ack_after_seconds = 0
    return c


@pytest.fixture
def req_harness(req_cfg):
    clock = Clock()
    api = FakeAPI()
    api.cards_ok = True
    store = Store(":memory:", clock=clock)
    provider = FakeProvider()
    providers = {name: provider for name in req_cfg.llm.providers}
    extractor = Extractor(providers, req_cfg.llm.default)
    qsvc = QueryService(api, req_cfg, providers, req_cfg.llm.default)
    service = BotService(
        req_cfg,
        api,
        store,
        extractor,
        BitableWriter(api, store, sleep=lambda s: None),
        clock=clock,
        query_service=qsvc,
        bot_open_id="ou_bot",
    )
    return type("H", (), dict(api=api, store=store, provider=provider, service=service, clock=clock, cfg=req_cfg))()


def test_builtin_scenario_path():
    assert resolve_config_path({"BOT_SCENARIO": "requirements"}) == BUILTIN_SCENARIOS["requirements"]
    assert resolve_config_path({"BOT_CONFIG": "config/custom.yaml", "BOT_SCENARIO": "requirements"}) == "config/custom.yaml"
    with pytest.raises(ConfigError, match="unknown BOT_SCENARIO"):
        resolve_config_path({"BOT_SCENARIO": "nope"})
    s = Settings(
        {
            "FEISHU_APP_ID": "a",
            "FEISHU_APP_SECRET": "b",
            "BOT_SCENARIO": "requirements",
        }
    )
    assert s.config_path.endswith("roles.requirements.yaml")


def test_requirements_config_shape(req_cfg):
    assert req_cfg.scenario == "requirements"
    assert req_cfg.dedicated is True
    assert req_cfg.bot_name == "需求小助手"
    assert req_cfg.react_on_mention is True
    assert req_cfg.context.recent_messages == 5
    role = req_cfg.roles[0]
    assert role.name == "需求小助手"
    assert role.confirm_before_write is True
    assert role.query.max_reply == 2
    assert req_cfg.primary_role() is role
    assert "需求小助手" in help_text(req_cfg)


def test_dedicated_rejects_multi_role(tmp_path):
    body = """
dedicated: true
llm: {default: a, providers: {a: {base_url: http://x, model: m}}}
roles:
  - {command: /a, name: A, target: {app_token: t, table_id: t}, fields: []}
  - {command: /b, name: B, target: {app_token: t, table_id: t}, fields: []}
"""
    p = tmp_path / "c.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match="dedicated"):
        load_config(p, env={})


def test_react_and_confirm_card_flow(req_harness):
    h = req_harness
    h.provider.outputs = [
        {
            "action": "record",
            "role": "/提需求",
            "confidence": 0.9,
            "query": None,
            "keywords": None,
            "reason": "提需求",
        },
        {
            "fields": {
                "title": "订单导出加速",
                "desc": "导出太慢",
                "project": "其他",
                "type": "优化",
                "priority": "P1",
                "due": None,
                "proposer": None,
                "status": None,
            },
            "missing": [],
            "confidence": {},
        },
    ]
    m = make_msg("订单导出太慢了，P1，希望优化一下导出性能")
    h.service.handle(m)
    assert h.api.reactions and h.api.reactions[0] == (m.message_id, "Get")
    # confirm card in thread
    cards = [r for r in h.api.replies if r.get("type") == "interactive"]
    assert cards and cards[-1]["in_thread"]
    card = cards[-1]["card"]
    assert "请确认写入" in card["header"]["title"]["content"]
    actions = card["elements"][-1]["actions"]
    assert {a["text"]["content"] for a in actions} == {"确认", "取消", "修改"}
    assert not h.api.records
    sessions = h.store.sessions_for(m.chat_id, m.sender_open_id)
    assert sessions and sessions[0].raw_fields.get("__phase__") == PHASE_CONFIRM

    # card confirm
    value = actions[0]["value"]
    toast = h.service.handle_card_action(
        {"operator": {"open_id": m.sender_open_id}, "action": {"value": value}}
    )
    assert toast["toast"]["type"] == "success"
    assert h.api.records
    assert h.api.records[0]["fields"]["需求标题"] == "订单导出加速"
    assert h.store.sessions_for(m.chat_id, m.sender_open_id) == []


def test_card_cancel_and_modify(req_harness):
    h = req_harness
    h.provider.outputs = [
        {
            "action": "record",
            "role": "/提需求",
            "confidence": 0.95,
            "query": None,
            "keywords": None,
            "reason": "ok",
        },
        {
            "fields": {"title": "A 需求", "desc": "d", "project": "其他", "type": "新功能", "priority": "P2"},
            "missing": [],
            "confidence": {},
        },
    ]
    m = make_msg("希望做一个 A 需求，新功能，中优先级，背景是 ddddddddd")
    h.service.handle(m)
    card = [r for r in h.api.replies if r.get("type") == "interactive"][-1]["card"]
    cancel_val = next(a["value"] for a in card["elements"][-1]["actions"] if a["text"]["content"] == "取消")
    toast = h.service.handle_card_action(
        {"operator": {"open_id": m.sender_open_id}, "action": {"value": cancel_val}}
    )
    assert toast["toast"]["content"] == "已取消"
    assert not h.api.records

    # restart and modify
    h.provider.outputs = [
        {
            "action": "record",
            "role": "/提需求",
            "confidence": 0.95,
            "query": None,
            "keywords": None,
            "reason": "ok",
        },
        {
            "fields": {"title": "B 需求", "desc": "背景说明足够长了", "project": "其他", "type": "新功能", "priority": "P2"},
            "missing": [],
            "confidence": {},
        },
    ]
    m2 = make_msg("希望做一个 B 需求，新功能，中优先级，背景说明足够长了")
    h.service.handle(m2)
    card = [r for r in h.api.replies if r.get("type") == "interactive"][-1]["card"]
    modify_val = next(a["value"] for a in card["elements"][-1]["actions"] if a["text"]["content"] == "修改")
    h.service.handle_card_action(
        {"operator": {"open_id": m2.sender_open_id}, "action": {"value": modify_val}}
    )
    s = h.store.sessions_for(m2.chat_id, m2.sender_open_id)[0]
    assert s.raw_fields["__phase__"] == PHASE_MODIFY
    # edit priority then re-confirm card
    h.service.handle(make_msg("优先级改成 P0", root_id=m2.message_id, parent_id=m2.message_id, at_bot=False))
    s = h.store.sessions_for(m2.chat_id, m2.sender_open_id)[0]
    assert s.raw_fields["__phase__"] == PHASE_CONFIRM
    assert s.raw_fields.get("priority") == "P0" or "P0" in str(s.raw_fields.get("priority"))


def test_confirm_card_builder(req_cfg):
    role = req_cfg.roles[0]
    card = confirm_card(
        role,
        {"title": "x", "priority": "P1"},
        {},
        "Asia/Shanghai",
        chat_id="oc",
        user_id="ou",
        source_message_id="om",
    )
    assert card["header"]["title"]["content"].startswith("请确认写入")
    assert len(card["elements"][-1]["actions"]) == 3


def test_query_top2_and_docs_hint(req_harness):
    h = req_harness
    h.api.bitable_rows = [
        {
            "record_id": "rec1",
            "fields": {"需求标题": "Acme API 流式", "状态": "联调中", "优先级": "P1", "提出人": "Alice"},
        },
        {
            "record_id": "rec2",
            "fields": {"需求标题": "Acme API 限流", "状态": "待评估", "优先级": "P2", "提出人": "Bob"},
        },
        {
            "record_id": "rec3",
            "fields": {"需求标题": "其它无关", "状态": "完成", "优先级": "P3", "提出人": "C"},
        },
    ]
    h.provider.outputs = [
        {
            "action": "query",
            "role": "/提需求",
            "confidence": 0.9,
            "query": "Acme API",
            "keywords": ["Acme", "API"],
            "reason": "查进展",
        },
        {
            "summary": "找到相关记录",
            "items": [
                {"record_id": "rec1", "one_liner": "流式返回联调中"},
                {"record_id": "rec2", "one_liner": "限流待评估"},
                {"record_id": "rec3", "one_liner": "should not appear"},
            ],
        },
    ]
    h.service.handle(make_msg("Acme API 上线了吗"))
    reply = h.api.replies[-1]["text"]
    assert "流式返回联调中" in reply
    assert "限流待评估" in reply
    assert "should not appear" not in reply
    assert h.cfg.query_docs_hint in reply
    assert not h.api.records
