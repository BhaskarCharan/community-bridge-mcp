"""Storage ports: the abstractions the service layer depends on (Dependency Inversion).

The service only knows these Protocols; ``sqlite_store.py`` provides the concrete adapter.
Interfaces are split per aggregate (Interface Segregation) rather than one god-repository.

Tenant rule (REQ-TEN-1): every business query takes ``nonprofit_id`` as a required
argument — there is deliberately no unscoped lookup for participants, funds or requests.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from community_bridge.domain import (
    AssistanceRequest,
    Caller,
    Fund,
    Participant,
    RequestStatus,
)


class StaffRepository(Protocol):
    def get_caller(self, staff_id: str) -> Caller | None:
        """Resolve a staff id to its role and nonprofit, or None if unknown.

        Not tenant-scoped by design: this lookup is how we *learn* the caller's tenant.
        """
        ...


class ParticipantRepository(Protocol):
    def list(self, nonprofit_id: str) -> list[Participant]: ...

    def get(self, nonprofit_id: str, participant_id: str) -> Participant | None: ...


class FundRepository(Protocol):
    def list(self, nonprofit_id: str) -> list[Fund]: ...

    def get(self, nonprofit_id: str, fund_id: str) -> Fund | None: ...

    def pending_totals(self, nonprofit_id: str) -> dict[str, int]:
        """Sum of PENDING request amounts per fund id (informational only)."""
        ...

    def try_reserve(self, nonprofit_id: str, fund_id: str, amount_cents: int) -> bool:
        """Atomically add ``amount_cents`` to committed **only if** it is available.

        Returns False (and changes nothing) when funds are insufficient. The check and the
        update must be a single conditional write so concurrent approvals cannot overspend.
        """
        ...

    def settle_payment(self, nonprofit_id: str, fund_id: str, amount_cents: int) -> bool:
        """Move ``amount_cents`` from committed to paid; False if not enough is committed."""
        ...


class RequestRepository(Protocol):
    def add(self, request: AssistanceRequest) -> None: ...

    def get(self, nonprofit_id: str, request_id: str) -> AssistanceRequest | None: ...

    def find_by_idempotency_key(self, nonprofit_id: str, key: str) -> AssistanceRequest | None: ...

    def list(
        self,
        nonprofit_id: str,
        status: RequestStatus | None = None,
        fund_id: str | None = None,
        participant_id: str | None = None,
    ) -> list[AssistanceRequest]: ...

    def mark_decided(
        self,
        nonprofit_id: str,
        request_id: str,
        *,
        expected: RequestStatus,
        new_status: RequestStatus,
        decided_by: str,
        decided_at: str,
        reason: str | None,
    ) -> bool:
        """Set the decision **only if** the current status equals ``expected``.

        Returns False if another call already moved the request (compare-and-set guard).
        """
        ...

    def mark_paid(
        self,
        nonprofit_id: str,
        request_id: str,
        *,
        expected: RequestStatus,
        paid_by: str,
        paid_at: str,
        payment_reference: str,
    ) -> bool:
        """Set PAID **only if** the current status equals ``expected`` (compare-and-set)."""
        ...


class UnitOfWork(Protocol):
    """One business transaction: all repository writes commit together or not at all.

    Usage::

        with uow_factory(write=True) as uow:
            ...                # repository calls
            uow.commit()       # without this, leaving the block rolls everything back
    """

    # Read-only properties (not plain attributes) so implementations may expose their
    # concrete repository types — Protocol attributes would have to match exactly.
    @property
    def staff(self) -> StaffRepository: ...
    @property
    def participants(self) -> ParticipantRepository: ...
    @property
    def funds(self) -> FundRepository: ...
    @property
    def requests(self) -> RequestRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self, *, write: bool = False) -> UnitOfWork:
        """Open a unit of work; ``write=True`` takes the write lock up front."""
        ...
