"""MCP adapter: tool definitions only (REQ-OPS-1, SPEC §5).

Each tool is deliberately thin: call one service method, convert the result to an output
model. No business rules live here, so the same rules apply whether the caller is an MCP
client, a test or a future HTTP transport.

Note there is no identity parameter on any tool. The caller's staff id is bound when the
server is built (from ``CB_STAFF_ID``) — tool arguments can't change who you are (REQ-ID-3).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from community_bridge.domain import Purpose, RequestStatus
from community_bridge.errors import DomainError, ErrorCode
from community_bridge.schemas import (
    CallerOut,
    CreateRequestOut,
    FundingSummaryOut,
    FundListOut,
    FundSummaryOut,
    ParticipantListOut,
    ParticipantOut,
    RequestListOut,
    RequestOut,
    TransitionOut,
)
from community_bridge.service import AssistanceService, TransitionResult

log = logging.getLogger(__name__)

# Hints for MCP clients; they don't enforce anything (the service does).
_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

RequestId = Annotated[str, Field(description="Assistance request id, e.g. 'req_1a2b3c4d5e6f'")]


def _invoke(tool_name: str, fn: Callable[[], BaseModel]) -> CallToolResult:
    """Run a service call and build the MCP tool result (SPEC §5.3).

    Success: ``structured_content`` is the output model (validated by the SDK against the
    tool's published ``outputSchema``), mirrored as JSON text for clients that only read
    text. Business errors: ``is_error=True`` with exactly the documented JSON body
    ``{"error": {code, message, details}}`` as text. We build the result ourselves rather
    than raising ``ToolError`` because the SDK would prefix the message with
    "Error executing tool ...", breaking the JSON contract. Unexpected exceptions are
    logged with a traceback (stderr) and returned as a generic INTERNAL_ERROR.
    """
    try:
        payload = fn().model_dump(mode="json")
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload))],
            structured_content=payload,
        )
    except DomainError as exc:
        log.info("tool=%s rejected code=%s", tool_name, exc.code.value)
        return _error_result(exc.to_dict())
    except Exception:
        log.exception("tool=%s failed unexpectedly", tool_name)
        return _error_result(
            {"error": {"code": ErrorCode.INTERNAL_ERROR.value, "message": "Internal error."}}
        )


def _error_result(body: dict[str, Any]) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(body))], is_error=True)


def _transition_out(result: TransitionResult) -> TransitionOut:
    return TransitionOut(
        request=RequestOut.build(result.request), fund=FundSummaryOut.build(result.fund)
    )


def build_server(service: AssistanceService, staff_id: str | None) -> MCPServer:
    """Create the MCP server with all 11 tools bound to one caller identity."""
    mcp = MCPServer(
        name="community-bridge",
        instructions=(
            "Emergency assistance funding for one nonprofit. Case workers create requests, "
            "program managers approve/reject, finance records payments. Amounts are USD "
            "decimal strings like '300.00'. Errors are JSON: {error: {code, message, details}}."
        ),
    )

    # ------------------------------------------------------------------ reads
    @mcp.tool(annotations=_READ_ONLY)
    def whoami() -> Annotated[CallToolResult, CallerOut]:
        """Show the staff identity this server acts as: role, nonprofit and permissions."""
        return _invoke("whoami", lambda: CallerOut.build(*service.whoami(staff_id)))

    @mcp.tool(annotations=_READ_ONLY)
    def list_participants() -> Annotated[CallToolResult, ParticipantListOut]:
        """List participants of your nonprofit. Any staff role."""
        return _invoke(
            "list_participants",
            lambda: ParticipantListOut(
                participants=[ParticipantOut.build(p) for p in service.list_participants(staff_id)]
            ),
        )

    @mcp.tool(annotations=_READ_ONLY)
    def get_participant(
        participant_id: Annotated[str, Field(description="e.g. 'par_bridge_001'")],
    ) -> Annotated[CallToolResult, ParticipantOut]:
        """Get one participant of your nonprofit. NOT_FOUND if unknown or another nonprofit's."""
        return _invoke(
            "get_participant",
            lambda: ParticipantOut.build(service.get_participant(staff_id, participant_id)),
        )

    @mcp.tool(annotations=_READ_ONLY)
    def list_funds() -> Annotated[CallToolResult, FundListOut]:
        """List your nonprofit's funds with allocation, committed, paid and available amounts."""
        return _invoke(
            "list_funds",
            lambda: FundListOut(
                funds=[FundSummaryOut.build(b) for b in service.list_funds(staff_id)]
            ),
        )

    @mcp.tool(annotations=_READ_ONLY)
    def get_funding_summary(
        fund_id: Annotated[
            str | None, Field(description="Optional fund id; omit for all funds + totals")
        ] = None,
    ) -> Annotated[CallToolResult, FundingSummaryOut]:
        """Funding summary: per-fund allocation, committed (approved, unpaid), paid, available
        (= allocation - committed - paid) and pending_requested (informational), plus totals."""
        return _invoke(
            "get_funding_summary",
            lambda: FundingSummaryOut.build(service.get_funding_summary(staff_id, fund_id)),
        )

    @mcp.tool(annotations=_READ_ONLY)
    def list_requests(
        status: Annotated[RequestStatus | None, Field(description="Filter by status")] = None,
        fund_id: Annotated[str | None, Field(description="Filter by fund id")] = None,
        participant_id: Annotated[str | None, Field(description="Filter by participant")] = None,
    ) -> Annotated[CallToolResult, RequestListOut]:
        """List assistance requests of your nonprofit, newest first, with optional filters."""
        return _invoke(
            "list_requests",
            lambda: RequestListOut(
                requests=[
                    RequestOut.build(r)
                    for r in service.list_requests(
                        staff_id, status=status, fund_id=fund_id, participant_id=participant_id
                    )
                ]
            ),
        )

    @mcp.tool(annotations=_READ_ONLY)
    def get_request(request_id: RequestId) -> Annotated[CallToolResult, RequestOut]:
        """Get one assistance request with its status and decision/payment details."""
        return _invoke(
            "get_request", lambda: RequestOut.build(service.get_request(staff_id, request_id))
        )

    # ----------------------------------------------------------------- writes
    @mcp.tool(annotations=_WRITE)
    def create_assistance_request(
        participant_id: Annotated[str, Field(description="Participant in your nonprofit")],
        fund_id: Annotated[str, Field(description="Fund in your nonprofit to pay from")],
        purpose: Annotated[Purpose, Field(description="Expense category")],
        description: Annotated[str, Field(description="What the money is for (1-500 chars)")],
        amount: Annotated[
            str, Field(description="USD decimal string, > 0, max 2 decimals, e.g. '300.00'")
        ],
        idempotency_key: Annotated[
            str | None,
            Field(description="Optional retry key; resubmitting with it never duplicates"),
        ] = None,
    ) -> Annotated[CallToolResult, CreateRequestOut]:
        """Submit a new assistance request (status PENDING). CASE_WORKER only.
        Does NOT reserve money; funds are checked and reserved at approval."""

        def run() -> CreateRequestOut:
            result = service.create_request(
                staff_id,
                participant_id=participant_id,
                fund_id=fund_id,
                purpose=purpose,
                description=description,
                amount=amount,
                idempotency_key=idempotency_key,
            )
            return CreateRequestOut(
                request=RequestOut.build(result.request),
                idempotent_replay=result.idempotent_replay,
            )

        return _invoke("create_assistance_request", run)

    @mcp.tool(annotations=_WRITE)
    def approve_request(request_id: RequestId) -> Annotated[CallToolResult, TransitionOut]:
        """Approve a PENDING request and reserve its full amount against its fund.
        PROGRAM_MANAGER only. Fails with INSUFFICIENT_FUNDS if the fund's available balance
        is too low, or INVALID_TRANSITION if the request is not PENDING."""
        return _invoke(
            "approve_request",
            lambda: _transition_out(service.approve_request(staff_id, request_id)),
        )

    @mcp.tool(annotations=_WRITE)
    def reject_request(
        request_id: RequestId,
        reason: Annotated[str | None, Field(description="Optional reason (max 500 chars)")] = None,
    ) -> Annotated[CallToolResult, TransitionOut]:
        """Reject a PENDING request. PROGRAM_MANAGER only. Funding is not changed."""
        return _invoke(
            "reject_request",
            lambda: _transition_out(service.reject_request(staff_id, request_id, reason)),
        )

    @mcp.tool(annotations=_WRITE)
    def record_payment(request_id: RequestId) -> Annotated[CallToolResult, TransitionOut]:
        """Record the simulated payment of an APPROVED request (full amount, once only).
        FINANCE only. Moves the amount from committed to paid; available is unchanged."""
        return _invoke(
            "record_payment", lambda: _transition_out(service.record_payment(staff_id, request_id))
        )

    return mcp
