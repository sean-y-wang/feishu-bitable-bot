from datetime import UTC, datetime

import pytest

from bitable_bot.config.models import FieldSpec, Option
from bitable_bot.validator import Context, Missing, match_option, normalize_value, validate
from bitable_bot.validator.normalize import edit_distance, parse_cn_number

from ..conftest import NOW

DAY_1023 = 1792684800000  # 2026-10-23 00:00 Asia/Shanghai, ms


def ctx(**kw):
    base = dict(sender_open_id="ou_me", now=datetime.fromtimestamp(NOW, UTC), sender_name="我",
                mentions={"张三": "ou_z"})
    base.update(kw)
    return Context(**base)


def spec(type_, **kw):
    return FieldSpec(key="k", field="F", type=type_, **kw)


STATUS = [Option(value="正常", synonyms=["顺利", "ok"]), Option(value="延期", synonyms=["delay"]),
          Option(value="阻塞", synonyms=["卡住", "blocked"])]


@pytest.mark.parametrize("raw,expected", [
    ("正常", "正常"), ("顺利", "正常"), ("OK", "正常"), ("ＯＫ", "正常"), ("Delay", "延期"),
    ("blockd", "阻塞"), ("完全不相关", None), ("", None),
])
def test_match_option(raw, expected):
    assert match_option(raw, STATUS) == expected


def test_match_option_ambiguous_fuzzy():
    opts = [Option(value="ab1"), Option(value="ab2")]
    assert match_option("ab3", opts) is None


@pytest.mark.parametrize("raw,expected", [
    (3.5, 3.5), ("3.5h", 3.5), ("三点五", 3.5), ("八", 8), ("十二", 12), ("两个半", 2.5), ("3个半小时", 3.5),
    ("1,024", 1024), ("负一", -1), ("三十五", 35), ("一百零五", 105), ("一万二千", 12000),
])
def test_number(raw, expected):
    assert normalize_value(spec("number"), raw, ctx()) == expected


def test_number_range_precision():
    s = spec("number", min=0, max=24, precision=1)
    assert normalize_value(s, "7.25", ctx()) == 7.2  # banker's rounding is fine for precision
    with pytest.raises(Missing):
        normalize_value(s, 25, ctx())
    with pytest.raises(Missing):
        normalize_value(s, -1, ctx())
    for bad in ("abc", True):
        with pytest.raises(Missing):
            normalize_value(s, bad, ctx())


@pytest.mark.parametrize("raw,expected", [
    ("2026-10-23", DAY_1023), ("2026年10月23日", DAY_1023), ("2026/10/23", DAY_1023),
    ("今天", DAY_1023), ("昨天", DAY_1023 - 86400000), ("明天", DAY_1023 + 86400000),
    (DAY_1023, DAY_1023), (DAY_1023 // 1000, DAY_1023),
])
def test_date(raw, expected):
    assert normalize_value(spec("date"), raw, ctx()) == expected


def test_date_invalid():
    with pytest.raises(Missing):
        normalize_value(spec("date"), "不知道哪天", ctx())


def test_user_resolution():
    s = spec("user")
    c = ctx(member_lookup=lambda n: {"李四": ["ou_l"], "王五": ["ou_w1", "ou_w2"]}.get(n, []))
    assert normalize_value(s, ["ou_x"], c) == [{"id": "ou_x"}]
    assert normalize_value(s, "@张三", c) == [{"id": "ou_z"}]
    assert normalize_value(s, ["我"], c) == [{"id": "ou_me"}]
    assert normalize_value(s, ["李四", {"name": "张三"}], c) == [{"id": "ou_l"}, {"id": "ou_z"}]
    with pytest.raises(Missing, match="多位"):
        normalize_value(s, "王五", c)
    with pytest.raises(Missing, match="找不到"):
        normalize_value(s, "赵六", c)
    with pytest.raises(Missing):
        normalize_value(s, [""], c)


def test_text_url_phone_checkbox_select():
    assert normalize_value(spec("text", max_len=3), "  abcdef ", ctx()) == "abc"
    assert normalize_value(spec("text"), ["a", "b"], ctx()) == "a\nb"
    assert normalize_value(spec("url"), "example.com/x", ctx()) == {"text": "https://example.com/x",
                                                                    "link": "https://example.com/x"}
    assert normalize_value(spec("url"), {"link": "http://a.b"}, ctx())["link"] == "http://a.b"
    with pytest.raises(Missing):
        normalize_value(spec("url"), "javascript:alert(1)", ctx())
    assert normalize_value(spec("phone"), "138-0000 0000", ctx()) == "13800000000"
    with pytest.raises(Missing):
        normalize_value(spec("phone"), "abc", ctx())
    assert normalize_value(spec("checkbox"), "是", ctx()) is True
    assert normalize_value(spec("checkbox"), False, ctx()) is False
    with pytest.raises(Missing):
        normalize_value(spec("checkbox"), "也许", ctx())
    ms = spec("multi_select", options=[Option(value="前端"), Option(value="后端", synonyms=["服务端"])])
    assert normalize_value(ms, "前端、服务端，前端", ctx()) == ["前端", "后端"]
    with pytest.raises(Missing):
        normalize_value(ms, ["运维"], ctx())
    with pytest.raises(Missing):
        normalize_value(ms, [""], ctx())
    ss = spec("single_select", options=STATUS)
    assert normalize_value(ss, ["卡住"], ctx()) == "阻塞"
    with pytest.raises(Missing, match="可选值"):
        normalize_value(ss, "未知", ctx())


def test_validate_defaults_and_missing(cfg):
    role = cfg.find_role("/日报")
    r = validate(role.fields, {"done": "登录模块", "hours": "八", "tags": ["server"], "link": "bad url"}, ctx())
    assert r.complete
    assert r.values["reporter"] == [{"id": "ou_me"}]
    assert r.values["date"] == DAY_1023
    assert r.values["status"] == "正常"
    assert r.values["hours"] == 8
    assert r.values["tags"] == ["后端"]
    assert "link" not in r.values and "plan" not in r.values
    r2 = validate(role.fields, {"status": "随便"}, ctx())
    assert set(r2.missing) == {"done", "status"}
    assert "可选值" in r2.missing["status"]


def test_validate_bad_default_reports_missing():
    s = FieldSpec(key="d", field="D", type="date", required=True, default="not-a-date")
    r = validate([s], {}, ctx())
    assert "d" in r.missing


def test_helpers():
    assert edit_distance("kitten", "sitting") == 3
    assert parse_cn_number("零点五") == 0.5


def test_defaults_variables():
    from bitable_bot.validator.normalize import resolve_default

    c = ctx(chat_name="群", message_link="https://l")
    assert resolve_default(FieldSpec(key="a", field="a", type="text", default="$chat_name"), c) == "群"
    assert resolve_default(FieldSpec(key="a", field="a", type="url", default="$message_link"), c) == "https://l"
    assert resolve_default(FieldSpec(key="a", field="a", type="date", default="$now"), c) == int(NOW * 1000)
    assert resolve_default(FieldSpec(key="a", field="a", type="text", default="x"), c) == "x"
