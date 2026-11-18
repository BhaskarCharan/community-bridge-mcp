"""SQLite adapter: schema, Unit of Work and repository implementations (REQ-OPS-2).

Concurrency model (REQ-APR-3, SPEC §3.3):
* Each unit of work uses its own connection and one explicit transaction.
* Write units start with ``BEGIN IMMEDIATE``, which takes SQLite's write lock up front.
  Competing writers therefore run one after another (waiting up to ``busy_timeout``),
  never interleaved.
* Money-moving statements are *conditional* UPDATEs that re-check the balance or status
  at write time and report success via ``rowcount``. Python-side checks only produce
  friendly errors; the SQL guards are what actually guarantee correctness.
* CHECK constraints and composite foreign keys are the last line of defence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self

from community_bridge.domain import (
    AssistanceRequest,
    Caller,
    Fund,
    Participant,
    Purpose,
    RequestStatus,
    Role,
)

BUSY_TIMEOUT_SECONDS = 5.0

SCHEMA = """
CREATE TABLE nonprofits (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE staff (
    id           TEXT PRIMARY KEY,
    nonprofit_id TEXT NOT NULL REFERENCES nonprofits(id),
    name         TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('CASE_WORKER', 'PROGRAM_MANAGER', 'FINANCE'))
);

CREATE TABLE participants (
    id           TEXT PRIMARY KEY,
    nonprofit_id TEXT NOT NULL REFERENCES nonprofits(id),
    full_name    TEXT NOT NULL,
    UNIQUE (nonprofit_id, id)          -- target of the composite FK below
);

CREATE TABLE funds (
    id               TEXT PRIMARY KEY,
    nonprofit_id     TEXT NOT NULL REFERENCES nonprofits(id),
    name             TEXT NOT NULL,
    allocation_cents INTEGER NOT NULL CHECK (allocation_cents > 0),
    committed_cents  INTEGER NOT NULL DEFAULT 0 CHECK (committed_cents >= 0),
    paid_cents       INTEGER NOT NULL DEFAULT 0 CHECK (paid_cents >= 0),
    -- REQ-FUND-2: the database itself refuses any overspend.
    CHECK (committed_cents + paid_cents <= allocation_cents),
    UNIQUE (nonprofit_id, id)
);

CREATE TABLE assistance_requests (
    id                TEXT PRIMARY KEY,
    nonprofit_id      TEXT NOT NULL REFERENCES nonprofits(id),
    participant_id    TEXT NOT NULL,
    fund_id           TEXT NOT NULL,
    purpose           TEXT NOT NULL
                      CHECK (purpose IN ('RENT', 'UTILITIES', 'TRANSPORTATION', 'OTHER')),
    description       TEXT NOT NULL,
    amount_cents      INTEGER NOT NULL CHECK (amount_cents > 0),
    status            TEXT NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'PAID')),
    idempotency_key   TEXT,
    created_by        TEXT NOT NULL REFERENCES staff(id),
    created_at        TEXT NOT NULL,
    decided_by        TEXT REFERENCES staff(id),
    decided_at        TEXT,
    decision_reason   TEXT,
    paid_by           TEXT REFERENCES staff(id),
    paid_at           TEXT,
    payment_reference TEXT,
    -- REQ-TEN-2: participant and fund must belong to the request's own nonprofit.
    FOREIGN KEY (nonprofit_id, participant_id) REFERENCES participants (nonprofit_id, id),
    FOREIGN KEY (nonprofit_id, fund_id)        REFERENCES funds (nonprofit_id, id),
    -- REQ-CRT-4: a key identifies one request per nonprofit (NULLs don't collide).
    UNIQUE (nonprofit_id, idempotency_key)
);

CREATE INDEX idx_requests_tenant_status ON assistance_requests (nonprofit_id, status);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection configured for explicit transactions and FK enforcement."""
    # isolation_level=None: the sqlite3 module must not open transactions implicitly;
    # we issue BEGIN / COMMIT ourselves so the transaction boundary is obvious.
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # per-connection setting in SQLite
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables in an empty database (used by the seed/reset command only)."""
    conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the single writer
    conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Row → entity mapping
