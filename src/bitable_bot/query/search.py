"""Bitable keyword search + recent-records fallback."""

from __future__ import annotations

from typing import Any

import structlog

from ..config.models import Role
from ..writer.feishu_api import FeishuAPI, FeishuAPIError

log = structlog.get_logger(__name__)


def _contains_filter(field_names: list[str], keywords: list[str]) -> dict[str, Any] | None:
    """Build a Feishu filter: OR over (field contains kw) for each field×keyword."""
    conditions = []
    for name in field_names:
        for kw in keywords:
            if not kw:
                continue
            conditions.append(
                {
                    "field_name": name,
                    "operator": "contains",
                    "value": [kw],
                }
            )
    if not conditions:
        return None
    if len(conditions) == 1:
        return {"conjunction": "or", "conditions": conditions}
    return {"conjunction": "or", "conditions": conditions}


def search_candidates(api: FeishuAPI, role: Role, keywords: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Return (candidates, source) where source is 'search' | 'fallback' | 'empty'."""
    q = role.query
    col_names = []
    for key in q.search_fields:
        spec = role.field_by_key(key)
        if spec:
            col_names.append(spec.field)
    items: list[dict[str, Any]] = []
    try:
        filt = _contains_filter(col_names, keywords)
        if filt:
            items = api.search_records(role.target.app_token, role.target.table_id, filt, page_size=q.max_candidates)
    except FeishuAPIError as e:
        log.warning("query.search_failed", code=e.code, msg=e.msg)
        items = []
    if items:
        return items[: q.max_candidates], "search"
    try:
        items = api.list_records(role.target.app_token, role.target.table_id, page_size=q.fallback_limit)
    except FeishuAPIError as e:
        log.warning("query.list_failed", code=e.code, msg=e.msg)
        return [], "empty"
    return items[: q.fallback_limit], ("fallback" if items else "empty")
