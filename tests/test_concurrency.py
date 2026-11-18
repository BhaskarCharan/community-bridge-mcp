"""Concurrency: competing writes never overspend or duplicate a financial effect.

AC-12 (REQ-APR-3): ten program-manager calls race to approve ten $1,500 requests against
the $10,000 rent fund. Only 6 × $1,500 = $9,000 fits, so exactly six must succeed and four
must fail with INSUFFICIENT_FUNDS.

REQ-APR-4 / REQ-PAY-2 under concurrency: many simultaneous approvals (or payments) of the
*same* request must produce exactly one financial effect.

A barrier releases all threads at once; each call opens its own SQLite connection, just
like separate server processes would. A mutation check (removing the SQL guard or
BEGIN IMMEDIATE makes these tests fail) is documented in AI_WORKFLOW.md.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path

from community_bridge.errors import DomainError
from community_bridge.service import AssistanceService
from tests.support import FIN, PM, RENT, assert_balances_match_requests, create, fund_cents


def race(calls: list[Callable[[], object]]) -> list[str]:
    """Run all calls at the same instant in separate threads; return sorted outcomes."""
    barrier = threading.Barrier(len(calls))
    outcomes: list[str] = []
    lock = threading.Lock()

    def run(call: Callable[[], object]) -> None:
        barrier.wait()  # maximise contention: everyone starts together
        try:
            call()
            outcome = "ok"
        except DomainError as exc:
            outcome = exc.code.value
        except Exception as exc:  # e.g. "database is locked" would show up here
            outcome = f"crash:{exc!r}"
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=run, args=(c,)) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return sorted(outcomes)


def test_ac12_concurrent_approvals_never_overspend(
    service: AssistanceService, db_path: Path
) -> None:
    request_ids = [create(service, amount="1500.00") for _ in range(10)]

    results = race([partial(service.approve_request, PM, rid) for rid in request_ids])

    assert results.count("ok") == 6, results
    assert results.count("INSUFFICIENT_FUNDS") == 4, results
    assert fund_cents(db_path, RENT) == (1_000_000, 900_000, 0)
    assert_balances_match_requests(db_path)


def test_concurrent_approvals_of_same_request_reserve_once(
    service: AssistanceService, db_path: Path
) -> None:
    """REQ-APR-4: a retry storm on one request reserves its amount exactly once."""
    request_id = create(service, amount="300.00")

    results = race([lambda: service.approve_request(PM, request_id) for _ in range(8)])

    assert results.count("ok") == 1, results
    assert results.count("INVALID_TRANSITION") == 7, results
    assert fund_cents(db_path, RENT) == (1_000_000, 30_000, 0)


def test_concurrent_payments_of_same_request_pay_once(
    service: AssistanceService, db_path: Path
) -> None:
    """REQ-PAY-2: simultaneous payment attempts move money exactly once."""
    request_id = create(service, amount="300.00")
    service.approve_request(PM, request_id)

    results = race([lambda: service.record_payment(FIN, request_id) for _ in range(8)])

    assert results.count("ok") == 1, results
    assert results.count("INVALID_TRANSITION") == 7, results
    assert fund_cents(db_path, RENT) == (1_000_000, 0, 30_000)
    assert_balances_match_requests(db_path)
