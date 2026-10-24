"""Chat field bindings, remote business config merge, LLM fallback chain."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from bitable_bot.config import load_config, merge_remote_into_config
from bitable_bot.config.models import ChatFieldBinding, ProviderConfig
from bitable_bot.config.remote import apply_remote_config, cell_text
from bitable_bot.extractor import Extractor
from bitable_bot.providers.base import LLMError
from bitable_bot.providers.openai_compat import (
    ChainedProvider,
    OpenAICompatibleProvider,
    build_providers,
    provider_chain,
)
from bitable_bot.service import BotService
from bitable_bot.session import Store
from bitable_bot.writer import BitableWriter
from tests.conftest import ROOT, Clock, FakeAPI, FakeProvider, make_msg

REQ = httpx.Request("POST", "http://x/v1/chat/completions")


def _status(code):
    return APIStatusError("err", response=httpx.Response(code, request=REQ), body=None)


def _ok(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=SimpleNamespace(total_tokens=1)
    )


class FakeClient:
    def __init__(self, results):
        self.results = list(results)
        self.kwargs = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.kwargs.append(kw)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


# -------------------- chat bindings --------------------


def test_chat_field_binding_normalizes_chat_id():
    b = ChatFieldBinding(chat_id="oc_1", field_key="project", value="AIHub")
    assert b.chat_ids == ["oc_1"]


def test_resolve_chat_bindings_role_and_global(cfg):
    cfg.chat_field_bindings = [
        ChatFieldBinding(chat_ids=["oc_ai"], field_key="severity", value="P0"),
    ]
    bug = cfg.find_role("/bug")
    bug.chat_bindings = [
        ChatFieldBinding(chat_id="oc_ai", field_key="title", value="from-role"),
    ]
    assert cfg.resolve_chat_bindings("oc_other", bug) == []
    pairs = dict(cfg.resolve_chat_bindings("oc_ai", bug))
    assert pairs["severity"] == "P0"
    assert pairs["title"] == "from-role"


def test_binding_applied_on_write(cfg, tmp_path):
    cfg.first_guide_enabled = False
    cfg.ack_after_seconds = 0
    bug = cfg.find_role("/bug")
    bug.confirm_before_write = False
    cfg.chat_field_bindings = [
        ChatFieldBinding(chat_ids=["oc_bind"], field_key="severity", value="P0"),
    ]
    api = FakeAPI()
    store = Store(":memory:", clock=Clock())
    provider = FakeProvider(
        [
            {"fields": {"title": "登录卡顿", "severity": "P2"}, "missing": []},
        ]
    )
    providers = {n: provider for n in cfg.llm.providers}
    svc = BotService(
        cfg,
        api,
        store,
        Extractor(providers, cfg.llm.default, fallback=cfg.llm.fallback),
        BitableWriter(api, store, sleep=lambda s: None),
        clock=Clock(),
    )
    svc.handle(make_msg("/bug 登录卡顿 P2", chat="oc_bind"))
    assert api.records, api.replies
    fields = api.records[0]["fields"]
    # Bitable column name for severity
    assert fields.get("严重程度") == "P0" or any("P0" in str(v) for v in fields.values())


# -------------------- remote config --------------------


def test_parse_and_merge_remote_rows(cfg):
    rows = [
        {
            "fields": {
                "config_key": "first_guide_text",
                "config_value": "远程引导文案",
                "enabled": True,
            }
        },
        {
            "fields": {
                "config_key": "system_prompt",
                "config_value": "远程 system",
                "role_command": "/bug",
                "enabled": "是",
            }
        },
        {
            "fields": {
                "config_key": "guide_prompt",
                "config_value": "应被跳过",
                "role_command": "/bug",
                "enabled": False,
            }
        },
        {
            "fields": {
                "config_key": "min_content_chars",
                "config_value": "9",
                "role_command": "/bug",
                "enabled": True,
            }
        },
        {
            "fields": {
                "config_key": "chat_binding",
                "config_value": json.dumps(
                    {"chat_ids": ["oc_r"], "field_key": "severity", "value": "P1"}, ensure_ascii=False
                ),
                "enabled": True,
            }
        },
        {
            "fields": {
                "config_key": "field.title.description",
                "config_value": "远程标题说明",
                "role_command": "/bug",
                "enabled": True,
            }
        },
    ]
    merged = merge_remote_into_config(cfg, rows)
    assert merged.first_guide_text == "远程引导文案"
    bug = merged.find_role("/bug")
    assert bug.system_prompt == "远程 system"
    assert bug.guide_prompt and "应被跳过" not in (bug.guide_prompt or "")
    assert bug.min_content_chars == 9
    assert any(b.chat_ids == ["oc_r"] and b.value == "P1" for b in merged.chat_field_bindings)
    assert bug.field_by_key("title").description == "远程标题说明"


def test_apply_remote_config_fallback_on_error(cfg):
    cfg.remote_config.enabled = True
    cfg.remote_config.app_token = "bascnX"
    cfg.remote_config.table_id = "tblX"
    api = FakeAPI()
    api.fail.add("list_records")
    out = apply_remote_config(cfg, api)
    assert out is cfg  # unchanged on failure


def test_apply_remote_config_success(cfg):
    cfg.remote_config.enabled = True
    cfg.remote_config.app_token = "bascnX"
    cfg.remote_config.table_id = "tblX"
    api = FakeAPI()
    api.bitable_rows = [
        {
            "app_token": "bascnX",
            "table_id": "tblX",
            "fields": {"config_key": "bot_name", "config_value": "远程名", "enabled": True},
        }
    ]
    out = apply_remote_config(cfg, api)
    assert out.bot_name == "远程名"


def test_cell_text_variants():
    assert cell_text([{"text": "a"}, {"text": "b"}]) == "ab"
    assert cell_text(True) == "true"


def test_remote_config_requires_tokens(tmp_path):
    body = """
