"""Typed service errors mapped to HTTP responses by the API layer."""
from __future__ import annotations


class EngineError(Exception):
    def __init__(self, message: str, status: int = 400, code: str | None = None, extra: dict | None = None):
        super().__init__(message)
        self.message, self.status, self.code, self.extra = message, status, code, extra or {}
