"""Structured JSON-line logging to a configurable absolute path.

The log path is resolved at import time to be robust to stdio's CWD inheritance.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


def _default_log_path() -> Path:
    return Path.home() / ".flights-mcp" / "logs" / "flight-search.log"


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            payload.update(record.extra_fields)  # type: ignore[attr-defined]
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

    logger = logging.getLogger("flights_mcp")
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(JsonLineFormatter())
    logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, msg: str, **fields) -> None:
    """Emit a structured log line with arbitrary extra fields."""
    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 0, msg, (), None
    )
    record.extra_fields = fields  # type: ignore[attr-defined]
    logger.handle(record)
