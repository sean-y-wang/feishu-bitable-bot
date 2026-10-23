from types import SimpleNamespace

import pytest

from bitable_bot.session import Session, Store
from bitable_bot.writer import BitableWriter, FeishuAPIError, LarkFeishuAPI, WriteError, client_token_for

from ..conftest import Clock, FakeAPI


def test_dedup_ttl():
    c = Clock()
    s = Store(":memory:", clock=c)
    assert s.seen("evt:1") is False
    assert s.seen("evt:1") is True
    c.t += 86401
    assert s.seen("evt:1") is False
    s.purge_expired()


def test_sessions_expire(tmp_path):
    c = Clock()
    st = Store(tmp_path / "sub" / "b.db", clock=c)
    sess = Session("oc", "ou", "/bug", "om_1", "t", expires_at=c.t + 60)
    st.save_session(sess)
    assert st.sessions_for("oc", "ou")[0].source_message_id == "om_1"
    assert st.pop_expired_sessions() == []
    c.t += 61
    assert st.sessions_for("oc", "ou") == []
    assert [x.source_message_id for x in st.pop_expired_sessions()] == ["om_1"]
    assert st.pop_expired_sessions() == []


def test_audit_rows():
    st = Store()
    st.audit(chat_id="oc", fields={"a": "中"}, result="ok")
    row = st.audit_rows()[0]
    assert row["chat_id"] == "oc" and row["fields"] == '{"a": "中"}'


def _writer(api=None):
    api = api or FakeAPI()
    return BitableWriter(api, Store(), sleep=lambda s: None), api


def test_write_maps_fields_and_idempotent(cfg):
    role = cfg.find_role("/bug")
    w, api = _writer()
    r = w.write(role, {"title": "x", "severity": "P1", "__evil": 1}, "om_1")
    assert api.records[0]["fields"] == {"标题": "x", "严重程度": "P1"}
    assert api.records[0]["client_token"] == client_token_for("om_1")
    assert api.records[0]["app_token"] == "bascnYYYY" and r.url.endswith("record=rec1")
    r2 = w.write(role, {"title": "x"}, "om_1")
    assert r2.duplicated and len(api.records) == 1


def test_client_token_deterministic():
    assert client_token_for("a") == client_token_for("a") != client_token_for("b")


def test_write_retries_then_ok(cfg):
    w, api = _writer()
    api.create_errors = [FeishuAPIError(99991400, "rate"), FeishuAPIError(0, "5xx", 502), FeishuAPIError(1, "x", 429)]
    assert w.write(cfg.roles[1], {"title": "x"}, "om_2").record_id == "rec1"


@pytest.mark.parametrize("code,needle", [(91403, "协作者"), (1254045, "字段"), (123, "123")])
def test_write_errors(cfg, code, needle):
    w, api = _writer()
    api.create_errors = [FeishuAPIError(code, "bad")]
    with pytest.raises(WriteError, match=needle):
        w.write(cfg.roles[1], {"title": "x"}, "om_3")


def test_write_retry_exhausted(cfg):
    w, api = _writer()
    api.create_errors = [FeishuAPIError(1254290, "busy")] * 4
    with pytest.raises(WriteError):
        w.write(cfg.roles[1], {"title": "x"}, "om_4")


class _Resp:
    def __init__(self, ok=True, data=None, code=0, msg=""):
        self._ok, self.data, self.code, self.msg = ok, data, code, msg
        self.raw = SimpleNamespace(status_code=200 if ok else 400)

    def success(self):
        return self._ok


def test_lark_api_wrapper():
    calls = {}

    def create(req):
        calls["create"] = req
        return _Resp(data=SimpleNamespace(record=SimpleNamespace(record_id="recX")))

    def reply(req):
        calls["reply"] = req
        return _Resp(data=SimpleNamespace(message_id="om_r"))

    pages = [SimpleNamespace(items=[SimpleNamespace(name="A", member_id="ou_a")], has_more=True, page_token="p2"),
             SimpleNamespace(items=[SimpleNamespace(name="B", member_id="ou_b")], has_more=False, page_token="")]
    client = SimpleNamespace(
        bitable=SimpleNamespace(v1=SimpleNamespace(app_table_record=SimpleNamespace(create=create))),
        im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(reply=reply),
                                              chat_members=SimpleNamespace(get=lambda r: _Resp(data=pages.pop(0))))),
    )
    api = LarkFeishuAPI(client)
    assert api.create_record("app", "tbl", {"标题": "x"}, "tok") == "recX"
    assert calls["create"].client_token == "tok" and calls["create"].request_body.fields == {"标题": "x"}
    assert api.reply_text("om_1", "你好", in_thread=True) == "om_r"
    assert '"你好"' in calls["reply"].request_body.content and calls["reply"].request_body.reply_in_thread
    assert api.list_chat_members("oc") == [("A", "ou_a"), ("B", "ou_b")]
    bad = SimpleNamespace(bitable=SimpleNamespace(v1=SimpleNamespace(app_table_record=SimpleNamespace(
        create=lambda r: _Resp(ok=False, code=91403, msg="no perm")))))
    with pytest.raises(FeishuAPIError) as ei:
        LarkFeishuAPI(bad).create_record("a", "t", {}, "x")
    assert ei.value.code == 91403 and ei.value.http_status == 400
