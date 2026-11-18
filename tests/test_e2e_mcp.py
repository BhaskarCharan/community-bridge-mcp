"""End-to-end through real MCP over stdio (AC-1, AC-10, AC-13, AC-14, AC-17).

Each identity gets its own server *process*, launched exactly as an MCP client would
(``python -m community_bridge`` with CB_STAFF_ID in its environment). If anything were
written to stdout besides JSON-RPC, these sessions would fail to parse it (REQ-OPS-4).
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from tests.support import CW, FIN, P1, PM, RENT

EXPECTED_TOOLS = {
    "whoami",
    "list_participants",
    "get_participant",
    "list_funds",
    "get_funding_summary",
    "create_assistance_request",
    "list_requests",
    "get_request",
    "approve_request",
    "reject_request",
    "record_payment",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def connect(db_path: Path, staff_id: str | None) -> AsyncIterator[Client]:
    env = {"CB_DB_PATH": str(db_path), "CB_LOG_LEVEL": "WARNING"}
    if staff_id is not None:
        env["CB_STAFF_ID"] = staff_id
    params = StdioServerParameters(command=sys.executable, args=["-m", "community_bridge"], env=env)
    async with Client(params) as client:
        yield client


async def ok(client: Client, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    result = await client.call_tool(tool, args or {})
    assert not result.is_error, result.content
    payload: dict[str, Any] | None = result.structured_content
    assert payload is not None
    return payload


async def error_code(client: Client, tool: str, args: dict[str, Any] | None = None) -> str:
    result = await client.call_tool(tool, args or {})
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    code: str = json.loads(text)["error"]["code"]
    return code


@pytest.mark.anyio
async def test_e2e_discovery_and_full_lifecycle(db_path: Path) -> None:
    async with connect(db_path, CW) as cw:
        tools = (await cw.list_tools()).tools
        assert {t.name for t in tools} == EXPECTED_TOOLS  # AC-17
        assert all(t.output_schema is not None for t in tools)
        created = await ok(
            cw,
            "create_assistance_request",
            {
                "participant_id": P1,
                "fund_id": RENT,
                "purpose": "RENT",
                "description": "November rent",
                "amount": "300.00",
            },
        )
        request_id = created["request"]["request_id"]
        assert created["request"]["status"] == "PENDING"
        assert await error_code(cw, "approve_request", {"request_id": request_id}) == "FORBIDDEN"

    async with connect(db_path, PM) as pm:
        approved = await ok(pm, "approve_request", {"request_id": request_id})
        assert approved["fund"]["available"] == "9700.00"
        assert approved["fund"]["committed"] == "300.00"

    async with connect(db_path, FIN) as fin:
        paid = await ok(fin, "record_payment", {"request_id": request_id})
        assert paid["request"]["status"] == "PAID"
        retry = await error_code(fin, "record_payment", {"request_id": request_id})
        assert retry == "INVALID_TRANSITION"
        summary = await ok(fin, "get_funding_summary", {"fund_id": RENT})
        fund = summary["funds"][0]
        assert (fund["allocation"], fund["committed"], fund["paid"], fund["available"]) == (
            "10000.00",
            "0.00",
            "300.00",
            "9700.00",
        )


@pytest.mark.anyio
async def test_e2e_missing_identity_is_rejected(db_path: Path) -> None:
    """AC-13: server starts (tools discoverable) but every call is UNAUTHENTICATED."""
    async with connect(db_path, None) as anon:
        assert {t.name for t in (await anon.list_tools()).tools} == EXPECTED_TOOLS
        assert await error_code(anon, "list_funds") == "UNAUTHENTICATED"
    async with connect(db_path, "stf_does_not_exist") as unknown:
        assert await error_code(unknown, "whoami") == "UNAUTHENTICATED"


@pytest.mark.anyio
async def test_e2e_invalid_amount_is_validation_error(db_path: Path) -> None:
    """AC-14 through the protocol: business validation surfaces as VALIDATION_ERROR."""
    async with connect(db_path, CW) as cw:
        code = await error_code(
            cw,
            "create_assistance_request",
            {
                "participant_id": P1,
                "fund_id": RENT,
                "purpose": "RENT",
                "description": "x",
                "amount": "10.999",
            },
        )
        assert code == "VALIDATION_ERROR"
        assert (await ok(cw, "list_requests"))["requests"] == []


@pytest.mark.anyio
async def test_e2e_extra_identity_arguments_grant_nothing(db_path: Path) -> None:
    """REQ-ID-3: identity-like arguments are ignored; the launch identity decides."""
    async with connect(db_path, CW) as cw:
        created = await ok(
            cw,
            "create_assistance_request",
            {
                "participant_id": P1,
                "fund_id": RENT,
                "purpose": "RENT",
                "description": "Rent",
                "amount": "10.00",
            },
        )
        request_id = created["request"]["request_id"]
        spoofed = {
            "request_id": request_id,
            "staff_id": PM,
            "role": "PROGRAM_MANAGER",
            "nonprofit_id": "np_harbor",
        }
        assert await error_code(cw, "approve_request", spoofed) == "FORBIDDEN"
        status = (await ok(cw, "get_request", {"request_id": request_id}))["status"]
        assert status == "PENDING"


@pytest.mark.anyio
async def test_e2e_schema_rejects_wrong_types_without_side_effects(db_path: Path) -> None:
    """Malformed arguments (number instead of decimal string, unknown enum) are rejected by
    the published input schema before business code runs (SPEC §5.3, v1.1)."""
    base = {"participant_id": P1, "fund_id": RENT, "purpose": "RENT", "description": "Rent"}
    async with connect(db_path, CW) as cw:
        for bad in ({"amount": 300}, {"amount": "300.00", "purpose": "VACATION"}):
            result = await cw.call_tool("create_assistance_request", base | bad)
            assert result.is_error
        assert (await ok(cw, "list_requests"))["requests"] == []
