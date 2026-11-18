"""Reproducible MCP client demo — no language model involved.

Launches the real server over stdio once per fixture identity and walks the workflow:

    discover tools → create (case worker) → approve (program manager)
    → record payment (finance) → final funding summary

Run with ``make demo`` (which resets the database first).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters

DB_PATH = os.path.abspath(os.environ.get("CB_DB_PATH", "data/community_bridge.db"))
FUND = "fund_bridge_rent"


def server_as(staff_id: str) -> StdioServerParameters:
    """Server launch parameters for one fixture identity (identity is set at launch)."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "community_bridge"],
        env={"CB_STAFF_ID": staff_id, "CB_DB_PATH": DB_PATH, "CB_LOG_LEVEL": "WARNING"},
    )


async def call(client: Client, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    result = await client.call_tool(tool, args or {})
    if result.is_error:
        raise SystemExit(f"{tool} failed: {result.content}")
    payload: dict[str, Any] | None = result.structured_content
    assert payload is not None
    return payload


def show(title: str, payload: dict[str, Any]) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2))


async def main() -> None:
    async with Client(server_as("stf_bridge_cw")) as cw:
        tools = (await cw.list_tools()).tools
        print(f"Discovered {len(tools)} tools: {', '.join(sorted(t.name for t in tools))}")
        show("whoami (case worker)", await call(cw, "whoami"))
        created = await call(
            cw,
            "create_assistance_request",
            {
                "participant_id": "par_bridge_001",
                "fund_id": FUND,
                "purpose": "RENT",
                "description": "November rent shortfall",
                "amount": "300.00",
                "idempotency_key": "demo-rent-001",
            },
        )
        show("create_assistance_request (case worker)", created)
        request_id = created["request"]["request_id"]

    async with Client(server_as("stf_bridge_pm")) as pm:
        show(
            "approve_request (program manager)",
            await call(pm, "approve_request", {"request_id": request_id}),
        )

    async with Client(server_as("stf_bridge_fin")) as fin:
        show(
            "record_payment (finance)",
            await call(fin, "record_payment", {"request_id": request_id}),
        )
        summary = await call(fin, "get_funding_summary", {"fund_id": FUND})
        show("get_funding_summary (finance)", summary)

    fund = summary["funds"][0]
    print(
        f"\nResult: allocation {fund['allocation']} | committed {fund['committed']} | "
        f"paid {fund['paid']} | available {fund['available']} {fund['currency']}"
    )


if __name__ == "__main__":
    asyncio.run(main())