# ---------------------------------------------------------------------------
def _to_request(row: sqlite3.Row) -> AssistanceRequest:
    return AssistanceRequest(
        request_id=row["id"],
        nonprofit_id=row["nonprofit_id"],
        participant_id=row["participant_id"],
        fund_id=row["fund_id"],
        purpose=Purpose(row["purpose"]),
        description=row["description"],
        amount_cents=row["amount_cents"],
        status=RequestStatus(row["status"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        idempotency_key=row["idempotency_key"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        decision_reason=row["decision_reason"],
        paid_by=row["paid_by"],
        paid_at=row["paid_at"],
        payment_reference=row["payment_reference"],
    )


def _to_fund(row: sqlite3.Row) -> Fund:
    return Fund(
        fund_id=row["id"],
        nonprofit_id=row["nonprofit_id"],
        name=row["name"],
        allocation_cents=row["allocation_cents"],
        committed_cents=row["committed_cents"],
        paid_cents=row["paid_cents"],
    )


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------
class SqliteStaffRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_caller(self, staff_id: str) -> Caller | None:
        row = self._conn.execute(
            """SELECT s.id, s.name, s.role, s.nonprofit_id, n.name AS nonprofit_name
               FROM staff s JOIN nonprofits n ON n.id = s.nonprofit_id
               WHERE s.id = ?""",
            (staff_id,),
        ).fetchone()
        if row is None:
            return None
        return Caller(
            staff_id=row["id"],
            name=row["name"],
            role=Role(row["role"]),
            nonprofit_id=row["nonprofit_id"],
            nonprofit_name=row["nonprofit_name"],
        )


class SqliteParticipantRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list(self, nonprofit_id: str) -> list[Participant]:
        rows = self._conn.execute(
            "SELECT id, nonprofit_id, full_name FROM participants "
            "WHERE nonprofit_id = ? ORDER BY full_name",
            (nonprofit_id,),
        ).fetchall()
        return [Participant(r["id"], r["nonprofit_id"], r["full_name"]) for r in rows]

    def get(self, nonprofit_id: str, participant_id: str) -> Participant | None:
        row = self._conn.execute(
            "SELECT id, nonprofit_id, full_name FROM participants "
            "WHERE nonprofit_id = ? AND id = ?",
            (nonprofit_id, participant_id),
        ).fetchone()
        return Participant(row["id"], row["nonprofit_id"], row["full_name"]) if row else None


class SqliteFundRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list(self, nonprofit_id: str) -> list[Fund]:
        rows = self._conn.execute(
            "SELECT * FROM funds WHERE nonprofit_id = ? ORDER BY name", (nonprofit_id,)
        ).fetchall()
        return [_to_fund(r) for r in rows]

    def get(self, nonprofit_id: str, fund_id: str) -> Fund | None:
        row = self._conn.execute(
            "SELECT * FROM funds WHERE nonprofit_id = ? AND id = ?", (nonprofit_id, fund_id)
        ).fetchone()
        return _to_fund(row) if row else None

    def pending_totals(self, nonprofit_id: str) -> dict[str, int]:
        rows = self._conn.execute(
            """SELECT fund_id, SUM(amount_cents) AS total FROM assistance_requests
               WHERE nonprofit_id = ? AND status = 'PENDING' GROUP BY fund_id""",
            (nonprofit_id,),
        ).fetchall()
        return {r["fund_id"]: r["total"] for r in rows}

    def try_reserve(self, nonprofit_id: str, fund_id: str, amount_cents: int) -> bool:
        # THE overspend guard (REQ-APR-1/2/3): the availability check is part of the
        # UPDATE itself, evaluated against the row's current values at write time.
        # Under BEGIN IMMEDIATE no other writer can change the row in between.
        cur = self._conn.execute(
            """UPDATE funds SET committed_cents = committed_cents + :amount
               WHERE id = :fund_id AND nonprofit_id = :np
                 AND allocation_cents - committed_cents - paid_cents >= :amount""",
            {"amount": amount_cents, "fund_id": fund_id, "np": nonprofit_id},
        )
        return cur.rowcount == 1

    def settle_payment(self, nonprofit_id: str, fund_id: str, amount_cents: int) -> bool:
        # REQ-PAY-1: committed → paid. Available is untouched because the money already
        # left "available" at approval time; subtracting again would double-count.
        cur = self._conn.execute(
            """UPDATE funds
               SET committed_cents = committed_cents - :amount,
                   paid_cents      = paid_cents + :amount
               WHERE id = :fund_id AND nonprofit_id = :np AND committed_cents >= :amount""",
            {"amount": amount_cents, "fund_id": fund_id, "np": nonprofit_id},
        )
        return cur.rowcount == 1


class SqliteRequestRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(self, request: AssistanceRequest) -> None:
        self._conn.execute(
            """INSERT INTO assistance_requests
               (id, nonprofit_id, participant_id, fund_id, purpose, description, amount_cents,
                status, idempotency_key, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request.request_id,
                request.nonprofit_id,
                request.participant_id,
                request.fund_id,
                request.purpose.value,
                request.description,
                request.amount_cents,
                request.status.value,
                request.idempotency_key,
                request.created_by,
                request.created_at,
            ),
        )

    def get(self, nonprofit_id: str, request_id: str) -> AssistanceRequest | None:
        row = self._conn.execute(
            "SELECT * FROM assistance_requests WHERE nonprofit_id = ? AND id = ?",
            (nonprofit_id, request_id),
        ).fetchone()
        return _to_request(row) if row else None

    def find_by_idempotency_key(self, nonprofit_id: str, key: str) -> AssistanceRequest | None:
        row = self._conn.execute(
            "SELECT * FROM assistance_requests WHERE nonprofit_id = ? AND idempotency_key = ?",
            (nonprofit_id, key),
        ).fetchone()
        return _to_request(row) if row else None

    def list(
        self,
        nonprofit_id: str,
        status: RequestStatus | None = None,
        fund_id: str | None = None,
        participant_id: str | None = None,
    ) -> list[AssistanceRequest]:
        # Filters are bound parameters (never string-interpolated values); the tenant
        # filter is unconditional.
        sql = "SELECT * FROM assistance_requests WHERE nonprofit_id = ?"
        params: list[str] = [nonprofit_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value)
        if fund_id is not None:
            sql += " AND fund_id = ?"
            params.append(fund_id)
        if participant_id is not None:
            sql += " AND participant_id = ?"
            params.append(participant_id)
        sql += " ORDER BY created_at DESC, id DESC"
        return [_to_request(r) for r in self._conn.execute(sql, params).fetchall()]

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
        # Compare-and-set on status: a retried or concurrent decision updates 0 rows.
        cur = self._conn.execute(
            """UPDATE assistance_requests
               SET status = ?, decided_by = ?, decided_at = ?, decision_reason = ?
               WHERE nonprofit_id = ? AND id = ? AND status = ?""",
            (
                new_status.value,
                decided_by,
                decided_at,
                reason,
                nonprofit_id,
                request_id,
                expected.value,
            ),
        )
        return cur.rowcount == 1

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
        # Compare-and-set: only an APPROVED request can become PAID, and only once.
        cur = self._conn.execute(
            """UPDATE assistance_requests
               SET status = 'PAID', paid_by = ?, paid_at = ?, payment_reference = ?
               WHERE nonprofit_id = ? AND id = ? AND status = ?""",
            (paid_by, paid_at, payment_reference, nonprofit_id, request_id, expected.value),
        )
        return cur.rowcount == 1


# ---------------------------------------------------------------------------
# Unit of Work
# ---------------------------------------------------------------------------
class SqliteUnitOfWork:
    """One connection + one transaction. Rolls back unless ``commit()`` was called."""

    staff: SqliteStaffRepository
    participants: SqliteParticipantRepository
    funds: SqliteFundRepository
    requests: SqliteRequestRepository
    _conn: sqlite3.Connection

    def __init__(self, db_path: Path, *, write: bool = False) -> None:
        self._db_path = db_path
        self._write = write
        self._committed = False

    def __enter__(self) -> Self:
        self._conn = connect(self._db_path)
        # IMMEDIATE = acquire the write lock now, so check-then-write sequences in this
        # unit can't interleave with another writer. Reads use a plain (deferred) BEGIN
        # for a consistent snapshot without blocking writers (WAL mode).
        self._conn.execute("BEGIN IMMEDIATE" if self._write else "BEGIN")
        self.staff = SqliteStaffRepository(self._conn)
        self.participants = SqliteParticipantRepository(self._conn)
        self.funds = SqliteFundRepository(self._conn)
        self.requests = SqliteRequestRepository(self._conn)
        return self

    def commit(self) -> None:
        self._conn.execute("COMMIT")
        self._committed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            # Any exception — or simply forgetting to commit — discards every write,
            # which is how "failed actions leave state unchanged" is guaranteed.
            if not self._committed:
                self._conn.execute("ROLLBACK")
        finally:
            self._conn.close()


class SqliteUnitOfWorkFactory:
    """Creates units of work bound to one database file (injected into the service)."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def __call__(self, *, write: bool = False) -> SqliteUnitOfWork:
        return SqliteUnitOfWork(self._db_path, write=write)
