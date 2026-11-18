"""Caller identity resolution (REQ-ID-1, REQ-ID-2).

The *only* identity input is the staff id configured at launch (``CB_STAFF_ID``). It is
looked up server-side on every call, so role and nonprofit always come from our data —
never from tool arguments, prompts or stored text (REQ-ID-3). Re-resolving per call also
means a removed staff record stops working immediately.
"""

from __future__ import annotations

import os
from typing import Final

from community_bridge.domain import Caller
from community_bridge.errors import Unauthenticated
from community_bridge.ports import StaffRepository

STAFF_ID_ENV_VAR: Final = "CB_STAFF_ID"


def staff_id_from_env() -> str | None:
    """Read the launch-time fixture identity; empty/whitespace counts as missing."""
    value = os.environ.get(STAFF_ID_ENV_VAR, "").strip()
    return value or None


def resolve_caller(staff: StaffRepository, staff_id: str | None) -> Caller:
    """Map a configured staff id to a ``Caller`` or raise ``Unauthenticated``.

    The error message is the same for "missing" and "unknown" apart from a hint, and never
    reveals which staff ids exist.
    """
    if not staff_id:
        raise Unauthenticated(f"No caller identity configured; set {STAFF_ID_ENV_VAR}.")
    caller = staff.get_caller(staff_id)
    if caller is None:
        raise Unauthenticated("Unknown caller identity.")
    return caller
