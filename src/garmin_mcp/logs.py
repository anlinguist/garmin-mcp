"""Structured JSON logging to stdout (journald picks it up under systemd).

Rules: never log tokens, passwords, MFA codes or raw health payloads. Call
sites log event names and small metadata only; the redaction filter is a
backstop, not the primary control.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_REDACT_PATTERNS = [
    # Bearer / JWT-looking values
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
    # key=value style secrets in URLs or messages
    re.compile(
        r"(?i)((?:access_token|refresh_token|di_token|di_refresh_token|password|"
        r"client_secret|code|ticket|code_verifier|state)[\"']?\s*[=:]\s*[\"']?)[^&\s\"',}]+"
    ),
]

# Loggers from dependencies that may log request/response bodies at DEBUG.
_NOISY_LOGGERS = {
    "garminconnect": logging.WARNING,
    "garminconnect.client": logging.WARNING,
    "urllib3": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "curl_cffi": logging.WARNING,
    "uvicorn.access": logging.WARNING,
}


def redact(text: str) -> str:
    for pat in _REDACT_PATTERNS:
        if pat.groups:
            text = pat.sub(lambda m: m.group(1) + "<redacted>", text)
        else:
            text = pat.sub("<redacted>", text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update({k: redact(str(v)) if isinstance(v, str) else v for k, v in extra.items()})
        if record.exc_info and record.exc_info[0] is not None:
            # Type name only: exception text from HTTP libraries can embed URLs/bodies.
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name, lvl in _NOISY_LOGGERS.items():
        # Never let LOG_LEVEL=DEBUG turn on body logging in dependencies.
        logging.getLogger(name).setLevel(max(lvl, logging.getLevelName(level)))


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(level, event, extra={"fields": {"event": event, **fields}})
