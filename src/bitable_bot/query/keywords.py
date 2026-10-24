"""Keyword extraction for progress query (rule-based; LLM keywords preferred when present)."""

from __future__ import annotations

import re

_STOP = frozenset(
    {
        "进展",
        "怎样了",
        "怎么样了",
        "怎么样",
        "怎样",
        "如何",
        "上线了吗",
        "上线了没",
        "上线了",
        "上线",
        "查一下",
        "查下",
        "查询",
        "查查",
        "找一下",
        "搜一下",
        "有没有做过",
        "有没有提过",
        "有没有",
        "状态如何",
        "做到哪了",
        "请问",
        "帮忙",
        "帮我",
        "一下",
        "这个",
        "那个",
        "什么",
        "吗",
        "呢",
        "的",
        "了",
        "啊",
        "呀",
        "吧",
        "是",
        "在",
        "和",
        "与",
        "或",
        "需求",
        "记录",
        "看看",
        "我的",
        "我提的",
        "我提交的",
        "我登记的",
        "查我的",
    }
)

_OWN_MARKERS = (
    "我的需求",
    "我提的",
    "我提交的",
    "我登记的",
    "我的进展",
    "查我的",
)


def is_own_progress_query(text: str) -> bool:
    """True when the user is asking about their own submitted requirements."""
    t = (text or "").strip()
    if not t:
        return False
    if any(m in t for m in _OWN_MARKERS):
        return True
    if "我的" in t and any(h in t for h in ("进展", "怎样", "如何", "状态", "上线", "查")):
        return True
    return False


def extract_keywords(text: str, llm_keywords: list[str] | None = None, limit: int = 5) -> list[str]:
    if llm_keywords:
        out = [k for k in llm_keywords if k and k not in _STOP]
        if out:
            return out[:limit]
    # drop hint phrases then split on punctuation / whitespace
    t = text
    for s in sorted(_STOP, key=len, reverse=True):
        t = t.replace(s, " ")
    parts = re.split(r"[\s,，、;；/|]+", t)
    out: list[str] = []
    for p in parts:
        p = p.strip("？?！!。.·…~～「」“”'\"()（）[]")
        if len(p) < 2 or p in _STOP:
            continue
        if p not in out:
            out.append(p)
        if len(out) >= limit:
            break
    return out or ([text.strip()[:40]] if text.strip() and not is_own_progress_query(text) else [])
