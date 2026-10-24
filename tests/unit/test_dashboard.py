"""Runtime dashboard: auth, secret masking, hot reload (success keeps new / failure keeps old)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from bitable_bot.config import ConfigError, load_config
from bitable_bot.dashboard import DashboardServer, Runtime, dashboard_settings, mask_secret

SECRET = "sk-test-SUPERSECRET-a1b2"
TOKEN = "t" * 32

YAML = """
llm:
  default: main
  fallback: [backup]
  providers:
    main: {base_url: "https://a.example/v1", api_key: "${DASH_TEST_KEY}", model: m1, timeout: 30}
    backup: {base_url: "https://b.example/v1", api_key: "", model: m2}
session: {timeout_minutes: 15}
intent: {min_confidence: 0.6}
remote_config: {enabled: true, app_token: bascnX, table_id: tblX, refresh_seconds: 0}
roles:
  - command: /req
    name: 需求
    target: {app_token: bascnY, table_id: tblY}
    system_prompt: 本地 prompt
    fields:
      - {key: title, field: 标题, type: text, required: true}
"""


class FakeAPI:
    def __init__(self) -> None:
        self.rows = [{"fields": {"config_key": "system_prompt", "config_value": "远程 v1", "role_command": "/req"}}]
        self.fail = False

    def list_records(self, app_token, table_id, page_size=100):
        if self.fail:
            raise RuntimeError("feishu 99991663 token invalid")
        return self.rows


class FakeStore:
    def audit_rows(self):
        return []


@pytest.fixture()
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("DASH_TEST_KEY", SECRET)
    monkeypatch.setenv("FEISHU_APP_SECRET", "feishu-app-secret-xyz9")
    cfgp = tmp_path / "roles.yaml"
    cfgp.write_text(YAML, encoding="utf-8")
    envp = tmp_path / ".env"
    envp.write_text("FEISHU_APP_ID=cli_x\nDASH_TEST_KEY=old\n", encoding="utf-8")
    cfg = load_config(cfgp)
    api = FakeAPI()
    extractor = SimpleNamespace(providers={}, default="", fallback=[])
    service = SimpleNamespace(cfg=cfg, api=api, store=FakeStore(), extractor=extractor, query=None, collector=None)
    settings = SimpleNamespace(
        config_path=str(cfgp),
        db_path=str(tmp_path / "bot.db"),
        app_id="cli_x",
        app_secret="feishu-app-secret-xyz9",
        open_domain="https://open.feishu.cn",
    )
    r = Runtime(settings, service, cfg, env_path=str(envp))
    yield r
    r.stop()


def test_mask_secret():
    assert mask_secret(SECRET) == "sk-****a1b2"
    assert mask_secret("short") == "****"
    assert mask_secret("") == ""


def test_system_config_never_leaks_secret(rt):
    blob = json.dumps(rt.system_config(), ensure_ascii=False)
    assert SECRET not in blob and "SUPERSECRET" not in blob
    assert "feishu-app-secret-xyz9" not in blob
    assert "sk-****a1b2" in blob
    assert any(s["name"] == "DASH_TEST_KEY" for s in rt.system_config()["secrets"])


def test_reload_success_hot_swaps(rt):
    res = rt.reload()
    assert res["ok"] and res["rows"] == 1 and res["source"] == "remote"
    assert rt.service.cfg.roles[0].system_prompt == "远程 v1"
    assert rt.service.extractor.default == "main" and set(rt.service.extractor.providers) == {"main", "backup"}


def test_reload_failure_keeps_old_config(rt):
    rt.reload()
    old = rt.service.cfg
    rt.service.api.fail = True
    res = rt.reload()
    assert not res["ok"] and "token invalid" in res["error"]
    assert rt.service.cfg is old and old.roles[0].system_prompt == "远程 v1"


def test_update_system_and_secrets(rt, tmp_path):
    out = rt.update_system(
        {
            "llm": {"fallback": "backup", "providers": {"main": {"model": "m9", "timeout": "12"}}},
            "session": {"timeout_minutes": 20},
        }
    )
    assert "llm.providers.main.model" in out["changed"] and out["reload"]["ok"]
    assert rt.service.cfg.llm.providers["main"].model == "m9" and rt.service.cfg.session.timeout_minutes == 20
    assert (tmp_path / "roles.yaml.bak").exists()
    with pytest.raises(ConfigError):
        rt.update_system({"llm": {"default": "nope"}})
    assert rt.service.cfg.llm.default == "main"

    res = rt.update_secrets({"DASH_TEST_KEY": "sk-new-value-9999"})
    assert res["changed"] == ["DASH_TEST_KEY"]
    env = (tmp_path / ".env").read_text()
    assert "DASH_TEST_KEY=sk-new-value-9999" in env and "FEISHU_APP_ID=cli_x" in env
    assert rt.service.cfg.llm.providers["main"].api_key == "sk-new-value-9999"
    with pytest.raises(ConfigError):
        rt.update_secrets({"PATH": "x"})
    audit = (tmp_path / "dashboard_audit.log").read_text()
    assert "secrets.update" in audit and "sk-new-value-9999" not in audit


def test_dashboard_settings():
    assert dashboard_settings({}) is None
    with pytest.raises(ValueError):
        dashboard_settings({"DASHBOARD_TOKEN": "short"})
    ds = dashboard_settings({"DASHBOARD_TOKEN": TOKEN})
    assert ds["host"] == "127.0.0.1" and ds["port"] == 8787


def _req(url, token=None, data=None):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_http_auth_and_no_plaintext(rt):
    srv = DashboardServer(rt, "127.0.0.1", 0, TOKEN)
    srv.start()
    base = f"http://127.0.0.1:{srv.port}"
    try:
        assert _req(base + "/")[0] == 200
        assert _req(base + "/api/overview")[0] == 401
        assert _req(base + "/api/system", token="wrong" * 8)[0] == 401
        assert _req(base + "/api/reload", data=b"{}")[0] == 401
        code, body = _req(base + "/api/system", token=TOKEN)
        assert code == 200 and SECRET not in body and "sk-****a1b2" in body
        code, body = _req(base + "/api/overview", token=TOKEN)
        assert code == 200 and json.loads(body)["role_count"] == 1
        code, body = _req(base + "/api/reload", token=TOKEN, data=b"{}")
        assert code == 200 and json.loads(body)["ok"]
    finally:
        srv.stop()


def test_base_url_is_restart_only(rt):
    with pytest.raises(ConfigError, match="restart-only"):
        rt.update_system({"llm": {"providers": {"main": {"base_url": "https://evil.example/v1"}}}})
    # unchanged value is tolerated (UI may echo it back)
    out = rt.update_system({"llm": {"providers": {"main": {"base_url": "https://a.example/v1", "timeout": 12}}}})
    assert "llm.providers.main.timeout" in out["changed"]
    assert not any(k.endswith("base_url") for k in out["changed"])
    from bitable_bot.dashboard.runtime import HOT_RELOAD
    assert not any("base_url" in x and "只读" not in x for x in HOT_RELOAD["hot"])
    assert any("base_url" in x for x in HOT_RELOAD["restart"])
