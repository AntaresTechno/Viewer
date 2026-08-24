"""Shared rule-engine exceptions."""
from __future__ import annotations


class RuleError(Exception):
    """Raised when a rule cannot be evaluated."""


class JsUnavailableError(RuleError):
    """No JavaScript engine is installed for @js / {{}} rules."""


class RuleJSError(RuleError):
    """JavaScript evaluation failed."""


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status
