"""Reset + seed command (REQ-OPS-3, REQ-OPS-6). Run with ``make reset``.

Deliberately separate from server startup: the server never creates or seeds data, so
restarting it can't silently wipe or alter business records.

All data is synthetic. Two nonprofits, each with one fixture staff identity per role,
participants and funds. No requests are seeded, so the demo starts from clean balances.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from community_bridge.sqlite_store import connect, create_schema

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/community_bridge.db")

NONPROFITS = [
    ("np_bridge", "Community Bridge"),
    ("np_harbor", "Harbor Help Network"),
]

# Fixture identities selected via CB_STAFF_ID (REQ-ID-1).
STAFF = [
    ("stf_bridge_cw", "np_bridge", "Avery Case (fictional)", "CASE_WORKER"),
    ("stf_bridge_pm", "np_bridge", "Blake Manager (fictional)", "PROGRAM_MANAGER"),
    ("stf_bridge_fin", "np_bridge", "Casey Ledger (fictional)", "FINANCE"),
    ("stf_harbor_cw", "np_harbor", "Drew Case (fictional)", "CASE_WORKER"),
    ("stf_harbor_pm", "np_harbor", "Emery Manager (fictional)", "PROGRAM_MANAGER"),
    ("stf_harbor_fin", "np_harbor", "Finley Ledger (fictional)", "FINANCE"),
]

PARTICIPANTS = [
    ("par_bridge_001", "np_bridge", "Jordan Sample"),
    ("par_bridge_002", "np_bridge", "Riley Example"),
    ("par_bridge_003", "np_bridge", "Morgan Placeholder"),
    ("par_harbor_001", "np_harbor", "Taylor Fixture"),
    ("par_harbor_002", "np_harbor", "Quinn Synthetic"),
]

# (id, nonprofit, name, allocation in cents)
FUNDS = [
    ("fund_bridge_rent", "np_bridge", "City Rent Relief Grant", 1_000_000),  # $10,000.00
    ("fund_bridge_utility", "np_bridge", "Utility Hardship Fund", 50_000),  # $500.00
    ("fund_harbor_transit", "np_harbor", "Transit Access Grant", 500_000),  # $5,000.00
    ("fund_harbor_emergency", "np_harbor", "Emergency Needs Fund", 250_000),  # $2,500.00
]


def reset_database(db_path: Path) -> None:
    """Delete any existing database at ``db_path`` and create a freshly seeded one."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove the DB and its WAL side files so the reset is truly from scratch.
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)

    conn = connect(db_path)
    try:
        create_schema(conn)
        conn.execute("BEGIN")
        conn.executemany("INSERT INTO nonprofits (id, name) VALUES (?, ?)", NONPROFITS)
        conn.executemany(
            "INSERT INTO staff (id, nonprofit_id, name, role) VALUES (?, ?, ?, ?)", STAFF
        )
        conn.executemany(
            "INSERT INTO participants (id, nonprofit_id, full_name) VALUES (?, ?, ?)",
            PARTICIPANTS,
        )
        conn.executemany(
            "INSERT INTO funds (id, nonprofit_id, name, allocation_cents) VALUES (?, ?, ?, ?)",
            FUNDS,
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Reset and seed the Community Bridge database.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite file path")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    reset_database(args.db)
    log.info(
        "Seeded %s: %d nonprofits, %d staff, %d participants, %d funds",
        args.db,
        len(NONPROFITS),
        len(STAFF),
        len(PARTICIPANTS),
        len(FUNDS),
    )


if __name__ == "__main__":
    main()
