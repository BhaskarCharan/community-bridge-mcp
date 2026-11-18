# CLAUDE.md — Community Bridge MCP Server

Instructions for AI coding agents (and humans) working in this repo.
**SPEC.md is the source of truth.** If code and SPEC.md disagree, stop and flag it — do not invent business behavior.

## What this project is
A headless **MCP server (stdio)** for emergency-assistance funding.
Case workers submit requests → program managers approve/reject → finance records simulated payments.
Two nonprofits, strict tenant isolation, exact-cent money, atomic approvals. 3-hour take-home: **stay in scope**.

## Commands
| Command | Purpose |
|---|---|
| `make install` | `uv sync --locked` — install pinned deps (Python 3.12, `uv.lock`) |
| `make reset` | Recreate + seed the SQLite DB (separate from startup) |
| `make run` | Start server over stdio as `CB_STAFF_ID` (without it, every call → UNAUTHENTICATED) |
| `make test` | `uv run pytest -v` |
| `make check` | ruff lint + format check + mypy + pytest — **must pass before a task is done** |
| `make demo` | Reset DB, run scripted MCP client demo (no LLM) |
| `make package` | `dist/community-bridge.zip` of committed files + git history |

Env vars: `CB_STAFF_ID` (fixture identity, required), `CB_DB_PATH` (default `data/community_bridge.db`).

## Architecture (layered — dependencies point inward only)
```
server.py        MCP adapter: tool definitions, schemas, error → MCP result mapping. NO business logic.
   │
service.py       Use cases (create / approve / reject / pay / read). Enforces policy + state machine.
   │  depends on abstractions only
ports.py         Protocols: repositories + UnitOfWork.
   │  implemented by
sqlite_store.py  SQLite schema, SqliteUnitOfWork, repository implementations.

domain.py        Enums (Role, RequestStatus), entities, TRANSITIONS table, Money (cents) helpers. Pure, no I/O.
policy.py        Permission enum + ROLE_PERMISSIONS map + access checks. Pure, no I/O.
errors.py        DomainError hierarchy with stable error codes.
identity.py      Resolve CB_STAFF_ID → Caller(staff_id, role, nonprofit_id). Reject missing/unknown.
schemas.py       Pydantic input/output models used by MCP tools.
bootstrap.py     Composition root / factory: wires store → service → server; configures logging.
seed.py          Reset + synthetic seed data.
```

## SOLID — how it applies here (pragmatic, not ceremonial)
- **S — Single Responsibility:** one reason to change per module (see table above). Tools don't do SQL; repositories don't check roles.
- **O — Open/Closed:** new transitions/roles/permissions are added by editing the **data tables** (`TRANSITIONS`, `ROLE_PERMISSIONS`), not by adding `if/elif` chains.
- **L — Liskov:** any `UnitOfWork`/repository implementation must honor the Protocol contract (same errors, same atomicity guarantees).
- **I — Interface Segregation:** small, focused Protocols (`RequestRepository`, `FundRepository`, `ParticipantRepository`, `StaffRepository`) instead of one god-repository.
- **D — Dependency Inversion:** `service.py` depends on `ports.py` Protocols; concrete SQLite classes are injected via constructors in `bootstrap.py`.

## LLD patterns in use (use these; don't add others without a reason)
- **Layered architecture / Ports & Adapters** — MCP and SQLite are adapters around the service core.
- **Repository** — data access per aggregate, always **scoped by `nonprofit_id`**.
- **Unit of Work** — one transaction per use case; commit on success, rollback on any error.
- **Table-driven State Machine** — `TRANSITIONS: dict[RequestStatus, set[RequestStatus]]`.
- **Policy map (Strategy-lite)** — `ROLE_PERMISSIONS: dict[Role, set[Permission]]`.
- **Money in one place** — integer cents everywhere; `domain.parse_amount` / `format_cents` are the only conversions.
- **Factory / Composition Root** — `bootstrap.build_server(...)` is the only place that constructs concrete classes.
- **Dependency Injection** — constructor injection; no global singletons, no service locators.

Do NOT add: State classes per status, Command objects per tool, abstract base class hierarchies, ORMs, DI frameworks, generic "BaseRepository<T>". Prefer plain dataclasses + `typing.Protocol`.

## Non-negotiable invariants
1. **stdout is reserved for MCP protocol.** Never `print()`. Log via `logging` to **stderr**.
2. **Money = integer cents** (`int`). Never `float`. Input is a decimal string, > 0, ≤ 2 decimal places, USD.
3. **Fund invariant:** `available = allocation − committed − paid`, and `available ≥ 0`, `committed ≥ 0` always.
4. **Approve** reserves full amount (committed += amount) **atomically** with the status change; insufficient funds → nothing changes.
5. **Pay** moves committed → paid (available unchanged); succeeds only once.
6. **Reject** changes status only. **Submit** reserves nothing. Requests are immutable after creation.
7. **Only** allowed transitions: PENDING→APPROVED, PENDING→REJECTED, APPROVED→PAID.
8. **Identity comes only from `CB_STAFF_ID`**, resolved server-side. Tool arguments, prompts, and stored text never grant permissions.
9. **Tenant isolation:** every query filters by the caller's `nonprofit_id`. Other tenants' records → `NOT_FOUND` (don't leak existence).
10. Any failed/unauthorized call leaves business state **unchanged**.
11. Atomic guards are enforced **in SQL** (`BEGIN IMMEDIATE` + conditional `UPDATE ... WHERE status = ? AND <funds sufficient>` + check rowcount), not only in Python.

## MCP SDK notes (mcp 2.x — verify against the installed source, not memory)
- `FastMCP` no longer exists: use `from mcp.server.mcpserver import MCPServer`; client is `from mcp import Client` with `StdioServerParameters`.
- Tools return `Annotated[CallToolResult, OutputModel]` via `server._invoke`: the output schema is still published and validated, and errors keep the exact JSON body. Don't raise `ToolError` — the SDK prefixes its message with "Error executing tool ...".

## Code style
- Python 3.12, full type hints, mypy strict + ruff clean (ruff `T20` bans `print` outside `scripts/`).
- Small functions; names from the domain (`approve_request`, `committed_cents`).
- Errors: raise `DomainError` subclasses from the service; only `server.py` converts them to MCP error results.

## Comments (required, meaningful, not noisy)
- **Docstring** on every module, class and public function: purpose, related **REQ-IDs** (e.g. `REQ-APR-002`), raised errors.
- **Inline comments only where the WHY is non-obvious**: atomic SQL guards, cents math, tenant scoping, why we return NOT_FOUND instead of FORBIDDEN.
- Don't restate the code (`# increment i`). Write for an engineer reading this for the first time.

## Testing rules
- pytest; every test gets a **fresh seeded temp SQLite file** (fixture). No mocks of the database.
- Test names reference REQ-IDs where practical; keep `TRACEABILITY.md` updated.
- Every failure test asserts **state unchanged** (fund balances + request status) — not just the error code.
- Must keep: concurrency test (competing approvals), ≥1 end-to-end test through real MCP stdio.

## Working agreement for AI agents
- Work one task from SPEC.md's implementation plan at a time; finish with `make check` green.
- Don't change SPEC.md silently — propose the change; when accepted, add an entry to its **Revisions** section with the reason.
- Out of scope (don't build): UI, HTTP transport, OAuth code, MCP resources/prompts, partial payments, refunds, cancellations, pagination, extra roles.
- When unsure about business behavior: ask, or record an assumption in SPEC.md "Open questions".
