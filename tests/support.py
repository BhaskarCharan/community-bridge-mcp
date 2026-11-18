"""Shared test constants and helpers (seed ids, DB snapshots, invariant checks)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from community_bridge.service import AssistanceService

# Fixture identities from seed.py
CW, PM, FIN = "stf_bridge_cw", "stf_bridge_pm", "stf_bridge_fin"
H_CW, H_PM, H_FIN = "stf_harbor_cw", "stf_harbor_pm", "stf_harbor_fin"

# Seed records
RENT = "fund_bridge_rent"  # $10,000.00
UTILITY = "fund_bridge_utility"  # $500.00
P1 = "par_bridge_001"
H_FUND = "fund_harbor_transit"
H_P1 = "par_harbor_001"


def db_state(db_path: Path) -> dict[str, Any]:
    """Raw snapshot of every fund balance and request row — used to prove 'unchanged'."""
    conn = sqlite3.connect(db_path)
    try:
        funds = conn.execute(
            "SELECT id, committed_cents, paid_cents FROM funds ORDER BY id"
        ).fetchall()
        requests = conn.execute("SELECT * FROM assistance_requests ORDER BY id").fetchall()
        return {"funds": funds, "requests": requests}
    finally:
        conn.close()


def fund_cents(db_path: Path, fund_id: str) -> tuple[int, int, int]:
    """(allocation, committed, paid) in cents straight from the database."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT allocation_cents, committed_cents, paid_cents FROM funds WHERE id = ?",
            (fund_id,),
        ).fetchone()
        return (row[0], row[1], row[2])
    finally:
        conn.close()


def assert_balances_match_requests(db_path: Path) -> None:
    """AC-19: committed = Σ APPROVED amounts and paid = Σ PAID amounts, for every fund."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT f.id, f.committed_cents, f.paid_cents,
                      COALESCE(SUM(CASE WHEN r.status='APPROVED' THEN r.amount_cents END), 0),
                      COALESCE(SUM(CASE WHEN r.status='PAID' THEN r.amount_cents END), 0)
               FROM funds f LEFT JOIN assistance_requests r ON r.fund_id = f.id
               GROUP BY f.id"""
        ).fetchall()
    finally:
        conn.close()
    for fund_id, committed, paid, approved_sum, paid_sum in rows:
        assert (committed, paid) == (approved_sum, paid_sum), fund_id


def create(
    service: AssistanceService,
    *,
    amount: str = "300.00",
    fund_id: str = RENT,
    participant_id: str = P1,
    staff_id: str = CW,
) -> str:
    """Create a PENDING request and return its id."""
    result = service.create_request(
        staff_id,
        participant_id=participant_id,
        fund_id=fund_id,
        purpose="RENT",
        description="November rent",
        amount=amount,
    )
    return result.request.request_id
