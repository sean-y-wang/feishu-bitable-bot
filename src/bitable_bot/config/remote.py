"""Pull business role config from a Feishu Bitable; fall back to local YAML on failure."""

from __future__ import annotations

import json
from typing import Any

import structlog

from .models import AppConfig, ChatFieldBinding, RemoteConfig

log = structlog.get_logger(__name__)

# Keys that may be overwritten from the remote table (never secrets / target tokens / providers).
_GLOBAL_TEXT = {"first_guide_text", "query_docs_hint", "bot_name"}
_GLOBAL_BOOL = {"first_guide_enabled"}
_ROLE_TEXT = {"system_prompt", "guide_prompt", "reply_style", "receipt_title"}
_ROLE_INT = {"min_content_chars"}
_ROLE_BOOL = {"confirm_before_write", "queryable"}


def cell_text(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list) and val and isinstance(val[0], dict):
        if "text" in val[0]:
            return "".join(str(x.get("text") or "") for x in val)
        if "name" in val[0]:
            return "、".join(str(x.get("name") or "") for x in val)
    return str(val)


def cell_bool(val: Any, default: bool = True) -> bool:
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        return val
    s = cell_text(val).strip().lower()
    if s in ("0", "false", "no", "否", "关闭", "禁用", "n"):
        return False
    if s in ("1", "true", "yes", "是", "开启", "启用", "y"):
        return True
    return default


def _field(row: dict[str, Any], *names: str) -> Any:
    fields = row.get("fields") or row
    if not isinstance(fields, dict):
        return None
    for n in names:
        if n in fields and fields[n] not in (None, ""):
            return fields[n]
    # case-insensitive / trim match
    lower = {str(k).strip().lower(): v for k, v in fields.items()}
    for n in names:
        v = lower.get(n.lower())
        if v not in (None, ""):
            return v
    return None


def parse_config_rows(
    rows: list[dict[str, Any]], remote: RemoteConfig
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return (global_overrides, role_overrides_by_command, chat_binding_dicts)."""
    global_ov: dict[str, Any] = {}
    role_ov: dict[str, dict[str, Any]] = {}
    bindings: list[dict[str, Any]] = []
    kf, vf, rf, ef = remote.key_field, remote.value_field, remote.role_field, remote.enabled_field
    for row in rows:
        key = cell_text(_field(row, kf, "config_key", "配置键", "键")).strip()
        if not key:
            continue
        if not cell_bool(_field(row, ef, "enabled", "启用"), default=True):
            continue
        raw_val = _field(row, vf, "config_value", "配置值", "值")
        value = cell_text(raw_val)
        role_cmd = cell_text(_field(row, rf, "role_command", "角色命令", "角色")).strip() or None

        if key in ("chat_binding", "chat_field_binding"):
            try:
                obj = json.loads(value) if value.strip().startswith("{") else {}
            except json.JSONDecodeError:
                log.warning("remote_config.bad_binding_json", value=value[:120])
                continue
            if role_cmd and "role_command" not in obj:
                obj["role_command"] = role_cmd
            bindings.append(obj)
            continue

        if key.startswith("field.") and role_cmd:
            # field.<key>.description / required / default
            parts = key.split(".", 2)
            if len(parts) == 3:
                _, fkey, attr = parts
                bucket = role_ov.setdefault(role_cmd, {}).setdefault("_fields", {})
                bucket.setdefault(fkey, {})[attr] = value if attr != "required" else cell_bool(raw_val, False)
            continue

        target = role_ov.setdefault(role_cmd, {}) if role_cmd else global_ov
        if key in _GLOBAL_TEXT or key in _ROLE_TEXT:
            if (role_cmd and key in _ROLE_TEXT) or (not role_cmd and key in _GLOBAL_TEXT):
                target[key] = value
            elif role_cmd and key in _GLOBAL_TEXT:
                # allow bot_name only global; ignore misplaced
                pass
            elif not role_cmd and key in _ROLE_TEXT:
                # role text without role_command → apply to all roles later via "*"
                role_ov.setdefault("*", {})[key] = value
        elif key in _GLOBAL_BOOL or key in _ROLE_BOOL:
            b = cell_bool(raw_val)
            if not role_cmd and key in _GLOBAL_BOOL:
                global_ov[key] = b
            elif role_cmd and key in _ROLE_BOOL:
                target[key] = b
            elif not role_cmd and key in _ROLE_BOOL:
                role_ov.setdefault("*", {})[key] = b
        elif key in _ROLE_INT:
            try:
                n = int(float(value))
            except (TypeError, ValueError):
                continue
            (role_ov.setdefault(role_cmd or "*", {}))[key] = n
        else:
            log.info("remote_config.unknown_key", key=key)

    return global_ov, role_ov, bindings


def merge_remote_into_config(
    cfg: AppConfig, rows: list[dict[str, Any]]
) -> AppConfig:
    """Overlay business fields from table rows onto a loaded AppConfig. Secrets untouched."""
    global_ov, role_ov, binding_dicts = parse_config_rows(rows, cfg.remote_config)
    data = cfg.model_dump()
    for k, v in global_ov.items():
        if k in data:
            data[k] = v

    # Merge chat bindings: remote appends (YAML first, then remote; resolve_chat_bindings de-dupes by field_key)
    extra_bindings: list[dict[str, Any]] = []
    for bd in binding_dicts:
        try:
            ChatFieldBinding.model_validate(bd)
            extra_bindings.append(bd)
        except Exception as e:
            log.warning("remote_config.bad_binding", error=str(e)[:160], raw=str(bd)[:160])
    if extra_bindings:
        data["chat_field_bindings"] = list(data.get("chat_field_bindings") or []) + extra_bindings

    star = role_ov.pop("*", {})
    for role in data["roles"]:
        cmd = role["command"]
        aliases = role.get("aliases") or []
        name = role.get("name") or ""
        patch: dict[str, Any] = dict(star)
        for key, ov in role_ov.items():
            if key in (cmd, name) or key in aliases:
                patch.update(ov)
        field_patches = patch.pop("_fields", {})
        for k, v in patch.items():
            if k in role:
                role[k] = v
        if field_patches:
            for f in role["fields"]:
                fp = field_patches.get(f["key"])
                if not fp:
                    continue
                for attr, val in fp.items():
                    if attr in f and attr in ("description", "required", "default"):
                        if attr == "required":
                            f[attr] = bool(val) if not isinstance(val, bool) else val
                        elif attr == "default":
                            f[attr] = val
                        else:
                            f[attr] = str(val) if val is not None else None

    return AppConfig.model_validate(data)


def fetch_remote_rows(api: Any, remote: RemoteConfig) -> list[dict[str, Any]]:
    return list(api.list_records(remote.app_token, remote.table_id, page_size=100) or [])


def apply_remote_config(cfg: AppConfig, api: Any | None) -> AppConfig:
    """Best-effort remote overlay. On any failure, return original cfg and log a warning."""
    if not cfg.remote_config.enabled:
        return cfg
    if api is None:
        log.warning("remote_config.skipped", reason="no api")
        return cfg
    try:
        rows = fetch_remote_rows(api, cfg.remote_config)
        merged = merge_remote_into_config(cfg, rows)
        log.info(
            "remote_config.applied",
            rows=len(rows),
            app_token=cfg.remote_config.app_token[:8] + "…",
            table_id=cfg.remote_config.table_id,
        )
        return merged
    except Exception as e:
        log.warning("remote_config.failed", error=str(e)[:300])
        return cfg
