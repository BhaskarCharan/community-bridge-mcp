"""Use cases for assistance requests and funding (the application/service layer).

Every public method follows the same shape (SPEC §3.5):

    open unit of work → resolve caller (UNAUTHENTICATED) → check permission (FORBIDDEN)
    → validate fields (VALIDATION_ERROR) → tenant-scoped lookup (NOT_FOUND)
    → state machine (INVALID_TRANSITION) → funds (INSUFFICIENT_FUNDS) → commit

Any ``DomainError`` raised before ``commit()`` makes the unit of work roll back, so a
failed call never changes business state (REQ-AUTH-5, REQ-ST-1, REQ-APR-2).

The service depends only on the ``ports`` Protocols (Dependency Inversion); it knows
nothing about SQLite or MCP.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from community_bridge import policy
from community_bridge.domain import (
    AssistanceRequest,
    Caller,
    FundBalance,
    Participant,
    Purpose,
    RequestStatus,
    can_transition,
    format_cents,
    parse_amount,
)
from community_bridge.errors import (
    IdempotencyConflict,
    InsufficientFunds,
    InvalidTransition,
    NotFound,
    ValidationFailed,
)
from community_bridge.identity import resolve_caller
from community_bridge.policy import Permission
from community_bridge.ports import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

MAX_DESCRIPTION_LEN = 500
MAX_REASON_LEN = 500
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


def _new_payment_reference() -> str:
    return f"SIM-{uuid.uuid4().hex[:10].upper()}"  # simulated; no real payment rail


@dataclass(frozen=True, slots=True)
class CreateResult:
    request: AssistanceRequest
    idempotent_replay: bool  # True when an earlier identical submission was returned


@dataclass(frozen=True, slots=True)
class TransitionResult:
    request: AssistanceRequest
    fund: FundBalance  # fund state *after* the transition, for immediate confirmation


class AssistanceService:
    """All business operations. Constructed once in ``bootstrap`` with its dependencies.

    Args:
        uow_factory: opens storage transactions (injected; SQLite in production and tests).
        clock: returns the current UTC timestamp string (injectable for deterministic tests).
        new_request_id / new_payment_reference: id generators (injectable for tests).
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Callable[[], str] = _utc_now,
        new_request_id: Callable[[], str] = _new_request_id,
        new_payment_reference: Callable[[], str] = _new_payment_reference,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._new_request_id = new_request_id
        self._new_payment_reference = new_payment_reference

    # ------------------------------------------------------------------ reads
    def whoami(self, staff_id: str | None) -> tuple[Caller, frozenset[Permission]]:
        """REQ-READ-1: the resolved caller and their permissions."""
        with self._uow_factory() as uow:
            caller = self._authorize(uow, staff_id, Permission.READ)
            return caller, policy.permissions_for(caller.role)

    def list_participants(self, staff_id: str | None) -> list[Participant]:
        """REQ-READ-2: participants of the caller's nonprofit."""
        with self._uow_factory() as uow:
            caller = self._authorize(uow, staff_id, Permission.READ)
            return uow.participants.list(caller.nonprofit_id)

    def get_participant(self, staff_id: str | None, participant_id: str) -> Participant:
        """REQ-READ-2. Raises NotFound for unknown or other-tenant ids (REQ-TEN-1)."""
        with self._uow_factory() as uow:
            caller = self._authorize(uow, staff_id, Permission.READ)
            participant = uow.participants.get(caller.nonprofit_id, participant_id)
            if participant is None:
                raise NotFound("Participant not found.", {"participant_id": participant_id})
            return participant

    def list_funds(self, staff_id: str | None) -> list[FundBalance]:
        """REQ-READ-3: all funds of the caller's nonprofit with balances."""
        with self._uow_factory() as uow:
            caller = self._authorize(uow, staff_id, Permission.READ)
            return self._fund_balances(uow, caller.nonprofit_id)

    def get_funding_summary(self, staff_id: str | None, fund_id: str | None) -> list[FundBalance]:
        """REQ-READ-3: one fund (if ``fund_id`` given) or all funds. Totals are computed
        by the adapter from the returned balances."""
        with self._uow_factory() as uow:
            caller = self._authorize(uow, staff_id, Permission.READ)
            balances = self._fund_balances(uow, caller.nonprofit_id)
            if fund_id is None:
                return balances
            selected = [b for b in balances if b.fund.fund_id == fund_id]
            if not selected:
                raise NotFound("Fund not found.", {"fund_id": fund_id})
            return selected

    def list_requests(
        self,
        staff_id: str | None,
        *,
        status: str | None = None,
        fund_id: str | None = None,
        participant_id: str | None = None,
    ) -> list[AssistanceRequest]:
        """REQ-READ-4: requests of the caller's nonprofit, newest first, optional filters."""
        with self._uow_factory() as uow:
            caller = self._authorize(uow, staff_id, Permission.READ)
            status_filter = _parse_enum(RequestStatus, status, "status") if status else None
            return uow.requests.list(
                caller.nonprofit_id,
                status=status_filter,
                fund_id=fund_id,
                participant_id=participant_id,
            )

    def get_request(self, staff_id: str | None, request_id: str) -> AssistanceRequest:
        """REQ-READ-4. Raises NotFound for unknown or other-tenant ids (REQ-TEN-1)."""
        with self._uow_factory() as uow:
            caller = self._authorize(uow, staff_id, Permission.READ)
            return self._load_request(uow, caller, request_id)

    # ----------------------------------------------------------------- writes
    def create_request(
        self,
        staff_id: str | None,
        *,
        participant_id: str,
        fund_id: str,
        purpose: str,
        description: str,
        amount: str,
        idempotency_key: str | None = None,
    ) -> CreateResult:
        """REQ-CRT-1..5: submit a PENDING request. Reserves no money (REQ-CRT-2).

        Text fields are stored verbatim as inert data; nothing in them is interpreted
        (REQ-ID-3).
        """
        with self._uow_factory(write=True) as uow:
            caller = self._authorize(uow, staff_id, Permission.CREATE_REQUEST)

            # Field validation first, so invalid input never touches storage.
            amount_cents = parse_amount(amount)
            purpose_value = _parse_enum(Purpose, purpose, "purpose")
            clean_description = _validate_text(description, "description", MAX_DESCRIPTION_LEN)
            if idempotency_key is not None and not _IDEMPOTENCY_KEY_PATTERN.fullmatch(
                idempotency_key
            ):
                raise ValidationFailed(
                    "idempotency_key must be 1-100 characters of A-Z, a-z, 0-9, '_' or '-'.",
                    {"field": "idempotency_key"},
                )

            # Tenant-scoped existence checks: another nonprofit's ids are simply "not found".
            if uow.participants.get(caller.nonprofit_id, participant_id) is None:
                raise NotFound("Participant not found.", {"participant_id": participant_id})
            if uow.funds.get(caller.nonprofit_id, fund_id) is None:
                raise NotFound("Fund not found.", {"fund_id": fund_id})

            # Retry safety (REQ-CRT-4). Safe from races: we hold the write lock.
            if idempotency_key is not None:
                existing = uow.requests.find_by_idempotency_key(
                    caller.nonprofit_id, idempotency_key
                )
                if existing is not None:
                    same_payload = (
                        existing.participant_id,
                        existing.fund_id,
                        existing.purpose,
                        existing.description,
                        existing.amount_cents,
                    ) == (participant_id, fund_id, purpose_value, clean_description, amount_cents)
                    if not same_payload:
                        raise IdempotencyConflict(
                            "idempotency_key was already used for a different request.",
                            {"idempotency_key": idempotency_key},
                        )
                    return CreateResult(existing, idempotent_replay=True)

            request = AssistanceRequest(
                request_id=self._new_request_id(),
                nonprofit_id=caller.nonprofit_id,  # tenant comes from the caller, not input
                participant_id=participant_id,
                fund_id=fund_id,
                purpose=purpose_value,
                description=clean_description,
                amount_cents=amount_cents,
                status=RequestStatus.PENDING,
                created_by=caller.staff_id,
                created_at=self._clock(),
                idempotency_key=idempotency_key,
            )
            uow.requests.add(request)
            uow.commit()
            log.info("request created id=%s by=%s", request.request_id, caller.staff_id)
            return CreateResult(request, idempotent_replay=False)

    def approve_request(self, staff_id: str | None, request_id: str) -> TransitionResult:
        """REQ-APR-1..4: PENDING → APPROVED and reserve the full amount, atomically."""
        with self._uow_factory(write=True) as uow:
            caller = self._authorize(uow, staff_id, Permission.DECIDE_REQUEST)
            request = self._load_request(uow, caller, request_id)
            _ensure_transition(request, RequestStatus.APPROVED)

            # Reserve first: if the fund can't cover it we fail before touching the request.
            if not uow.funds.try_reserve(
                caller.nonprofit_id, request.fund_id, request.amount_cents
            ):
                fund = uow.funds.get(caller.nonprofit_id, request.fund_id)
                available = fund.available_cents if fund else 0
                raise InsufficientFunds(
                    f"Fund has {format_cents(available)} available; "
                    f"request needs {format_cents(request.amount_cents)}.",
                    {
                        "fund_id": request.fund_id,
                        "available": format_cents(available),
                        "requested": format_cents(request.amount_cents),
                    },
                )
            # Compare-and-set on status. Under the write lock this can't fail after the
            # check above, but if it ever did, raising here rolls back the reservation too.
            if not uow.requests.mark_decided(
                caller.nonprofit_id,
                request_id,
                expected=RequestStatus.PENDING,
                new_status=RequestStatus.APPROVED,
                decided_by=caller.staff_id,
                decided_at=self._clock(),
                reason=None,
            ):
                raise _transition_error(request.status, RequestStatus.APPROVED)

            result = self._transition_result(uow, caller, request_id)
            uow.commit()
            log.info("request approved id=%s by=%s", request_id, caller.staff_id)
            return result

    def reject_request(
        self, staff_id: str | None, request_id: str, reason: str | None = None
    ) -> TransitionResult:
        """REQ-REJ-1: PENDING → REJECTED. Funding is not touched."""
        with self._uow_factory(write=True) as uow:
            caller = self._authorize(uow, staff_id, Permission.DECIDE_REQUEST)
            clean_reason = (
                _validate_text(reason, "reason", MAX_REASON_LEN, required=False)
                if reason is not None
                else None
            )
            request = self._load_request(uow, caller, request_id)
            _ensure_transition(request, RequestStatus.REJECTED)
            if not uow.requests.mark_decided(
                caller.nonprofit_id,
                request_id,
                expected=RequestStatus.PENDING,
                new_status=RequestStatus.REJECTED,
                decided_by=caller.staff_id,
                decided_at=self._clock(),
                reason=clean_reason,
            ):
                raise _transition_error(request.status, RequestStatus.REJECTED)

            result = self._transition_result(uow, caller, request_id)
            uow.commit()
            log.info("request rejected id=%s by=%s", request_id, caller.staff_id)
            return result

    def record_payment(self, staff_id: str | None, request_id: str) -> TransitionResult:
        """REQ-PAY-1..2: APPROVED → PAID; move the amount from committed to paid, once."""
        with self._uow_factory(write=True) as uow:
            caller = self._authorize(uow, staff_id, Permission.RECORD_PAYMENT)
            request = self._load_request(uow, caller, request_id)
            _ensure_transition(request, RequestStatus.PAID)

            # Status compare-and-set is what makes payment "at most once" (REQ-PAY-2).
            if not uow.requests.mark_paid(
                caller.nonprofit_id,
                request_id,
                expected=RequestStatus.APPROVED,
                paid_by=caller.staff_id,
                paid_at=self._clock(),
                payment_reference=self._new_payment_reference(),
            ):
                raise _transition_error(request.status, RequestStatus.PAID)
            if not uow.funds.settle_payment(
                caller.nonprofit_id, request.fund_id, request.amount_cents
            ):
                # An APPROVED request always has its amount committed; reaching this means
                # the stored balances are corrupt. Fail loudly (rolls back) — never guess.
                raise RuntimeError(f"Committed balance missing for approved request {request_id}")

            result = self._transition_result(uow, caller, request_id)
            uow.commit()
            log.info("payment recorded id=%s by=%s", request_id, caller.staff_id)
            return result

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _authorize(uow: UnitOfWork, staff_id: str | None, permission: Permission) -> Caller:
        """Resolve identity then check role permission — the first step of every call."""
        caller = resolve_caller(uow.staff, staff_id)
        policy.require(caller, permission)
        return caller

    @staticmethod
    def _load_request(uow: UnitOfWork, caller: Caller, request_id: str) -> AssistanceRequest:
        request = uow.requests.get(caller.nonprofit_id, request_id)
        if request is None:
            # Same answer whether the id doesn't exist or belongs to another nonprofit,
            # so callers can't probe for other tenants' records (assumption A9).
            raise NotFound("Request not found.", {"request_id": request_id})
        return request

    @staticmethod
    def _fund_balances(uow: UnitOfWork, nonprofit_id: str) -> list[FundBalance]:
        pending = uow.funds.pending_totals(nonprofit_id)
        return [FundBalance(f, pending.get(f.fund_id, 0)) for f in uow.funds.list(nonprofit_id)]

    def _transition_result(
        self, uow: UnitOfWork, caller: Caller, request_id: str
    ) -> TransitionResult:
        """Re-read request and fund inside the same transaction for the response."""
        request = self._load_request(uow, caller, request_id)
        balance = next(
            b
            for b in self._fund_balances(uow, caller.nonprofit_id)
            if b.fund.fund_id == request.fund_id
        )
        return TransitionResult(request, balance)


# -------------------------------------------------------------- module helpers
def _ensure_transition(request: AssistanceRequest, target: RequestStatus) -> None:
    if not can_transition(request.status, target):
        raise _transition_error(request.status, target)


def _transition_error(current: RequestStatus, target: RequestStatus) -> InvalidTransition:
    return InvalidTransition(
        f"Cannot move request from {current.value} to {target.value}.",
        {"current_status": current.value, "requested_status": target.value},
    )


def _parse_enum[E: (Purpose, RequestStatus)](enum_type: type[E], value: str, field: str) -> E:
    try:
        return enum_type(value)
    except ValueError:
        raise ValidationFailed(
            f"{field} must be one of: {', '.join(m.value for m in enum_type)}.",
            {"field": field, "value": value},
        ) from None


def _validate_text(value: str, field: str, max_len: int, *, required: bool = True) -> str:
    cleaned = value.strip()
    if required and not cleaned:
        raise ValidationFailed(f"{field} must not be empty.", {"field": field})
    if len(cleaned) > max_len:
        raise ValidationFailed(f"{field} must be at most {max_len} characters.", {"field": field})
    return cleaned
