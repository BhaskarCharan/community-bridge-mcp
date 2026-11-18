# Community Bridge — Emergency Assistance Funding MCP Server

A headless MCP server (stdio) for submitting, approving and paying emergency-assistance requests against grant funds, for two isolated nonprofits. Exact-cent accounting, atomic approvals, server-side identity and role checks.

| Document | Purpose |
|---|---|
| [SPEC.md](SPEC.md) · [initial v1.0](docs/SPEC_v1.0_initial.md) | Specification: requirements (REQ-IDs), business rules, data model, MCP interface, acceptance criteria, plan, production security, open questions, **revisions** |
| [TRACEABILITY.md](TRACEABILITY.md) | Requirement → code → test mapping |
| [AI_WORKFLOW.md](AI_WORKFLOW.md) | Three AI-assisted examples with evidence and corrections |
| [CLAUDE.md](CLAUDE.md) | Rules given to the AI coding agent (scope, invariants, architecture) |

## Setup and run

Requirements: [uv](https://docs.astral.sh/uv/) (it downloads Python 3.12 automatically). Dependencies are pinned in `pyproject.toml` (`==`) and `uv.lock`: `mcp==2.3.0`; dev: `pytest==9.2.0`, `ruff==0.17.5`, `mypy==2.4.1`.

```bash
make install     # uv sync --locked
make reset       # create + seed data/community_bridge.db (separate from startup; repeatable)
make check       # ruff lint + format check, mypy --strict, pytest
make demo        # reset, then scripted MCP client: discover → create → approve → pay → summary
make run CB_STAFF_ID=stf_bridge_cw   # start the stdio server as one identity
```

The server never creates or seeds data; if the DB is missing it exits and tells you to run `make reset`. Logs go to stderr (`CB_LOG_LEVEL`, default INFO); stdout carries only MCP JSON-RPC.

### Fixture identities (`CB_STAFF_ID`)

| Nonprofit | Case worker | Program manager | Finance |
|---|---|---|---|
| Community Bridge (`np_bridge`) | `stf_bridge_cw` | `stf_bridge_pm` | `stf_bridge_fin` |
| Harbor Help Network (`np_harbor`) | `stf_harbor_cw` | `stf_harbor_pm` | `stf_harbor_fin` |

Seed funds: `fund_bridge_rent` $10,000.00, `fund_bridge_utility` $500.00, `fund_harbor_transit` $5,000.00, `fund_harbor_emergency` $2,500.00. Participants: `par_bridge_001..003`, `par_harbor_001..002`. All data is synthetic.

### MCP client configuration (Claude Desktop / Claude Code)

One server entry per identity — identity is fixed at launch, never passed in tool arguments:

```json
{
  "mcpServers": {
    "community-bridge-manager": {
      "command": "uv",
      "args": ["run", "--directory", "<ABSOLUTE_PATH_TO_REPO>", "python", "-m", "community_bridge"],
      "env": {
        "CB_STAFF_ID": "stf_bridge_pm",
        "CB_DB_PATH": "<ABSOLUTE_PATH_TO_REPO>/data/community_bridge.db"
      }
    }
  }
}
```

**Tools (11):** `whoami`, `list_participants`, `get_participant`, `list_funds`, `get_funding_summary`, `create_assistance_request`, `list_requests`, `get_request`, `approve_request`, `reject_request`, `record_payment`. Each publishes input and output schemas; errors are `isError: true` with body `{"error": {"code", "message", "details"}}` (SPEC §5).

## Architecture

```
server.py (MCP adapter, thin) → service.py (use cases, policy, state machine) → ports.py (Protocols)
                                                                                    ↑ implemented by
domain.py · policy.py · errors.py · identity.py (pure)                      sqlite_store.py (UoW + repos)
bootstrap.py = composition root (wires SQLite → service → server; logging to stderr)
```

Pragmatic SOLID: one responsibility per module; table-driven transitions and role permissions (extend by data, not branches); service depends on Protocols, concrete classes injected in `bootstrap.py`; small per-aggregate repositories; Unit of Work per use case.

## How competing approvals are handled

Each write runs in one SQLite transaction opened with `BEGIN IMMEDIATE`, so writers serialize on the write lock. The reservation is a single conditional statement — `UPDATE funds SET committed = committed + :amt WHERE … AND allocation − committed − paid >= :amt` — and success is read from `rowcount`; the status change is a compare-and-set (`WHERE status = 'PENDING'`). Any failure rolls back the whole unit. DB `CHECK (committed + paid <= allocation)` is a final safety net.

**Verification:** `test_ac12_concurrent_approvals_never_overspend` releases 10 threads (separate connections) through a barrier to approve 10 × $1,500 against $10,000: exactly 6 succeed, 4 get `INSUFFICIENT_FUNDS`, committed = $9,000, balances equal request sums. A mutation check showed the test fails when either the SQL guard or `BEGIN IMMEDIATE` is removed (AI_WORKFLOW.md §3b).

## Results (actual, 2026-11-18)

```
$ make check
ruff check: All checks passed!   ruff format --check: 30 files already formatted
mypy (strict): Success: no issues found in 24 source files
pytest: 94 passed
$ make demo   → Result: allocation 10000.00 | committed 0.00 | paid 300.00 | available 9700.00 USD
```

Test files: `test_domain` (money, transition table), `test_service` (AC-1…9, 14–16, 19), `test_permissions` (AC-10, 11, 13), `test_concurrency` (AC-12, AC-20), `test_seed` (AC-18), `test_e2e_mcp` (AC-17, AC-21 + lifecycle, auth and validation over the SDK client), `test_stdio_protocol` (AC-23: raw JSON-RPC, stdout purity, logs on stderr).

**Failed checks during development** (fixed, details in AI_WORKFLOW.md): first E2E run failed 3 tests (SDK error-message prefix broke the JSON error contract); first raw-stdio test lost a response because it closed stdin before the call finished (test harness bug).

**Not run / not verified:** manual use from a GUI MCP client (Claude Desktop) — verified only through the SDK client in the E2E test and demo. Concurrency was tested with threads in one process (separate SQLite connections), not with multiple server processes.

## Time spent

_To be filled in by the author: actual active time (spec / implementation / testing / docs)._

## Known limitations

- One caller identity per server process (by design for stdio fixtures; HTTP + OAuth design in SPEC §9).
- APPROVED requests that are never paid keep their commitment — cancellation/release is out of scope (SPEC Q8).
- Idempotency keys only on create; approve/reject/pay retries return `INVALID_TRANSITION` with `current_status` for the client to interpret.
- No pagination; no audit-event table beyond who/when fields on each request.
- SQLite single-writer model suits local use; a production deployment would use a server database with the same conditional-update pattern.

## Next priorities (deferred)

1. Streamable HTTP transport with OAuth resource-server validation and per-request `Caller` (SPEC §9).
2. Append-only audit events per transition.
3. Business decision + implementation for releasing stale commitments.
4. Multi-process concurrency test; pagination for list tools.
