"""Bitable keyword search + recent-records fallback + proposer bias."""

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


def _proposer_field_name(role: Role) -> str | None:
    spec = role.field_by_key("proposer")
    return spec.field if spec else None


def _cell_text(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list) and val and isinstance(val[0], dict):
        if "text" in val[0]:
            return "".join(str(x.get("text") or "") for x in val)
        if "name" in val[0]:
            return "、".join(str(x.get("name") or x.get("id") or "") for x in val)
    return str(val)


def _proposer_of(row: dict[str, Any], field_name: str) -> str:
    return _cell_text((row.get("fields") or {}).get(field_name))


def _matches_proposer(row: dict[str, Any], field_name: str, proposer_name: str) -> bool:
    cell = _proposer_of(row, field_name)
    if not cell or not proposer_name:
        return False
    return proposer_name == cell or proposer_name in cell or cell in proposer_name


def rank_by_proposer(
    candidates: list[dict[str, Any]], proposer_name: str, field_name: str | None
) -> list[dict[str, Any]]:
    """Stable partition: rows whose 提出人 matches sender first."""
    if not proposer_name or not field_name or not candidates:
        return candidates
    own: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for c in candidates:
        (own if _matches_proposer(c, field_name, proposer_name) else other).append(c)
    return own + other


def search_candidates(
    api: FeishuAPI,
    role: Role,
    keywords: list[str],
    *,
    proposer_name: str = "",
    prefer_own: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """Return (candidates, source) where source is 'own'|'search'|'fallback'|'empty'."""
    q = role.query
    col_names = []
    for key in q.search_fields:
        spec = role.field_by_key(key)
        if spec:
            col_names.append(spec.field)
    prop_field = _proposer_field_name(role)
    items: list[dict[str, Any]] = []

    # Prefer rows where 提出人 matches the consulting user (e.g. 「我的需求进展」).
    if prefer_own and proposer_name and prop_field:
        try:
            own_filt = {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": prop_field,
                        "operator": "contains",
                        "value": [proposer_name],
                    }
                ],
            }
            items = api.search_records(
                role.target.app_token, role.target.table_id, own_filt, page_size=q.max_candidates
            )
            if keywords and col_names and items:
                # Soft filter by keywords; keep all own rows if none match.
                narrowed = []
                for row in items:
                    fields = row.get("fields") or {}
                    blob = " ".join(_cell_text(fields.get(n)) for n in col_names)
                    if any(kw and kw in blob for kw in keywords):
                        narrowed.append(row)
                if narrowed:
                    items = narrowed
            if items:
                return items[: q.max_candidates], "own"
        except FeishuAPIError as e:
            log.warning("query.own_search_failed", code=e.code, msg=e.msg)
            items = []

    try:
        filt = _contains_filter(col_names, keywords)
        if filt:
            items = api.search_records(role.target.app_token, role.target.table_id, filt, page_size=q.max_candidates)
    except FeishuAPIError as e:
        log.warning("query.search_failed", code=e.code, msg=e.msg)
        items = []
    if items:
        ranked = rank_by_proposer(items, proposer_name, prop_field)
        return ranked[: q.max_candidates], "search"

    try:
        items = api.list_records(role.target.app_token, role.target.table_id, page_size=q.fallback_limit)
    except FeishuAPIError as e:
        log.warning("query.list_failed", code=e.code, msg=e.msg)
        return [], "empty"
    if not items:
        return [], "empty"
    ranked = rank_by_proposer(items, proposer_name, prop_field)
    if prefer_own and proposer_name and prop_field:
        own = [r for r in ranked if _matches_proposer(r, prop_field, proposer_name)]
        if own:
            return own[: q.fallback_limit], "own_fallback"
    return ranked[: q.fallback_limit], "fallback"
