"""Long-connection (WebSocket) gateway. Handlers return immediately; work runs in a thread pool."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog

from ..service import BotService
from .events import to_incoming

log = structlog.get_logger(__name__)


class Gateway:
    def __init__(self, service: BotService, workers: int = 4) -> None:
        self.service = service
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bot")

    def on_message(self, data: Any) -> None:
        """Registered for im.message.receive_v1. Must return within 3 seconds."""
        try:
            msg = to_incoming(data)
        except Exception:
            log.exception("event.parse_failed")
            return
        log.info("event.received", event_id=msg.event_id, message_id=msg.message_id, chat_id=msg.chat_id)
        self.pool.submit(self.service.handle, msg)

    def on_bot_added(self, data: Any) -> None:  # optional event: im.chat.member.bot.added_v1
        return None

    def build_handler(self):
        import lark_oapi as lark

        return lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(self.on_message).build()

    def run(self, app_id: str, app_secret: str, domain: str, log_level: Any = None) -> None:
        import lark_oapi as lark

        client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=self.build_handler(),
            domain=domain,
            log_level=log_level or lark.LogLevel.INFO,
        )
        client.start()  # blocks, auto-reconnects
