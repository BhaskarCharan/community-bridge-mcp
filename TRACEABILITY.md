# Requirement → Implementation → Test

Paths are relative to `src/community_bridge/` (code) and `tests/` (tests). All listed tests pass (see README "Results").

| Requirement | Implementation | Tests |
|---|---|---|
| REQ-ID-1 identity from `CB_STAFF_ID`, resolved server-side per call | `identity.py` `staff_id_from_env`, `resolve_caller`; `service.py` `_authorize`; `server.py` `build_server(service, staff_id)` | `test_permissions::test_all_roles_can_read`, `test_e2e_mcp::test_e2e_discovery_and_full_lifecycle` |
| REQ-ID-2 missing/unknown → UNAUTHENTICATED | `identity.py` `resolve_caller`; `bootstrap.py` `main` (warns, keeps serving) | `test_permissions::test_ac13_missing_or_unknown_identity_is_rejected`, `test_e2e_mcp::test_e2e_missing_identity_is_rejected` |
| REQ-ID-3 inputs/text grant nothing | No identity params on any tool (`server.py`); tenant/creator taken from `Caller` (`service.create_request`) | `test_service::test_ac16_prompt_injection_text_is_inert` |
| REQ-AUTH-1..5 role permissions | `policy.py` `ROLE_PERMISSIONS`, `require`; each service method declares its `Permission` | `test_permissions::test_ac10_roles_cannot_exceed_permissions`, `::test_all_roles_can_read`, E2E (CW approve → FORBIDDEN) |
| REQ-TEN-1 tenant scoping, NOT_FOUND across tenants | `ports.py` (all repo methods take `nonprofit_id`); `sqlite_store.py` queries; `service._load_request` | `test_permissions::test_ac11_other_nonprofit_cannot_see_or_act_on_requests`, `::test_tenant_lists_are_isolated` |
| REQ-TEN-2 participant & fund in caller's tenant | `service.create_request` lookups; composite FKs in `sqlite_store.SCHEMA` | `test_permissions::test_ac11_cannot_create_with_other_tenant_records` |
| REQ-CRT-1/2 create PENDING, no reservation | `service.create_request` | `test_service::test_ac2_submit_reserves_nothing` |
| REQ-CRT-3 validation | `domain.parse_amount`; `service._validate_text`, `_parse_enum` | `test_service::test_ac14_*`, `test_domain::test_parse_amount_*`, `test_e2e_mcp::test_e2e_invalid_amount_is_validation_error` |
| REQ-CRT-4 idempotent create | `service.create_request`; `UNIQUE (nonprofit_id, idempotency_key)` | `test_service::test_ac15_idempotent_create` |
| REQ-CRT-5 immutable | No edit tool/repo method; only `mark_decided`/`mark_paid` update rows | `test_e2e_mcp` (tool list is exactly 11, none edit) |
| REQ-ST-1 transitions | `domain.TRANSITIONS`, `can_transition`; `service._ensure_transition`; SQL `WHERE status = ?` guards | `test_domain::test_transition_table_matches_spec`, `test_service::test_ac6/7/8/9_*` |
| REQ-ST-2 actor + timestamp | `mark_decided`, `mark_paid` | `test_service::test_ac1_full_lifecycle_matches_business_example` |
| REQ-FUND-1/2 formula & invariants | `domain.Fund.available_cents`; `CHECK` constraints | `test_service::test_ac19_*`, `support.assert_balances_match_requests` |
| REQ-APR-1/2 atomic reserve; insufficient → unchanged | `service.approve_request`; `SqliteFundRepository.try_reserve`; `SqliteUnitOfWork` (BEGIN IMMEDIATE, rollback) | `test_service::test_ac1_*`, `::test_ac4_*`, `::test_ac5_*` |
| REQ-APR-3 no overspend under concurrency | `try_reserve` conditional UPDATE + `BEGIN IMMEDIATE` | `test_concurrency::test_ac12_concurrent_approvals_never_overspend` (+ mutation check, AI_WORKFLOW.md) |
| REQ-APR-4 re-approve is a no-op error | status guard in `service` + `mark_decided` | `test_service::test_ac6_repeated_approval_does_not_double_reserve` |
| REQ-REJ-1 reject leaves funds | `service.reject_request` | `test_service::test_ac3_*` |
| REQ-PAY-1/2 pay once, committed → paid | `service.record_payment`; `mark_paid`; `settle_payment` | `test_service::test_ac1_*`, `::test_ac7_*`, `::test_ac8_*`, E2E payment retry |
| REQ-MON-1 integer cents, string I/O | `domain.parse_amount`, `format_cents`; `schemas.py` money as strings | `test_domain::*`, E2E summary strings |
| REQ-READ-1..4 reads | `service` read methods; `server.py` read tools | `test_service::test_reads_*`, `test_permissions::test_all_roles_can_read`, E2E summary |
| REQ-OPS-1 stdio, discoverable, output schemas | `server.py` (`Annotated[CallToolResult, Model]`), `bootstrap.main` | `test_e2e_mcp::test_e2e_discovery_and_full_lifecycle` |
| REQ-OPS-2 SQLite persistence | `sqlite_store.py` | all service tests (real temp SQLite files) |
| REQ-OPS-3 separate reset | `seed.py`, `make reset`; server exits if DB missing | `test_seed::test_ac18_reset_restores_clean_seed` |
| REQ-OPS-4 logs on stderr only | `bootstrap.configure_logging`; ruff rule `T20` bans `print` in `src` | E2E sessions (stdout must be pure JSON-RPC) |
| REQ-OPS-5 pinned deps | `pyproject.toml` (`==`), `uv.lock`, `.python-version` | `make install` (`uv sync --locked`) |
| REQ-OPS-6 seed data | `seed.py` | `test_seed::test_seed_has_two_nonprofits_with_all_roles` |
