"""REQ-OPS-4 / AC-17: stdout carries only MCP JSON-RPC; application logs go to stderr.

Talks to the server with raw newline-delimited JSON-RPC (no SDK client), so any stray
print or log line on stdout would show up here as a non-JSON line.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

MESSAGES = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "raw-test", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "list_funds", "arguments": {}},
    },
]


def test_stdout_is_pure_json_rpc_and_logs_go_to_stderr(db_path: Path) -> None:
    env = os.environ | {
        "CB_DB_PATH": str(db_path),
        "CB_STAFF_ID": "stf_bridge_fin",
        "CB_LOG_LEVEL": "INFO",  # make sure there *are* logs that could leak
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "community_bridge"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    watchdog = threading.Timer(30, proc.kill)  # never hang the suite
    watchdog.start()
    try:
        for message in MESSAGES:
            proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        # Keep stdin open until every response has arrived: closing it early makes the
        # server shut down while the tool call is still in flight.
        parsed: list[dict[str, Any]] = []
        while {m.get("id") for m in parsed} < {1, 2, 3}:
            line = proc.stdout.readline()
            assert line, "server closed stdout early"
            parsed.append(json.loads(line))  # fails on any non-JSON stdout line
        proc.stdin.close()
        trailing = proc.stdout.read()  # anything printed during shutdown
        stderr = proc.stderr.read()
        proc.wait()
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()

    assert trailing.strip() == ""
    assert all(msg.get("jsonrpc") == "2.0" for msg in parsed)
    responses = {msg["id"]: msg for msg in parsed if "id" in msg}
    assert len(responses[2]["result"]["tools"]) == 11
    assert responses[3]["result"]["structuredContent"]["funds"]

    assert "Starting community-bridge MCP server" in stderr  # logs exist, on stderr
