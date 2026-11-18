"""Pure domain rules: money parsing (REQ-MON-1) and the transition table (REQ-ST-1)."""

from __future__ import annotations

import pytest

from community_bridge.domain import (
    RequestStatus,
    can_transition,
    format_cents,
    parse_amount,
)
from community_bridge.errors import ValidationFailed

S = RequestStatus


@pytest.mark.parametrize(
    ("text", "cents"),
    [("300", 30000), ("300.5", 30050), ("300.50", 30050), ("0.01", 1), ("1000000.00", 100_000_000)],
)
def test_parse_amount_valid(text: str, cents: int) -> None:
    assert parse_amount(text) == cents


@pytest.mark.parametrize(
    "text",
    ["0", "0.00", "-5", "+5", "10.999", "abc", "1e3", "", " 5", "5.", "1000000.01", "1,000"],
)
def test_parse_amount_invalid(text: str) -> None:
    with pytest.raises(ValidationFailed):
        parse_amount(text)


def test_cents_math_is_exact() -> None:
    # The classic float trap: 0.1 + 0.2 != 0.3. In cents it is exact.
    assert parse_amount("0.10") + parse_amount("0.20") == parse_amount("0.30")


def test_format_cents() -> None:
    assert format_cents(970000) == "9700.00"
    assert format_cents(5) == "0.05"
    assert format_cents(0) == "0.00"


ALLOWED = {(S.PENDING, S.APPROVED), (S.PENDING, S.REJECTED), (S.APPROVED, S.PAID)}


@pytest.mark.parametrize("current", list(S))
@pytest.mark.parametrize("target", list(S))
def test_transition_table_matches_spec(current: RequestStatus, target: RequestStatus) -> None:
    """REQ-ST-1: exactly three transitions are allowed; all 13 others are rejected."""
    assert can_transition(current, target) == ((current, target) in ALLOWED)
