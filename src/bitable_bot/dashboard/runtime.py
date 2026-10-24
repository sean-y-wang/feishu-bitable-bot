"""Shared runtime state: stats, WS status, hot reload of business config, system-config edits."""

from __future__ import annotations

import collections
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import structlog
import yaml

from ..config import ConfigError, load_config
from ..config import remote as remote_mod
from ..config.models import AppConfig
from ..config.remote import fetch_remote_rows, merge_remote_into_config
from ..providers.openai_compat import build_providers

log = structlog.get_logger(__name__)

_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# Always-editable secrets besides the ${VAR}s referenced by llm.providers.*.api_key
_BASE_SECRETS = ("FEISHU_APP_SECRET",)
# Event names counted for the overview (from structlog).
_COUNTED = {
    "llm.fallback": "llm_fallback",
    "llm.provider_failed": "llm_provider_failed",
    "intent.low_conf_guide": "guide",
    "help.reply": "guide",
}


def mask_secret(value: str | None) -> str:
    """sk-abcdef123456 → sk-****3456. Never returns the full value."""
    if not value:
        return ""
    v = str(value)
    if len(v) < 10:
        return "****"
    return f"{v[:3]}****{v[-4:]}"


def today_start(now: float | None = None) -> float:
    t = time.localtime(now or time.time())
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


