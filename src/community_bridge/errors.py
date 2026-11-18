"""Business error hierarchy with stable, client-facing error codes (SPEC §5.3).

The service raises these; only the MCP adapter (``server.py``) turns them into tool results.
Every error carries a machine-readable ``code`` plus optional ``details`` for the client.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar


class ErrorCode(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class DomainError(Exception):
    """Base class for anticipated business failures. State is always left unchanged."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the documented error body: ``{"error": {code, message, details}}``."""
        return {
            "error": {"code": self.code.value, "message": self.message, "details": self.details}
        }


class Unauthenticated(DomainError):
    """REQ-ID-2: CB_STAFF_ID missing or not a known staff member."""

    code = ErrorCode.UNAUTHENTICATED


class Forbidden(DomainError):
    """REQ-AUTH-5: the caller's role lacks the required permission."""

    code = ErrorCode.FORBIDDEN


class NotFound(DomainError):
    """REQ-TEN-1: record absent *in the caller's nonprofit* (other tenants look identical)."""

    code = ErrorCode.NOT_FOUND


class ValidationFailed(DomainError):
    """REQ-CRT-3: an input field violates a business validation rule (SPEC §3.4)."""

    code = ErrorCode.VALIDATION_ERROR


class InvalidTransition(DomainError):
    """REQ-ST-1: the action is not allowed from the request's current status."""

    code = ErrorCode.INVALID_TRANSITION


class InsufficientFunds(DomainError):
    """REQ-APR-2: approving would exceed the fund's available balance."""

    code = ErrorCode.INSUFFICIENT_FUNDS


class IdempotencyConflict(DomainError):
    """REQ-CRT-4: idempotency key reused with a different payload."""

    code = ErrorCode.IDEMPOTENCY_CONFLICT
