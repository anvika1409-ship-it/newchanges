"""Structured logging.

Emits the operational fields listed in AI_DEVELOPMENT_RULES.md section 27 and
refuses to emit the values that section forbids: API keys, passwords, bearer
tokens, cookies and raw secrets.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from app.core.config import Settings
from app.core.context import current_correlation

# Reserved LogRecord attributes; anything else is treated as structured extra.
_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)

_SECRET_KEY_HINTS = (
    "api_key", "apikey", "secret", "token", "password", "passwd",
    "authorization", "cookie", "credential", "private_key",
)

# Redacts `Bearer <token>` and `sk-...` style values that reach a log message.
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]+")
_APIKEY_RE = re.compile(r"\b(sk-[A-Za-z0-9._\-]{8,})")

REDACTED = "***redacted***"


def _redact_text(value: str) -> str:
    value = _BEARER_RE.sub(r"\1 " + REDACTED, value)
    return _APIKEY_RE.sub(REDACTED, value)


def _redact_value(key: str, value: Any) -> Any:
    if any(hint in key.lower() for hint in _SECRET_KEY_HINTS):
        return REDACTED
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {k: _redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(key, item) for item in value]
    return value


class RedactionFilter(logging.Filter):
    """Last line of defence against secrets reaching a log sink."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_text(record.msg)
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED:
                continue
            record.__dict__[key] = _redact_value(key, value)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update({k: v for k, v in current_correlation().items() if v is not None})

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            # Type and message only. Stack traces stay out of structured logs and
            # never reach the client (AI_DEVELOPMENT_RULES.md section 26).
            exc_type, exc_value, _ = record.exc_info
            payload["exception_type"] = getattr(exc_type, "__name__", str(exc_type))
            payload["exception_message"] = _redact_text(str(exc_value))

        return json.dumps(payload, default=str, separators=(",", ":"))


class ConsoleFormatter(logging.Formatter):
    """Human-readable local development format."""

    def __init__(self, service: str) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)s :: %(message)s")
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        correlation = {k: v for k, v in current_correlation().items() if v is not None}
        if correlation:
            base += " " + " ".join(f"{k}={v}" for k, v in correlation.items())
        return base


def configure_logging(settings: Settings) -> None:
    """Install the root logging configuration. Safe to call more than once."""
    formatter: logging.Formatter = (
        JsonFormatter(settings.app_name)
        if settings.log_format == "json"
        else ConsoleFormatter(settings.app_name)
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # Uvicorn writes its own access log; ours carries the correlation fields.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("uvicorn.error").propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
