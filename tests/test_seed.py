"""AC-18 (REQ-OPS-3, REQ-OPS-6): reset is repeatable and seeds both nonprofits."""

from __future__ import annotations

from pathlib import Path

from support import FIN, H_FIN, PM, create, db_state

from community_bridge.seed import reset_database
from community_bridge.service import AssistanceService


def test_ac18_reset_restores_clean_seed(service: AssistanceService, db_path: Path) -> None:
    clean = db_state(db_path)
    request_id = create(service)
    service.approve_request(PM, request_id)
    assert db_state(db_path) != clean

    reset_database(db_path)
    assert db_state(db_path) == clean


def test_seed_has_two_nonprofits_with_all_roles(service: AssistanceService) -> None:
    for staff_id, nonprofit in [(FIN, "np_bridge"), (H_FIN, "np_harbor")]:
        caller, _ = service.whoami(staff_id)
        assert caller.nonprofit_id == nonprofit
        assert service.list_participants(staff_id)
        assert len(service.list_funds(staff_id)) == 2
