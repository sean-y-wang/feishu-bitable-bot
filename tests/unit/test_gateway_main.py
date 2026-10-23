import json
from types import SimpleNamespace

import pytest

from bitable_bot.config import ConfigError
from bitable_bot.gateway import Gateway, to_incoming
from bitable_bot.main import Settings


def _payload():
    return {
        "header": {"event_id": "ev1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_s"}},
            "message": {
                "message_id": "om_1", "chat_id": "oc_1", "chat_type": "group", "message_type": "text",
                "content": json.dumps({"text": "@_user_1 /bug x"}), "root_id": "om_0", "parent_id": "om_0",
                "create_time": "1700000000000",
                "mentions": [{"key": "@_user_1", "name": "Bot", "id": {"open_id": "ou_bot"}}],
            },
        },
    }


def test_to_incoming_dict_and_objects():
    m = to_incoming(_payload())
    assert (m.event_id, m.message_id, m.sender_open_id, m.root_id) == ("ev1", "om_1", "ou_s", "om_0")
    assert m.mentions[0].open_id == "ou_bot" and m.create_time_ms == 1700000000000

    def ns(d):
        return SimpleNamespace(**{k: ns(v) if isinstance(v, dict) else [ns(x) for x in v] if isinstance(v, list)
                                  else v for k, v in d.items()})

    assert to_incoming(ns(_payload())).mentions[0].name == "Bot"


def test_gateway_dispatches_async():
    handled = []
    svc = SimpleNamespace(handle=handled.append)
    g = Gateway(svc, workers=1)
    g.on_message(_payload())
    g.pool.shutdown(wait=True)
    assert handled[0].message_id == "om_1"
    assert g.build_handler() is not None
    g.on_bot_added(None)


def test_settings_env():
    with pytest.raises(ConfigError, match="FEISHU_APP_SECRET"):
        Settings({"FEISHU_APP_ID": "a"})
    with pytest.raises(ConfigError):
        Settings({"FEISHU_APP_ID": "a", "FEISHU_APP_SECRET": "b", "FEISHU_DOMAIN": "x"})
    s = Settings({"FEISHU_APP_ID": "a", "FEISHU_APP_SECRET": "b", "FEISHU_DOMAIN": "lark"})
    assert s.open_domain == "https://open.larksuite.com" and s.bot_open_id is None


def test_main_reports_config_error(monkeypatch, capsys):
    from bitable_bot import main as m

    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    assert m.main() == 2
    assert "FEISHU_APP_ID" in capsys.readouterr().err
