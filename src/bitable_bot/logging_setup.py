"""JSON structured logs to stdout with secret redaction."""

from __future__ import annotations

import logging
import re

import structlog

_SECRET_KEYS = re.compile(r"(secret|token|api_key|password|authorization)", re.I)


def _redact(_, __, event_dict):
    for k in list(event_dict):
        if _SECRET_KEYS.search(k) and k != "event":
            event_dict[k] = "***"
    return event_dict


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
    )
