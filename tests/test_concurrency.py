"""AC-12 (REQ-APR-3): competing approvals can never overspend a fund.

Ten program-manager calls race to approve ten $1,500 requests against the $10,000 rent
fund. A barrier releases all threads at once; each call opens its own SQLite connection,
just like separate server processes would. Only 6 × $1,500 = $9,000 fits, so exactly six
must succeed and four must fail with INSUFFICIENT_FUNDS.

Without the conditional SQL guard and BEGIN IMMEDIATE, several threads could read the same
"available" balance and all reserve against it. A mutation check (removing the guard)
is documented in AI_WORKFLOW.md.
"""

from __future__ import annotations

import threading
from pathlib import Path

from support import PM, RENT, assert_balances_match_requests, create, fund_cents

from community_bridge.errors import DomainError, InsufficientFunds
from community_bridge.service import AssistanceService

N_REQUESTS = 10


def test_ac12_concurrent_approvals_never_overspend(
    service: AssistanceService, db_path: Path
) -> None:
    request_ids = [create(service, amount="1500.00") for _ in range(N_REQUESTS)]
    barrier = threading.Barrier(N_REQUESTS)
    outcomes: dict[str, str] = {}
    lock = threading.Lock()

    def approve(request_id: str) -> None:
        barrier.wait()  # maximise contention: everyone starts together
        try:
            service.approve_request(PM, request_id)
            outcome = "approved"
        except DomainError as exc:
            outcome = exc.code.value
        except Exception as exc:  # e.g. "database is locked" would show up here
            outcome = f"crash:{exc!r}"
        with lock:
            outcomes[request_id] = outcome

    threads = [threading.Thread(target=approve, args=(rid,)) for rid in request_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    results = sorted(outcomes.values())
    assert results.count("approved") == 6, results
    assert results.count(InsufficientFunds.code.value) == 4, results
    assert fund_cents(db_path, RENT) == (1_000_000, 900_000, 0)
    assert_balances_match_requests(db_path)
