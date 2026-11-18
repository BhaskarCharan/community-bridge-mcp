"""pytest fixtures: every test gets its own freshly seeded SQLite file (no DB mocks)."""

from __future__ import annotations

from pathlib import Path

import pytest

from community_bridge.seed import reset_database
from community_bridge.service import AssistanceService
from community_bridge.sqlite_store import SqliteUnitOfWorkFactory


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    reset_database(path)
    return path


@pytest.fixture
def service(db_path: Path) -> AssistanceService:
    return AssistanceService(SqliteUnitOfWorkFactory(db_path))
