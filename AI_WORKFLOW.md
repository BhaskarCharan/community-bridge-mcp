# AI-assisted workflow — three examples

Tool: Claude Code (Claude Opus) in VS Code, guided by `CLAUDE.md` (scope, invariants, layering) and `SPEC.md`. Each suggestion was checked against independent evidence.

## 1. Requirements / specification — storage choice

**Prompt (excerpt):** *"do we need to use db in this, is that mentioned in the doc, if we need to use db then use mongo db"*

**Suggestion:** The brief asks for "persistent local storage", not a specific database — but approval must atomically update two records (request status + fund balance). MongoDB needs a replica set for multi-document transactions and a running server for every reviewer; SQLite is stdlib and ACID. The AI recommended SQLite and asked rather than overriding the stated preference. **Decision:** SQLite (SPEC §4, REQ-OPS-2).

**Evidence:** the brief's atomicity and "repeatable setup" clauses; MongoDB's documentation (transactions require a replica set); later, the concurrency tests (AC-12, AC-20) showed the SQLite design meets the atomicity requirement.

## 2. Implementation planning / coding — MCP SDK major version

**Prompt (excerpt):** *"Approved and continue with next steps"* — execute SPEC §8 tasks T1→T9 under `CLAUDE.md` rules, `make check` as the gate per task.

**What happened:** the plan assumed the v1 API `mcp.server.fastmcp.FastMCP` (AI prior knowledge), but `uv add mcp` resolved **mcp 2.3.0**. Before coding, the AI read the installed package: `mcp/server/fastmcp.py` only raises "FastMCP was renamed to MCPServer". It then wrote the adapter against the installed `MCPServer`, `Client` and `StdioServerParameters` source, and recorded the gotcha in `CLAUDE.md`.

**Evidence:** installed SDK source; `mypy --strict` clean against real SDK types; E2E tests over a real stdio subprocess pass.

## 3. Debugging / verification

**a) E2E failure → spec revision.** First E2E run: 3 failures (`JSONDecodeError`). Printing the raw `CallToolResult` showed the SDK prefixes raised `ToolError` text with "Error executing tool …", breaking SPEC §5.3's JSON error body. The SDK source showed tools may return `Annotated[CallToolResult, OutputModel]` (output schema still published and validated), so `server._invoke` now builds results itself (SPEC v1.1). **Evidence:** E2E asserts exact error codes; all 11 tools still expose `output_schema`.

**b) Mutation check of overspend protection.** Each guard was broken temporarily to prove the concurrency test can fail:

| Mutation | Result |
|---|---|
| Availability condition in `try_reserve` → `AND 1 = 1` | 3 tests fail; overspend stopped only by the DB `CHECK` (as `IntegrityError` crashes, not `INSUFFICIENT_FUNDS`) |
| `BEGIN IMMEDIATE` → deferred `BEGIN` | Concurrency test fails with `database is locked` |
| Restored | all tests pass |

**c) Brief-vs-code cross-check.** Prompt: *"once again cross check the document file with our current code in detail; implement if anything is missed"*. Probing the running server found that SPEC §3.4 required malformed ids → VALIDATION_ERROR, but `get_request("")` returned NOT_FOUND (fixed). It also found behaviors that were correct but untested: spoofed `role`/`staff_id` arguments, stdout purity, and concurrent duplicate payments. Added AC-20…23 (SPEC v1.2). **Evidence:** probe output; 94 tests pass.

**Corrections (honest record):** the first mutation attempt deleted a line that also held the SQL string's closing `"""` (SyntaxError, not a valid mutation) — redone as a substitution. A regex rewriting tool return types over-matched one helper — caught in grep output before commit. The first raw-stdio test closed stdin before a tool call finished and lost a response — a harness bug, fixed by reading responses first.