class Runtime:
    """Everything the dashboard reads/changes. Thread-safe; lives for the whole process."""

    def __init__(
        self,
        settings: Any,
        service: Any,
        cfg: AppConfig,
        *,
        env_path: str = ".env",
        audit_path: str | None = None,
        error_buffer: int = 30,
        clock=time.time,
    ) -> None:
        self.settings = settings
        self.service = service
        self.cfg = cfg
        self.env_path = Path(env_path)
        self.audit_path = Path(audit_path or Path(settings.db_path).parent / "dashboard_audit.log")
        self.clock = clock
        self.started_at = clock()
        self._lock = threading.RLock()
        self.counters: collections.Counter[str] = collections.Counter()
        self.counter_day = today_start(self.started_at)
        self.errors: collections.deque[dict[str, Any]] = collections.deque(maxlen=error_buffer)
        self.ws: dict[str, Any] = {"state": "starting", "since": self.started_at, "detail": ""}
        rc = cfg.remote_config
        self.config_load: dict[str, Any] = {
            "source": "remote" if rc.enabled else "local",
            "at": self.started_at,
            "ok": True,
            "rows": None,
            "error": "",
            "trigger": "startup",
        }
        if rc.enabled and remote_mod.LAST_LOAD:
            self.config_load.update(remote_mod.LAST_LOAD)
        self._stop = threading.Event()

    # ------------------------------------------------------------ observers
    def structlog_processor(self, _logger, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        """Count interesting events + keep recent errors. Runs after secret redaction."""
        try:
            ev = str(event_dict.get("event", ""))
            with self._lock:
                self._roll_day()
                key = _COUNTED.get(ev)
                if key:
                    self.counters[key] += 1
                if method in ("error", "exception", "critical") or event_dict.get("level") in ("error", "critical"):
                    self._push_error(ev, event_dict.get("error") or event_dict.get("msg") or "")
        except Exception:  # never break logging
            pass
        return event_dict

    def _push_error(self, ev: str, detail: Any) -> None:
        self.errors.appendleft({"at": self.clock(), "event": ev, "detail": str(detail)[:300]})

    def _roll_day(self) -> None:
        d = today_start(self.clock())
        if d != self.counter_day:
            self.counter_day = d
            self.counters.clear()

    def ws_log_handler(self) -> logging.Handler:
        rt = self

        class _H(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    m = record.getMessage()
                    st = None
                    if "connected to" in m and "disconnected" not in m:
                        st = "connected"
                    elif "disconnected" in m:
                        st = "disconnected"
                    elif "reconnect" in m:
                        st = "reconnecting"
                    elif "connect failed" in m:
                        st = "error"
                    if st:
                        with rt._lock:
                            rt.ws = {"state": st, "since": rt.clock(), "detail": re.sub(r"\?.*", "", m)[:200]}
                    if record.levelno >= logging.ERROR:
                        with rt._lock:
                            rt._push_error("lark.ws", re.sub(r"\?\S*", "", m))
                except Exception:
                    pass

        return _H(level=logging.INFO)

    # ------------------------------------------------------------ overview
    def today_counts(self) -> dict[str, int]:
        rec = qry = 0
        try:
            since = today_start(self.clock())
            for r in self.service.store.audit_rows():
                if (r.get("ts") or 0) < since:
                    continue
                cmd, res = str(r.get("command") or ""), str(r.get("result") or "")
                if cmd.startswith("query:"):
                    qry += 1
                elif res == "ok" and r.get("record_id"):
                    rec += 1
        except Exception:
            pass
        with self._lock:
            self._roll_day()
            c = dict(self.counters)
        return {
            "records": rec,
            "queries": qry,
            "guides": c.get("guide", 0),
            "llm_fallback": c.get("llm_fallback", 0),
            "llm_provider_failed": c.get("llm_provider_failed", 0),
        }

    def overview(self) -> dict[str, Any]:
        cfg = self.cfg
        now = self.clock()
        with self._lock:
            ws = dict(self.ws)
            load = dict(self.config_load)
            errors = list(self.errors)
        return {
            "bot_name": cfg.display_name(),
            "scenario": cfg.scenario,
            "dedicated": cfg.dedicated,
            "roles": [{"command": r.command, "name": r.name, "queryable": r.queryable} for r in cfg.roles],
            "role_count": len(cfg.roles),
            "bot_count": 1,
            "ws": ws,
            "started_at": self.started_at,
            "uptime_seconds": int(now - self.started_at),
            "config_path": str(self.settings.config_path),
            "config_load": load,
            "remote_config": {
                "enabled": cfg.remote_config.enabled,
                "table_id": cfg.remote_config.table_id,
                "refresh_seconds": cfg.remote_config.refresh_seconds,
            },
            "today": self.today_counts(),
            "llm": {
                "default": cfg.llm.default,
                "fallback": list(cfg.llm.fallback),
                "model": cfg.llm.providers[cfg.llm.default].model,
            },
            "errors": errors,
        }

    # ------------------------------------------------------------ system config (masked)
    def secret_names(self, cfg: AppConfig | None = None) -> list[str]:
        cfg = cfg or self.cfg
        names = list(_BASE_SECRETS)
        for p in self._raw_yaml().get("llm", {}).get("providers", {}).values():
            m = _ENV_REF.match(str((p or {}).get("api_key") or ""))
            if m and m.group(1) not in names:
                names.append(m.group(1))
        return names

    def _raw_yaml(self) -> dict[str, Any]:
        try:
            return yaml.safe_load(Path(self.settings.config_path).read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def system_config(self) -> dict[str, Any]:
        cfg = self.cfg
        raw_providers = self._raw_yaml().get("llm", {}).get("providers", {})
        providers = {}
        for name, p in cfg.llm.providers.items():
            ref = _ENV_REF.match(str((raw_providers.get(name) or {}).get("api_key") or ""))
            providers[name] = {
                "model": p.model,
                "base_url": p.base_url,
                "timeout": p.timeout,
                "fallback_models": list(p.fallback_models),
                "api_key_env": ref.group(1) if ref else None,
                "api_key_masked": mask_secret(p.api_key),
                "api_key_set": bool(p.api_key),
            }
        secrets = [
            {
                "name": n,
                "masked": mask_secret(os.environ.get(n)),
                "set": bool(os.environ.get(n)),
                "hot": n != "FEISHU_APP_SECRET",
            }
            for n in self.secret_names()
        ]
        return {
            "llm": {"default": cfg.llm.default, "fallback": list(cfg.llm.fallback), "providers": providers},
            "session": {"timeout_minutes": cfg.session.timeout_minutes, "max_followups": cfg.session.max_followups},
            "intent": {"min_confidence": cfg.intent.min_confidence, "enabled": cfg.intent.enabled},
            "context": {
                "recent_messages": cfg.context.recent_messages,
                "thread_messages": cfg.context.thread_messages,
                "max_chars": cfg.context.max_chars,
            },
            "remote_config": {"refresh_seconds": cfg.remote_config.refresh_seconds},
            "feishu": {
                "app_id": self.settings.app_id,
                "domain": self.settings.open_domain,
                "app_secret_masked": mask_secret(self.settings.app_secret),
            },
            "dashboard": {
                "host": os.environ.get("DASHBOARD_HOST", "127.0.0.1"),
                "port": os.environ.get("DASHBOARD_PORT", "8787"),
            },
            "secrets": secrets,
            "hot_reload": HOT_RELOAD,
        }

    # ------------------------------------------------------------ reload
    def build_config(self) -> tuple[AppConfig, int | None]:
        """Re-read YAML (+ env) and overlay the remote table. Raises on any failure."""
        cfg = load_config(self.settings.config_path)
        rows = None
        if cfg.remote_config.enabled:
            data = fetch_remote_rows(self.service.api, cfg.remote_config)
            rows = len(data)
            cfg = merge_remote_into_config(cfg, data)
        return cfg, rows

    def apply_config(self, cfg: AppConfig) -> None:
        """Hot-swap config into running components (atomic attribute swaps)."""
        providers = build_providers(cfg)
        s = self.service
        ex = s.extractor
        ex.providers, ex.default, ex.fallback = providers, cfg.llm.default, list(cfg.llm.fallback)
        if s.query is not None:
            s.query.providers, s.query.default, s.query.cfg = providers, cfg.llm.default, cfg
        if s.collector is not None:
            s.collector.cfg = cfg
        s.cfg = cfg
        self.cfg = cfg

    def reload(self, trigger: str = "manual") -> dict[str, Any]:
        with self._lock:
            try:
                cfg, rows = self.build_config()
                self.apply_config(cfg)
                self.config_load = {
                    "source": "remote" if cfg.remote_config.enabled else "local",
                    "at": self.clock(),
                    "ok": True,
                    "rows": rows,
                    "error": "",
                    "trigger": trigger,
                }
                log.info("dashboard.reload_ok", trigger=trigger, rows=rows)
            except Exception as e:
                err = str(e)[:300]
                self.config_load = {
                    **self.config_load,
                    "at": self.clock(),
                    "ok": False,
                    "error": err,
                    "trigger": trigger,
                }
                log.warning("dashboard.reload_failed", trigger=trigger, error=err)
            return dict(self.config_load)

    def start_auto_refresh(self) -> threading.Thread | None:
        def loop() -> None:
            while True:
                secs = self.cfg.remote_config.refresh_seconds if self.cfg.remote_config.enabled else 0
                if self._stop.wait(secs if secs > 0 else 60):
                    return
                if secs > 0 and self.cfg.remote_config.enabled:
                    self.reload("auto")

        t = threading.Thread(target=loop, daemon=True, name="config-refresh")
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ edits
    def audit(self, action: str, actor: str, **kw: Any) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "action": action, "actor": actor, **kw}
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("dashboard.audit_failed", error=str(e))
        log.info("dashboard.audit", action=action, actor=actor, keys=kw.get("keys"))

    def update_system(self, patch: dict[str, Any], actor: str = "dashboard") -> dict[str, Any]:
        """Patch whitelisted system keys in the YAML, validate, write (with .bak), hot-reload."""
        path = Path(self.settings.config_path)
        raw = self._raw_yaml()
        if not raw:
            raise ConfigError(f"cannot read {path}")
        changed: list[str] = []
        llm = patch.get("llm") or {}
        if "default" in llm:
            raw["llm"]["default"] = str(llm["default"])
            changed.append("llm.default")
        if "fallback" in llm:
            fb = llm["fallback"]
            if isinstance(fb, str):
                fb = [x.strip() for x in fb.split(",") if x.strip()]
            raw["llm"]["fallback"] = [str(x) for x in fb]
            changed.append("llm.fallback")
        for name, p in (llm.get("providers") or {}).items():
            if name not in raw["llm"].get("providers", {}):
                raise ConfigError(f"unknown provider {name!r}")
            tgt = raw["llm"]["providers"][name]
            if p.get("base_url") not in (None, "") and str(p["base_url"]) != str(tgt.get("base_url", "")):
                raise ConfigError(
                    f"llm.providers.{name}.base_url is restart-only; edit config/roles.yaml and restart"
                )
            for k, cast in (("model", str), ("timeout", float)):
                if k in p and p[k] not in (None, ""):
                    tgt[k] = cast(p[k])
                    changed.append(f"llm.providers.{name}.{k}")
            if "fallback_models" in p:
                fm = p["fallback_models"]
                if isinstance(fm, str):
                    fm = [x.strip() for x in fm.split(",") if x.strip()]
                tgt["fallback_models"] = list(fm)
                changed.append(f"llm.providers.{name}.fallback_models")
        for sect, keys in (
            ("session", {"timeout_minutes": int, "max_followups": int}),
            ("intent", {"min_confidence": float}),
            ("context", {"recent_messages": int, "thread_messages": int, "max_chars": int}),
            ("remote_config", {"refresh_seconds": int}),
        ):
            for k, cast in keys.items():
                v = (patch.get(sect) or {}).get(k)
                if v not in (None, ""):
                    raw.setdefault(sect, {})[k] = cast(v)
                    changed.append(f"{sect}.{k}")
        if not changed:
            return {"changed": [], "reload": dict(self.config_load)}
        text = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
        # validate before writing (env expansion applies)
        from ..config.loader import expand_env

        try:
            AppConfig.model_validate(expand_env(raw))
        except Exception as e:
            raise ConfigError(f"invalid config: {e}") from e
        with self._lock:
            if path.exists():
                path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            path.write_text(text, encoding="utf-8")
        self.audit("system.update", actor, keys=changed)
        return {"changed": changed, "reload": self.reload("system_update")}

    def update_secrets(self, values: dict[str, str], actor: str = "dashboard") -> dict[str, Any]:
        allowed = set(self.secret_names())
        bad = [k for k in values if k not in allowed or not _ENV_NAME.match(k)]
        if bad:
            raise ConfigError(f"not an editable secret: {', '.join(sorted(bad))}")
        vals = {k: str(v).strip() for k, v in values.items() if str(v).strip()}
        for k, v in vals.items():
            if "\n" in v or "\r" in v:
                raise ConfigError(f"{k}: value must be single-line")
        if not vals:
            return {"changed": [], "restart_required": []}
        with self._lock:
            write_env_file(self.env_path, vals)
            os.environ.update(vals)
        restart = [k for k in vals if k == "FEISHU_APP_SECRET"]
        self.audit("secrets.update", actor, keys=sorted(vals))
        res = self.reload("secrets_update")
        return {"changed": sorted(vals), "restart_required": restart, "reload": res}


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done: set[str] = set()
    out = []
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in values:
            out.append(f"{m.group(1)}={values[m.group(1)]}")
            done.add(m.group(1))
        else:
            out.append(line)
    for k, v in values.items():
        if k not in done:
            out.append(f"{k}={v}")
    text = "\n".join(out) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    except OSError:  # e.g. single-file bind mount in Docker: rewrite in place
        tmp.unlink(missing_ok=True)
        path.write_text(text, encoding="utf-8")


HOT_RELOAD = {
    "hot": [
        "业务配置（飞书「机器人配置」表：prompt / 引导文案 / 群绑定 / 字段说明）",
        "LLM 主模型、降级顺序、模型名、超时（base_url 只读，需改 roles.yaml 并重启）",
        "LLM 密钥（*_API_KEY）",
        "会话超时、意图置信度阈值、上下文条数",
        "自动刷新间隔 refresh_seconds（下一轮生效）",
    ],
    "restart": [
        "飞书 App ID / App Secret / FEISHU_DOMAIN（需重启进程以重连长连接）",
        "看板 DASHBOARD_HOST / DASHBOARD_PORT / DASHBOARD_TOKEN",
        "BOT_CONFIG / BOT_SCENARIO / BOT_DB_PATH / BOT_WORKERS / LOG_LEVEL",
        "LLM 供应商 base_url（看板只读；改 config/roles.yaml 后重启）",
        "角色结构（多维表格目标 app_token / table_id、字段定义）建议重启",
    ],
}
