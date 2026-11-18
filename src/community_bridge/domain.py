"""Domain model: enums, entities, the request state machine and money helpers.

Pure Python — no I/O, no MCP, no SQL — so every rule here is unit-testable in isolation.
Related requirements: REQ-ST-1, REQ-FUND-1, REQ-FUND-2, REQ-MON-1.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from community_bridge.errors import ValidationFailed


class Role(StrEnum):
    """Staff roles. Each staff member has exactly one role (assumption A1)."""

    CASE_WORKER = "CASE_WORKER"
    PROGRAM_MANAGER = "PROGRAM_MANAGER"
    FINANCE = "FINANCE"


class RequestStatus(StrEnum):
    """Lifecycle states of an assistance request (SPEC §3.1)."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAID = "PAID"


class Purpose(StrEnum):
    """What the assistance pays for (assumption A5)."""

    RENT = "RENT"
    UTILITIES = "UTILITIES"
    TRANSPORTATION = "TRANSPORTATION"
    OTHER = "OTHER"


# ---------------------------------------------------------------------------
# State machine (REQ-ST-1)
# ---------------------------------------------------------------------------
# Table-driven on purpose (Open/Closed): a new transition is a one-line data change,
# not a new if/elif branch scattered across the service.
TRANSITIONS: Final[Mapping[RequestStatus, frozenset[RequestStatus]]] = {
    RequestStatus.PENDING: frozenset({RequestStatus.APPROVED, RequestStatus.REJECTED}),
    RequestStatus.APPROVED: frozenset({RequestStatus.PAID}),
    RequestStatus.REJECTED: frozenset(),  # terminal
    RequestStatus.PAID: frozenset(),  # terminal
}


def can_transition(current: RequestStatus, target: RequestStatus) -> bool:
    """Return True if moving from ``current`` to ``target`` is allowed (REQ-ST-1)."""
    return target in TRANSITIONS[current]


# ---------------------------------------------------------------------------
# Money (REQ-MON-1): integer cents internally, decimal strings at the boundary
# ---------------------------------------------------------------------------
CURRENCY: Final = "USD"
MIN_AMOUNT_CENTS: Final = 1  # $0.01
MAX_AMOUNT_CENTS: Final = 100_000_000  # $1,000,000.00 sanity cap (assumption A4)

# Plain digits with at most two decimals. This deliberately rejects "1e3", "-5", "+5",
# " 5", "5." and "10.999" before any numeric conversion happens.
_AMOUNT_PATTERN: Final = re.compile(r"^\d{1,7}(\.\d{1,2})?$")


def parse_amount(text: str) -> int:
    """Parse a USD decimal string such as ``"300.50"`` into integer cents (``30050``).

    Raises:
        ValidationFailed: if the text is not a plain positive amount with ≤ 2 decimals,
            or is outside 0.01 – 1,000,000.00.
    """
    if not isinstance(text, str) or not _AMOUNT_PATTERN.fullmatch(text):
        raise ValidationFailed(
            "amount must be a decimal string with at most 2 decimal places, e.g. '300.00'",
            {"field": "amount"},
        )
    # Decimal (not float) so "0.1" is exactly ten cents; the regex guarantees the
    # scaled value is a whole number, so int() never truncates anything.
    cents = int(Decimal(text) * 100)
    if not MIN_AMOUNT_CENTS <= cents <= MAX_AMOUNT_CENTS:
        raise ValidationFailed(
            "amount must be between 0.01 and 1000000.00", {"field": "amount", "value": text}
        )
    return cents


def format_cents(cents: int) -> str:
    """Format integer cents as a 2-decimal string: ``970000`` → ``"9700.00"``."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}"


# ---------------------------------------------------------------------------
# Entities (immutable snapshots read from storage)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Caller:
    """The authenticated staff member behind the current tool call (REQ-ID-1).

    Built only by ``identity.resolve_caller`` from server-side data — never from tool input.
    """

    staff_id: str
    name: str
    role: Role
    nonprofit_id: str
    nonprofit_name: str


@dataclass(frozen=True, slots=True)
class Participant:
    participant_id: str
    nonprofit_id: str
    full_name: str


@dataclass(frozen=True, slots=True)
class Fund:
    """A grant fund. Balances are stored, and kept consistent by atomic SQL updates."""

    fund_id: str
    nonprofit_id: str
    name: str
    allocation_cents: int
    committed_cents: int
    paid_cents: int

    @property
    def available_cents(self) -> int:
        """REQ-FUND-1: available = allocation − committed − paid."""
        return self.allocation_cents - self.committed_cents - self.paid_cents


@dataclass(frozen=True, slots=True)
class FundBalance:
    """A fund plus the informational total of its PENDING requests (not reserved)."""

    fund: Fund
    pending_cents: int


@dataclass(frozen=True, slots=True)
class AssistanceRequest:
    """An assistance request. Immutable after creation except via state transitions."""

    request_id: str
    nonprofit_id: str
    participant_id: str
    fund_id: str
    purpose: Purpose
    description: str
    amount_cents: int
    status: RequestStatus
    created_by: str
    created_at: str
    idempotency_key: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    decision_reason: str | None = None
    paid_by: str | None = None
    paid_at: str | None = None
    payment_reference: str | None = None
