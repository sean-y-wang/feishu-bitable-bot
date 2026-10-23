import json
from types import SimpleNamespace

from bitable_bot.context import ContextCollector, ConversationContext, _fit, build_message_link
from bitable_bot.main import resolve_bot_open_id
from bitable_bot.writer import FeishuAPIError
from bitable_bot.writer.feishu_api import LarkFeishuAPI

from ..conftest import FakeAPI, make_msg

H = "applink.feishu.cn"


def test_message_link_variants():
    assert build_message_link({"message_app_link": "https://x/y"}, "oc_1", H) == "https://x/y"
    t = build_message_link({"thread_id": "omt_1", "thread_message_position": 3}, "oc_1", H)
    assert t.startswith("https://applink.feishu.cn/client/thread/open?") and "openthreadid=omt_1" in t
    assert "thread_position=3" in t and "open_chat_id=oc_1" in t
    assert build_message_link({"message_position": "7"}, "oc_1", H).endswith("openChatId=oc_1&position=7")
    assert build_message_link({}, "oc_1", H) == "https://applink.feishu.cn/client/chat/open?openChatId=oc_1"
    assert build_message_link({}, "", H) == ""


def test_fit_trims_oldest_lines_of_last_section_first():
    secs = [("A", ["a" * 10]), ("B", ["1" * 10, "2" * 10, "3" * 10])]
    text = _fit(secs, 40)
    assert "a" * 10 in text and "3" * 10 in text and "1" * 10 not in text
    assert len(_fit([("A", ["x" * 100])], 20)) == 20


def test_name_resolution_chain_and_cache(cfg, clock):
    api = FakeAPI()
    c = ContextCollector(api, cfg, clock=clock)
    api.users = {"ou_a": "通讯录名"}
    api.members = [("群名片", "ou_b")]
    assert c.resolve_name("ou_a", "oc_1") == "通讯录名"
    assert c.resolve_name("ou_b", "oc_1") == "群名片"
    assert c.resolve_name("ou_c", "oc_1") == "ou_c"
    api.users = {"ou_a": "改名"}
    assert c.resolve_name("ou_a", "oc_1") == "通讯录名"  # cached
    clock.t += cfg.context.name_cache_seconds + 1
    assert c.resolve_name("ou_a", "oc_1") == "改名"
    api.fail = {"get_user_name"}
    assert c.resolve_name("ou_b2", "") == "ou_b2"


def test_chat_name(cfg, clock):
    api = FakeAPI()
    api.chat_names = {"oc_1": "研发群"}
    c = ContextCollector(api, cfg, clock=clock)
    assert c.chat_name(make_msg("x")) == "研发群"
    assert c.chat_name(make_msg("x", chat_type="p2p")) == "单聊"
    api.fail = {"get_chat_name"}
    assert c.chat_name(make_msg("x", chat="oc_2")) == "oc_2"


def test_render_and_recent_order(cfg, clock):
    api = FakeAPI()
    api.members = [("Bob", "ou_b")]
    c = ContextCollector(api, cfg, bot_open_id="ou_bot", clock=clock)
    mk = lambda mid, s, t, ts: {"message_id": mid, "msg_type": "text", "create_time": str(ts),  # noqa: E731
                                "sender": {"id": s, "sender_type": "user"},
                                "body": {"content": json.dumps({"text": t})}}
    m = make_msg("now")
    api.history[("chat", "oc_1")] = [mk("o1", "ou_b", "first", 1792727000000), mk("o2", "ou_bot", "bot", 1792727100000),
                                      mk(m.message_id, "ou_alice", "now", 1792728000000)]
    ctx = c.collect(m)
    assert ctx.excerpt.splitlines()[0] == "【最近的聊天消息】"
    lines = ctx.excerpt.splitlines()[1:]
    assert lines[0].endswith("Bob：first") and lines[1].endswith("机器人：bot") and len(lines) == 2
    assert lines[0].startswith("[10-23 11:43]")
    assert ConversationContext.from_dict(ctx.to_dict()) == ctx
    assert c.render({"deleted": True}, "oc_1") == ""
    assert c.render({"msg_type": "image", "sender": {"id": "ou_b"}}, "oc_1").endswith("Bob：[图片]")


def test_context_disabled_keeps_link_and_names(cfg, clock):
    cfg.context.enabled = False
    api = FakeAPI()
    api.history[("chat", "oc_1")] = [{"message_id": "o1", "msg_type": "text", "body": {"content": '{"text":"x"}'}}]
    ctx = ContextCollector(api, cfg, clock=clock).collect(make_msg("y"))
    assert ctx.excerpt == "" and ctx.message_link and ctx.chat_name == "oc_1"


def test_resolve_bot_open_id():
    api = FakeAPI()
    assert resolve_bot_open_id(api, "ou_env") == "ou_env"
    assert resolve_bot_open_id(api, None) == "ou_bot"
    api.fail = {"get_bot_open_id"}
    assert resolve_bot_open_id(api, None) is None


class _Client:
    def __init__(self, body):
        self.body = body
        self.reqs = []

    def request(self, req):
        self.reqs.append(req)
        return SimpleNamespace(code=None, msg=None, raw=SimpleNamespace(content=json.dumps(self.body).encode(),
                                                                         status_code=200))


def test_lark_raw_calls():
    cl = _Client({"code": 0, "data": {"items": [{"message_id": "om_1", "message_position": "5"}]}})
    api = LarkFeishuAPI(cl)
    assert api.get_message("om_1")["message_position"] == "5"
    assert api.list_messages("chat", "oc_1", 11)[0]["message_id"] == "om_1"
    q = dict(cl.reqs[-1].queries)
    assert q["container_id_type"] == "chat" and q["container_id"] == "oc_1" and q["page_size"] == "11"
    assert q["sort_type"] == "ByCreateTimeDesc"
    api.list_messages("thread", "omt_1", 20, newest_first=False)
    assert dict(cl.reqs[-1].queries)["sort_type"] == "ByCreateTimeAsc"
    cl.body = {"code": 0, "data": {"user": {"name": "Alice"}}}
    assert api.get_user_name("ou_a") == "Alice"
    assert dict(cl.reqs[-1].queries)["user_id_type"] == "open_id"
    cl.body = {"code": 0, "data": {"name": "研发群"}}
    assert api.get_chat_name("oc_1") == "研发群"
    cl.body = {"code": 0, "bot": {"open_id": "ou_bot"}}
    assert api.get_bot_open_id() == "ou_bot"
    cl.body = {"code": 230027, "msg": "Lack of necessary permissions"}
    try:
        api.get_chat_name("oc_1")
        raise AssertionError
    except FeishuAPIError as e:
        assert e.code == 230027
