import json

from bitable_bot.router import Kind, Mention, clean_text, extract_text, help_text, route


def test_route_role_and_alias(cfg):
    r = route("/日报 今天完成登录", cfg)
    assert r.kind is Kind.ROLE and r.role.command == "/日报" and r.body == "今天完成登录"
    assert route("/daily done", cfg).role.command == "/日报"
    assert route("／日报今天完成", cfg).body == "今天完成"  # full-width slash, no space for CJK command


def test_route_ascii_needs_boundary(cfg):
    assert route("/bugfix x", cfg).kind is Kind.UNKNOWN
    assert route("/bug 登录白屏", cfg).role.command == "/bug"
    assert route("/bug", cfg).body == ""


def test_route_builtin(cfg):
    assert route("/help", cfg).kind is Kind.HELP
    assert route("/取消", cfg).kind is Kind.CANCEL
    assert route("/cancel", cfg).kind is Kind.CANCEL
    assert route("你好", cfg).kind is Kind.PLAIN
    r = route("/foo bar", cfg)
    assert r.kind is Kind.UNKNOWN and r.command == "/foo"


def test_clean_text_strips_bot_and_renders_people():
    ms = [Mention("@_user_1", "Bot", "ou_bot"), Mention("@_user_2", "张三", "ou_z")]
    text, people = clean_text("@_user_1 /bug 登录挂了 负责人 @_user_2", ms, None)
    assert text == "/bug 登录挂了 负责人 @张三"
    assert people == {"张三": "ou_z"}
    # explicit bot id; bot mention not leading
    text, people = clean_text("@_user_2 @_user_1 hi", ms, "ou_bot")
    assert text == "@张三 hi" and people == {"张三": "ou_z"}


def test_clean_text_only_first_leading_mention_is_bot():
    ms = [Mention("@_user_1", "Bot", "ou_bot"), Mention("@_user_2", "张三", "ou_z")]
    text, people = clean_text("@_user_1 @_user_2 /bug x", ms, None)
    assert text == "@张三 /bug x" and people == {"张三": "ou_z"}


def test_clean_text_placeholder_prefix_collision():
    ms = [Mention("@_user_1", "Bot", "ou_bot")] + [Mention(f"@_user_{i}", f"P{i}", f"ou_{i}") for i in range(2, 11)]
    text, _ = clean_text("@_user_1 a @_user_10 b @_user_2", ms, None)
    assert text == "a @P10 b @P2"


def test_extract_text_post():
    content = json.dumps(
        {"zh_cn": {"title": "T", "content": [[{"tag": "text", "text": "/bug "}, {"tag": "a", "text": "x"}]]}}
    )
    assert extract_text("post", content) == "T\n/bug x"
    assert extract_text("text", "not json") == ""
    assert extract_text("image", "{}") == ""


def test_help_text_filters_roles(cfg):
    cfg.roles[1].allowed_chats = ["oc_other"]
    h = help_text(cfg, "oc_1")
    assert "/日报" in h and "/bug" not in h
