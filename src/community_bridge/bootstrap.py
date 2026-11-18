"""Composition root: the only place that constructs concrete classes (Factory + DI).

Wiring:  SqliteUnitOfWorkFactory → AssistanceService → MCP server (bound to CB_STAFF_ID)

Logging goes to **stderr** only (REQ-OPS-4): on the stdio transport, stdout carries the
JSON-RPC stream and any stray output there would corrupt the protocol.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from community_bridge.identity import STAFF_ID_ENV_VAR, staff_id_from_env
from community_bridge.seed import DEFAULT_DB_PATH
from community_bridge.server import build_server
from community_bridge.service import AssistanceService
from community_bridge.sqlite_store import SqliteUnitOfWorkFactory

DB_PATH_ENV_VAR = "CB_DB_PATH"
log = logging.getLogger("community_bridge")


def configure_logging() -> None:
    level = os.environ.get("CB_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        stream=sys.stderr,  # never stdout
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def build_app(db_path: Path, staff_id: str | None) -> MCPServer:
    """Build a fully wired server. Used by ``main`` and usable directly in tests."""
    service = AssistanceService(SqliteUnitOfWorkFactory(db_path))
    return build_server(service, staff_id)


def main() -> None:
    configure_logging()
    db_path = Path(os.environ.get(DB_PATH_ENV_VAR, str(DEFAULT_DB_PATH)))
    if not db_path.exists():
        # The server never creates data itself (REQ-OPS-3); seeding is an explicit step.
        log.error("Database %s not found. Run `make reset` first.", db_path)
        sys.exit(1)

    staff_id = staff_id_from_env()
    if staff_id is None:
        # Keep running so clients can still discover tools, but every call will return
        # UNAUTHENTICATED (REQ-ID-2). Identity is re-checked on every call anyway.
        log.warning("%s is not set; all tool calls will be rejected.", STAFF_ID_ENV_VAR)

    log.info("Starting community-bridge MCP server (stdio) db=%s staff=%s", db_path, staff_id)
    build_app(db_path, staff_id).run("stdio")
