"""Authorization policy: which role may do what (REQ-AUTH-1..5).

A data table instead of role checks sprinkled through the code: the service asks
"does this caller have permission X?" and never inspects roles directly. Changing who
may do what is a one-line edit here (Open/Closed principle).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

from community_bridge.domain import Caller, Role
from community_bridge.errors import Forbidden


class Permission(StrEnum):
    READ = "READ"
    CREATE_REQUEST = "CREATE_REQUEST"
    DECIDE_REQUEST = "DECIDE_REQUEST"  # approve or reject
    RECORD_PAYMENT = "RECORD_PAYMENT"


ROLE_PERMISSIONS: Final[Mapping[Role, frozenset[Permission]]] = {
    Role.CASE_WORKER: frozenset({Permission.READ, Permission.CREATE_REQUEST}),
    Role.PROGRAM_MANAGER: frozenset({Permission.READ, Permission.DECIDE_REQUEST}),
    Role.FINANCE: frozenset({Permission.READ, Permission.RECORD_PAYMENT}),
}


def permissions_for(role: Role) -> frozenset[Permission]:
    """All permissions granted to ``role`` (unknown roles get none — fail closed)."""
    return ROLE_PERMISSIONS.get(role, frozenset())


def require(caller: Caller, permission: Permission) -> None:
    """Raise ``Forbidden`` unless the caller's role grants ``permission``.

    The decision depends only on the server-resolved role, never on tool arguments
    (REQ-ID-3), so checking it before any record lookup leaks nothing.
    """
    if permission not in permissions_for(caller.role):
        raise Forbidden(
            f"Role {caller.role.value} is not allowed to {permission.value}.",
            {"role": caller.role.value, "required_permission": permission.value},
        )
