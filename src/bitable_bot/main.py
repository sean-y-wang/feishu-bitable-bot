"""Entrypoint: read env, load config, wire components, start the long connection."""

from __future__ import annotations

import os
import sys
import threading

import structlog

from .config import ConfigError, load_config
from .context import ContextCollector
from .extractor import Extractor
from .logging_setup import setup_logging
from .providers.openai_compat import build_providers
from .query import QueryService
from .service import BotService
from .session import Store
from .writer import BitableWriter, LarkFeishuAPI

log = structlog.get_logger(__name__)

DOMAINS = {
    "feishu": ("https://open.feishu.cn", "https://feishu.cn", "applink.feishu.cn"),
    "lark": ("https://open.larksuite.com", "https://larksuite.com", "applink.larksuite.com"),
}


class Settings:
    def __init__(self, env: dict[str, str]) -> None:
        missing = [k for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET") if not env.get(k)]
        if missing:
            raise ConfigError(f"missing required environment variables: {', '.join(missing)}")
        self.app_id = env["FEISHU_APP_ID"]
        self.app_secret = env["FEISHU_APP_SECRET"]
        domain = env.get("FEISHU_DOMAIN", "feishu").lower()
        if domain not in DOMAINS:
            raise ConfigError("FEISHU_DOMAIN must be 'feishu' or 'lark'")
        self.open_domain, self.web_domain, self.applink_host = DOMAINS[domain]
        self.config_path = env.get("BOT_CONFIG", "config/roles.yaml")
        self.db_path = env.get("BOT_DB_PATH", "data/bot.db")
        self.bot_open_id = env.get("FEISHU_BOT_OPEN_ID") or None
        self.log_level = env.get("LOG_LEVEL", "INFO")
        self.workers = int(env.get("BOT_WORKERS", "4"))


def resolve_bot_open_id(api, configured: str | None) -> str | None:
    """Env override, else ask the platform (GET /open-apis/bot/v3/info, no extra scope needed)."""
    if configured:
        return configured
    try:
        return api.get_bot_open_id() or None
    except Exception as e:  # network / permission: fall back to the leading-mention heuristic
        log.warning("bot_info.unavailable", error=str(e)[:200])
        return None


def build_service(settings: Settings, cfg, lark_client) -> BotService:
    store = Store(settings.db_path)
    api = LarkFeishuAPI(lark_client)
    providers = build_providers(cfg)
    bot_id = resolve_bot_open_id(api, settings.bot_open_id)
    collector = ContextCollector(api, cfg, applink_host=settings.applink_host, bot_open_id=bot_id)
    extractor = Extractor(providers, cfg.llm.default)
    qsvc = QueryService(api, cfg, providers, cfg.llm.default, base_url=settings.web_domain)
    return BotService(
        cfg,
        api,
        store,
        extractor,
        BitableWriter(api, store, base_url=settings.web_domain),
        bot_open_id=bot_id,
        collector=collector,
        query_service=qsvc,
    )


def _sweeper(service: BotService, interval: float = 60.0) -> None:
    stop = threading.Event()
    while not stop.wait(interval):
        try:
            n = service.expire_sessions()
            service.store.purge_expired()
            if n:
                log.info("session.expired", count=n)
        except Exception:
            log.exception("sweeper.failed")


def main() -> int:
    try:
        settings = Settings(dict(os.environ))
        setup_logging(settings.log_level)
        cfg = load_config(settings.config_path)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    import lark_oapi as lark

    from .gateway import Gateway

    lark_client = (
        lark.Client.builder()
        .app_id(settings.app_id)
        .app_secret(settings.app_secret)
        .domain(settings.open_domain)
        .build()
    )
    service = build_service(settings, cfg, lark_client)
    threading.Thread(target=_sweeper, args=(service,), daemon=True, name="sweeper").start()
    log.info("bot.starting", roles=[r.command for r in cfg.roles], db=settings.db_path, bot_open_id=service.bot_open_id)
    Gateway(service, settings.workers).run(settings.app_id, settings.app_secret, settings.open_domain)
    return 0


if __name__ == "__main__":
    sys.exit(main())
