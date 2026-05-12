"""Tool error contract — every failure path returns one of these codes."""
from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    NO_RESULTS = "no_results"
    INVALID_INPUT = "invalid_input"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"


class ToolError(Exception):
    """Raised internally, caught at the tool boundary, converted to error_response."""

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def error_response(code: ErrorCode, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "error": {
            "code": code.value,
            "message": message,
            "retryable": retryable,
        }
    }
