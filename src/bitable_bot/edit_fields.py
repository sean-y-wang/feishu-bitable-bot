"""Parse free-text field edits during confirm-before-write (e.g. 「优先级改成 P0」「标题：xxx」)."""

from __future__ import annotations

import re
from typing import Any

from .config.models import FieldSpec, Role

_SKIP = frozenset({"跳过", "skip", "略过", "不用了", "暂无", "先跳过"})
_YES = frozenset({"是", "对", "确认", "好", "好的", "嗯", "y", "yes", "ok", "是的", "对的", "没错", "可以", "行"})

# 「优先级改成 P0」/「标题改为 xxx」/「标题：xxx」/「标题: xxx」
_EDIT_RE = re.compile(
    r"^\s*(?P<field>[^：:改]{1,40}?)\s*(?:改成|改为|改為|改成了|改|：|:)\s*(?P<value>.+?)\s*$",
    re.DOTALL,
)


def is_yes(text: str) -> bool:
    return text.strip().strip("。.!！~").lower() in _YES


def is_skip(text: str) -> bool:
    return text.strip().strip("。.!！~").lower() in _SKIP


def _match_spec(role: Role, name: str) -> FieldSpec | None:
    t = name.strip().strip("「」“”\"' ")
    if not t:
        return None
    for f in role.fields:
        if t == f.field or t == f.key or t.casefold() == f.key.casefold():
            return f
    hits = [f for f in role.fields if f.field in t or f.key in t]
    return hits[0] if len(hits) == 1 else None


def parse_edits(role: Role, text: str) -> dict[str, Any] | None:
    """Return {key: raw_value} if the message looks like one or more field edits; else None.

    Supports a single edit, or several lines / 「；」-separated edits.
    """
    chunks = re.split(r"[\n;；]+", text.strip())
    out: dict[str, Any] = {}
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _EDIT_RE.match(chunk)
        if not m:
            return None
        spec = _match_spec(role, m.group("field"))
        if spec is None:
            return None
        out[spec.key] = m.group("value").strip()
    return out or None
