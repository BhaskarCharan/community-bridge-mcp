"""MCP output models (SPEC §5.1). The SDK publishes these as each tool's ``outputSchema``
and validates every result against them before it is sent (REQ-OPS-1).

Money leaves the server only as 2-decimal strings plus ``currency`` — never floats —
so clients can't introduce rounding errors when they parse results (REQ-MON-1).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from community_bridge.domain import (
    CURRENCY,
    AssistanceRequest,
    Caller,
    FundBalance,
    Participant,
    format_cents,
)
from community_bridge.policy import Permission

Money = str  # documentation alias: "9700.00"


class CallerOut(BaseModel):
    staff_id: str
    name: str
    role: str
    nonprofit_id: str
    nonprofit_name: str
    permissions: list[str]

    @classmethod
    def build(cls, caller: Caller, permissions: frozenset[Permission]) -> CallerOut:
        return cls(
            staff_id=caller.staff_id,
            name=caller.name,
            role=caller.role.value,
            nonprofit_id=caller.nonprofit_id,
            nonprofit_name=caller.nonprofit_name,
            permissions=sorted(p.value for p in permissions),
        )


class ParticipantOut(BaseModel):
    participant_id: str
    full_name: str

    @classmethod
    def build(cls, p: Participant) -> ParticipantOut:
        return cls(participant_id=p.participant_id, full_name=p.full_name)


class ParticipantListOut(BaseModel):
    participants: list[ParticipantOut]


class FundSummaryOut(BaseModel):
    fund_id: str
    name: str
    currency: str = CURRENCY
    allocation: Money
    committed: Money = Field(description="Reserved by approved, not yet paid requests")
    paid: Money
    available: Money = Field(description="allocation - committed - paid")
    pending_requested: Money = Field(
        description="Sum of PENDING requests; informational, NOT reserved"
    )

    @classmethod
    def build(cls, b: FundBalance) -> FundSummaryOut:
        f = b.fund
        return cls(
            fund_id=f.fund_id,
            name=f.name,
            allocation=format_cents(f.allocation_cents),
            committed=format_cents(f.committed_cents),
            paid=format_cents(f.paid_cents),
            available=format_cents(f.available_cents),
            pending_requested=format_cents(b.pending_cents),
        )


class FundListOut(BaseModel):
    funds: list[FundSummaryOut]


class TotalsOut(BaseModel):
    allocation: Money
    committed: Money
    paid: Money
    available: Money
    pending_requested: Money


class FundingSummaryOut(BaseModel):
    currency: str = CURRENCY
    funds: list[FundSummaryOut]
    totals: TotalsOut

    @classmethod
    def build(cls, balances: list[FundBalance]) -> FundingSummaryOut:
        # Totals are summed in integer cents, then formatted once — no float drift.
        return cls(
            funds=[FundSummaryOut.build(b) for b in balances],
            totals=TotalsOut(
                allocation=format_cents(sum(b.fund.allocation_cents for b in balances)),
                committed=format_cents(sum(b.fund.committed_cents for b in balances)),
                paid=format_cents(sum(b.fund.paid_cents for b in balances)),
                available=format_cents(sum(b.fund.available_cents for b in balances)),
                pending_requested=format_cents(sum(b.pending_cents for b in balances)),
            ),
        )


class RequestOut(BaseModel):
    request_id: str
    participant_id: str
    fund_id: str
    purpose: str
    description: str
    amount: Money
    currency: str = CURRENCY
    status: str
    created_by: str
    created_at: str
    decided_by: str | None = None
    decided_at: str | None = None
    decision_reason: str | None = None
    paid_by: str | None = None
    paid_at: str | None = None
    payment_reference: str | None = None

    @classmethod
    def build(cls, r: AssistanceRequest) -> RequestOut:
        return cls(
            request_id=r.request_id,
            participant_id=r.participant_id,
            fund_id=r.fund_id,
            purpose=r.purpose.value,
            description=r.description,
            amount=format_cents(r.amount_cents),
            status=r.status.value,
            created_by=r.created_by,
            created_at=r.created_at,
            decided_by=r.decided_by,
            decided_at=r.decided_at,
            decision_reason=r.decision_reason,
            paid_by=r.paid_by,
            paid_at=r.paid_at,
            payment_reference=r.payment_reference,
        )


class RequestListOut(BaseModel):
    requests: list[RequestOut]


class CreateRequestOut(BaseModel):
    request: RequestOut
    idempotent_replay: bool = Field(
        description="True if this idempotency_key was already used; the original is returned"
    )


class TransitionOut(BaseModel):
    request: RequestOut
    fund: FundSummaryOut = Field(description="Fund balances after this action")