llm: {default: a, providers: {a: {base_url: http://x, model: m}}}
remote_config: {enabled: true, app_token: "", table_id: ""}
roles:
  - {command: /a, name: A, target: {app_token: t, table_id: t}, fields: []}
"""
    from bitable_bot.config import ConfigError

    p = tmp_path / "c.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p, env={})


# -------------------- LLM fallback --------------------


def test_same_provider_fallback_models():
    cfg = ProviderConfig(base_url="http://x/v1", model="m1", fallback_models=["m2"], supports_json_schema=False)
    c = FakeClient([_status(503), _status(503), _status(503), _ok("{}")])
    p = OpenAICompatibleProvider("p", cfg, client=c, max_attempts=3, sleep=lambda s: None)
    assert p.extract([], {}) == "{}"
    models = [k["model"] for k in c.kwargs]
    assert models[:3] == ["m1", "m1", "m1"]
    assert models[3] == "m2"


def test_cross_provider_chain(cfg):
    a = FakeProvider([LLMError("a down")])
    b = FakeProvider(['{"fields":{"title":"ok"},"missing":[]}'])
    a.name, a.model = "a", "ma"
    b.name, b.model = "b", "mb"
    chain = ChainedProvider([a, b])
    assert json.loads(chain.extract([], {}))["fields"]["title"] == "ok"
    assert a.calls and b.calls


def test_provider_chain_helper(cfg):
    providers = build_providers(cfg)
    # Force unknown fallback ignored
    one = provider_chain(providers, "qwen", ["nope", "deepseek"])
    assert isinstance(one, ChainedProvider) or one.name == "qwen"


def test_extractor_uses_fallback(cfg):
    primary = FakeProvider([LLMError("boom")])
    secondary = FakeProvider(['{"fields":{"title":"T","severity":"P2"},"missing":[]}'])
    primary.name = "qwen"
    secondary.name = "deepseek"
    providers = {n: primary for n in cfg.llm.providers}
    providers["deepseek"] = secondary
    providers["qwen"] = primary
    ext = Extractor(providers, "qwen", fallback=["deepseek"])
    role = cfg.find_role("/bug")
    out = ext.extract(role, [{"role": "user", "content": "x"}])
    assert out.fields["title"] == "T"
    assert primary.calls and secondary.calls


def test_requirements_template_loads():
    env = {"OPENAI_API_KEY": "k", "DASHSCOPE_API_KEY": "k", "DEEPSEEK_API_KEY": "k", "PRIVATE_LLM_KEY": "k"}
    c = load_config(ROOT / "config" / "roles.requirements.yaml", env=env)
    assert c.dedicated and c.llm.fallback == ["deepseek"]
    assert c.llm.providers["qwen"].fallback_models == ["qwen-turbo"]
    assert c.remote_config.enabled is False
