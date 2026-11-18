# AI-assisted workflow — three examples

Tool: Claude Code (Claude Opus) in VS Code, driven by `CLAUDE.md` (scope, invariants, layering) and `SPEC.md`. Every suggestion below was checked against independent evidence, not accepted on the AI's word.

## 1. Requirements / specification — storage choice

**Context / prompt (excerpt):** *"do we need to use db in this, is that mentioned in the doc, if we need to use db then use mongo db"*

**AI suggestion:** The brief says "persistent local storage", not "database", but it also requires *atomic approval + reservation* across two records (request status and fund balance). MongoDB supports that only via multi-document transactions, which need a replica set (even single-node), plus a running server for every reviewer. Recommended SQLite (stdlib, ACID, zero setup) and asked rather than overriding the stated preference.

**Decision:** SQLite chosen by the developer. Recorded in SPEC §4 / REQ-OPS-2.

**Independent evidence:** Re-read the brief's requirements ("Approval and budget reservation must be atomic", "Persistent local storage", "repeatable setup"); MongoDB documentation states multi-document transactions require a replica set or sharded cluster. The concurrency test (AC-12) later proved the SQLite design meets the atomicity requirement.

## 2. Implementation planning / coding — MCP SDK major version

**Prompt (excerpt):** *"Approved and continue with next steps"* — i.e. execute SPEC §8 tasks T1→T9 under `CLAUDE.md` rules ("use all the SOLID principles and standard LLD patterns … add meaningful comments"), with `make check` as the gate per task.

**Context:** The implementation plan assumed the Python SDK's `mcp.server.fastmcp.FastMCP` (v1 API, from the AI's prior knowledge).

**What happened:** `uv add mcp` resolved **mcp 2.3.0**. Before writing server code, the AI inspected the installed package instead of coding from memory: `mcp/server/fastmcp.py` now only raises `ModuleNotFoundError` ("FastMCP was renamed to MCPServer … other APIs changed"). It then read `MCPServer.tool`, `ToolError`, `func_metadata.convert_result` and `Client`/`StdioServerParameters` in the installed source and wrote the adapter against that.

**Correction:** v1 imports replaced with `from mcp.server.mcpserver import MCPServer` and `from mcp import Client`; added a note to `CLAUDE.md` so later agent tasks don't regress.

**Independent evidence:** installed source in `.venv/.../mcp/`; `make typecheck` (mypy strict) clean against the real SDK types; E2E test over a real stdio subprocess passes.

## 3. Debugging / verification — error contract and concurrency proof

**a) E2E failure.** First E2E run: 3 failures, `JSONDecodeError` when parsing error results. Prompted to inspect the raw result, the AI printed the `CallToolResult`:
`text='Error executing tool approve_request: {"error": {"code": "FORBIDDEN", ...}}'` — the SDK prefixes `ToolError` messages, breaking SPEC §5.3's "JSON body" contract. Reading `func_metadata.py` showed tools may return `Annotated[CallToolResult, OutputModel]`: the output schema is still published/validated, and the tool controls the error result. `server._invoke` now builds the result itself. **Evidence:** E2E tests assert exact error codes and pass; `list_tools` still reports an `output_schema` for all 11 tools.

**b) Mutation check of the overspend protection (AC-12).** To confirm the concurrency test can actually fail, each guard was broken temporarily, then restored:

| Mutation | Result |
|---|---|
| M1: availability condition in `try_reserve` replaced with `AND 1 = 1` | 3 tests fail. Concurrency test: overspending writes are stopped only by the DB `CHECK` constraint, surfacing as `IntegrityError` crashes instead of `INSUFFICIENT_FUNDS` |
| M2: `BEGIN IMMEDIATE` → deferred `BEGIN` (guard kept) | Concurrency test fails: `OperationalError('database is locked')` for competing approvals |
| Restored | 82/82 then passing |

**c) Brief-vs-code cross-check.** Prompt: *"once again cross check the document file with our current code in detail; implement if anything is missed"*. The AI re-read the brief clause by clause and probed the running server. Findings: SPEC §3.4 required malformed ids → VALIDATION_ERROR, but `get_request("")` returned NOT_FOUND (spec/code drift, fixed); "tool arguments must not grant permissions" and "logs separate from protocol" were true but untested (added AC-21 spoofed-argument E2E test and AC-23 raw JSON-RPC stdout test); duplicate payment/approval was only tested sequentially (added AC-20 concurrent tests). The new raw stdio test initially lost one response because it closed stdin before the tool call finished — a harness bug, fixed by reading responses before closing. **Evidence:** 94 tests pass; probe output showed spoofed `role`/`staff_id` arguments are ignored and FORBIDDEN is returned.

**Corrections during this work (honest record):** the first M1 attempt used `sed` to delete the guard line, which also deleted the SQL string's closing `"""` (SyntaxError, not a valid mutation) — redone as an in-place substitution. A regex used to rewrite tool return types over-matched one helper (`_transition_out`); caught in the grep output and reverted before commit.
