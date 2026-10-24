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


_HOOKS: list = []


def add_log_hook(fn) -> None:
    """Register a processor (e.g. dashboard stats) that sees every already-redacted event."""
    _HOOKS.append(fn)


def _run_hooks(logger, method, event_dict):
    for fn in list(_HOOKS):
        event_dict = fn(logger, method, event_dict)
    return event_dict


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact,
            _run_hooks,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
    )
