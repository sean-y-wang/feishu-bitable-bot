import pytest

from bitable_bot.config import ConfigError, expand_env, load_config


def test_example_config_loads(cfg):
    assert [r.command for r in cfg.roles] == ["/日报", "/bug"]
    assert cfg.llm.providers["qwen"].api_key == "k"
    assert cfg.find_role("/daily").name == "研发日报"


def test_expand_env_default_and_missing():
    assert expand_env({"a": ["${X}", "${Y:-d}", "${Z}"]}, {"X": "1"}) == {"a": ["1", "d", ""]}


def _write(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text(body, encoding="utf-8")
    return p


BASE = """
llm: {default: a, providers: {a: {base_url: http://x, model: m}}}
roles:
"""


@pytest.mark.parametrize(
    "roles",
    [
        "  - {command: /a, name: A, target: {app_token: t, table_id: t}, fields: []}\n"
        "  - {command: /a, name: B, target: {app_token: t, table_id: t}, fields: []}\n",
        "  - {command: /help, name: A, target: {app_token: t, table_id: t}, fields: []}\n",
        "  - {command: nope, name: A, target: {app_token: t, table_id: t}, fields: []}\n",
        "  - {command: /a, name: A, target: {app_token: t, table_id: t}, fields: [{key: s, field: S, type: single_select}]}\n",
        "  - {command: /a, name: A, llm: zz, target: {app_token: t, table_id: t}, fields: []}\n",
    ],
)
def test_invalid_configs(tmp_path, roles):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, BASE + roles), env={})


def test_unknown_default_provider(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, "llm: {default: b, providers: {a: {base_url: x, model: m}}}\nroles: []"), env={})


def test_unreadable(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")
