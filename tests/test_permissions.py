"""Identity, role and tenant boundaries (REQ-ID-*, REQ-AUTH-*, REQ-TEN-*)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from community_bridge.errors import Forbidden, NotFound, Unauthenticated
from community_bridge.policy import Permission
from community_bridge.service import AssistanceService
from tests.support import CW, FIN, H_CW, H_FUND, H_P1, H_PM, P1, PM, RENT, create, db_state


def _create_args(**overrides: str) -> dict[str, str]:
    return {
        "participant_id": P1,
        "fund_id": RENT,
        "purpose": "RENT",
        "description": "Rent",
        "amount": "10.00",
    } | overrides


# ------------------------------------------------------------------ AC-10 roles
@pytest.mark.parametrize(
    ("staff_id", "action"),
    [
        (PM, "create"),
        (FIN, "create"),
        (CW, "approve"),
        (FIN, "approve"),
        (CW, "reject"),
        (FIN, "reject"),
        (CW, "pay"),
        (PM, "pay"),
    ],
)
def test_ac10_roles_cannot_exceed_permissions(
    service: AssistanceService, db_path: Path, staff_id: str, action: str
) -> None:
    """REQ-AUTH-2/3/4/5: each write is limited to one role; failures change nothing."""
    request_id = create(service)
    if action == "pay":
        service.approve_request(PM, request_id)  # make payment otherwise valid
    actions: dict[str, Callable[[], Any]] = {
        "create": lambda: service.create_request(staff_id, **_create_args()),
        "approve": lambda: service.approve_request(staff_id, request_id),
        "reject": lambda: service.reject_request(staff_id, request_id),
        "pay": lambda: service.record_payment(staff_id, request_id),
    }
    before = db_state(db_path)
    with pytest.raises(Forbidden):
        actions[action]()
    assert db_state(db_path) == before


@pytest.mark.parametrize("staff_id", [CW, PM, FIN])
def test_all_roles_can_read(service: AssistanceService, staff_id: str) -> None:
    """REQ-AUTH-1."""
    request_id = create(service)
    assert service.get_request(staff_id, request_id).request_id == request_id
    assert service.list_funds(staff_id)
    caller, perms = service.whoami(staff_id)
    assert caller.nonprofit_id == "np_bridge" and Permission.READ in perms


# ------------------------------------------------------------ AC-11 tenancy
def test_ac11_other_nonprofit_cannot_see_or_act_on_requests(
    service: AssistanceService, db_path: Path
) -> None:
    """REQ-TEN-1: Harbor staff get NOT_FOUND for a Community Bridge request."""
    request_id = create(service)
    before = db_state(db_path)
    with pytest.raises(NotFound):
        service.get_request(H_PM, request_id)
    with pytest.raises(NotFound):
        service.approve_request(H_PM, request_id)
    with pytest.raises(NotFound):
        service.reject_request(H_PM, request_id)
    assert db_state(db_path) == before
    assert service.list_requests(H_PM) == []
    with pytest.raises(NotFound):
        service.get_funding_summary(H_PM, RENT)
    with pytest.raises(NotFound):
        service.get_participant(H_PM, P1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"fund_id": RENT},  # Bridge fund, Harbor participant
        {"participant_id": P1},  # Bridge participant, Harbor fund
    ],
)
def test_ac11_cannot_create_with_other_tenant_records(
    service: AssistanceService, db_path: Path, overrides: dict[str, str]
) -> None:
    """REQ-TEN-2."""
    args = _create_args(participant_id=H_P1, fund_id=H_FUND) | overrides
    before = db_state(db_path)
    with pytest.raises(NotFound):
        service.create_request(H_CW, **args)
    assert db_state(db_path) == before


def test_tenant_lists_are_isolated(service: AssistanceService) -> None:
    assert {p.participant_id for p in service.list_participants(H_CW)} == {
        "par_harbor_001",
        "par_harbor_002",
    }
    assert all(b.fund.nonprofit_id == "np_harbor" for b in service.list_funds(H_CW))


# ---------------------------------------------------------- AC-13 identity
@pytest.mark.parametrize("staff_id", [None, "", "stf_unknown", "np_bridge"])
def test_ac13_missing_or_unknown_identity_is_rejected(
    service: AssistanceService, db_path: Path, staff_id: str | None
) -> None:
    """REQ-ID-2: reads and writes both fail; nothing changes."""
    request_id = create(service)
    before = db_state(db_path)
    with pytest.raises(Unauthenticated):
        service.list_requests(staff_id)
    with pytest.raises(Unauthenticated):
        service.approve_request(staff_id, request_id)
    with pytest.raises(Unauthenticated):
        service.create_request(staff_id, **_create_args())
    assert db_state(db_path) == before
