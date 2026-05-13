"""Structured JSON-line logging to a configurable absolute path.

The log path must be absolute to be robust to stdio's CWD inheritance.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


def _default_log_path() -> Path:
    return Path.home() / ".trip-search-mcp" / "logs" / "trip-search.log"


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = getattr(record, "extra_fields", {}) or {}
        payload: dict = dict(extras)
        payload["ts"] = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        payload["level"] = record.levelname
        payload["logger"] = record.name
        payload["msg"] = record.getMessage()
        payload.pop("exc", None)  # user-supplied "exc" cannot impersonate a traceback
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> logging.Logger:
    raw_path = os.environ.get("LOG_FILE_PATH")
    log_path = Path(raw_path) if raw_path else _default_log_path()
    if not log_path.is_absolute():
        raise ValueError(
            f"LOG_FILE_PATH must be an absolute path, got {log_path!r}. "
            "stdio inherits CWD from the MCP client and relative paths are unpredictable."
        )
    log_path.parent.mkdir(parents=True, exist_ok=True)

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger("trip_search_mcp")
    logger.setLevel(level)
    logger.propagate = False
    # Once a handler is attached, LOG_FILE_PATH changes are ignored for the lifetime of the process.
    if logger.handlers:
        return logger

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(JsonLineFormatter())
    logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, msg: str, *, level: int = logging.INFO, **fields) -> None:
    """Emit a structured log line with arbitrary extra fields.

    `level` is checked against the logger's effective level. `fields` are merged
    into the JSON payload; core fields (ts, level, logger, msg, exc) win over
    user-supplied fields with the same name.
    """
    if not logger.isEnabledFor(level):
        return
    record = logger.makeRecord(
        logger.name, level, __file__, 0, msg, (), None
    )
    record.extra_fields = fields  # type: ignore[attr-defined]
    logger.handle(record)
