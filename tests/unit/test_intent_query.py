"""Intent action=record|query and role prompt injection."""

from datetime import UTC, datetime

from bitable_bot.extractor.intent import classify, looks_like_help, looks_like_query
from bitable_bot.extractor.prompt import build_messages
from bitable_bot.query.keywords import extract_keywords, is_own_progress_query
from bitable_bot.query.summarize import _project

from ..conftest import NOW, FakeProvider


def test_looks_like_query(cfg):
    assert looks_like_query("Acme API 上线了吗", cfg.intent.query_hints)
    assert not looks_like_query("今天完成了登录模块", cfg.intent.query_hints)


def test_classify_query_action(cfg):
    p = FakeProvider(
        [
            {
                "action": "query",
                "role": "/bug",
                "confidence": 0.9,
                "query": "登录页白屏",
                "keywords": ["登录页", "白屏"],
                "reason": "问进展",
            }
        ]
    )
    intent = classify(p, cfg, cfg.roles, "登录页白屏进展怎样了")
    assert intent.action == "query" and intent.role.command == "/bug"
    assert intent.keywords == ["登录页", "白屏"] and intent.query == "登录页白屏"


def test_classify_record_compat_without_action(cfg):
    p = FakeProvider([{"role": "/bug", "confidence": 0.95, "reason": "登记"}])
    intent = classify(p, cfg, cfg.roles, "登录页白屏 P0")
    assert intent.action == "record" and intent.role.command == "/bug"


def test_system_prompt_injected(cfg):
    role = cfg.find_role("/bug")
    msgs = build_messages(role, "白屏", now=datetime.fromtimestamp(NOW, UTC), tz=cfg.timezone)
    assert "【本角色补充指令】" in msgs[0]["content"]
    assert "登记缺陷" in msgs[0]["content"]
    assert "【回复风格】" in msgs[0]["content"]


def test_extract_keywords_strips_hints():
    assert "进展" not in extract_keywords("Acme API 进展怎样了")
    assert "Acme" in " ".join(extract_keywords("Acme API 进展怎样了")) or "API" in extract_keywords(
        "Acme API 进展怎样了"
    )


def test_project_reply_fields_only(cfg):
    role = cfg.find_role("/bug")
    raw = {
        "record_id": "rec1",
        "fields": {"标题": "白屏", "严重程度": "P1", "原始消息": "secret", "上下文摘录": "nope"},
    }
    # /bug example has no 原始消息 column name in fields for raw_text with Chinese names
    # reply_fields = title, severity, owner, due — raw_text not included
    out = _project(role, raw)
    assert out["record_id"] == "rec1" and out.get("title") == "白屏"
    assert "原始消息" not in out and "secret" not in str(out)


def test_looks_like_help():
    assert looks_like_help("你可以干什么")
    assert looks_like_help("你是谁？")
    assert looks_like_help("hi")
    assert looks_like_help("在吗")
    assert not looks_like_help("明天下午三点配合集团演示")
    assert not looks_like_help("我的需求进展如何")


def test_classify_help_fast_path(cfg):
    p = FakeProvider([])  # must not be called
    intent = classify(p, cfg, cfg.roles, "你可以干什么")
    assert intent.action == "help" and not p.calls


def test_is_own_progress_query():
    assert is_own_progress_query("我的需求进展如何")
    assert is_own_progress_query("查一下我提的需求")
    assert not is_own_progress_query("SparkAudio 上线了吗")
