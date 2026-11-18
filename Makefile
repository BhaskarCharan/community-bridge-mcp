# Community Bridge MCP server — common commands. All run through uv (pinned via uv.lock).
.PHONY: install reset run test lint typecheck check demo

DB ?= data/community_bridge.db

install:          ## Install exact pinned dependencies (Python 3.12)
	uv sync --locked

reset:            ## Recreate and seed the SQLite DB (separate from server startup)
	uv run python -m community_bridge.seed --db $(DB)

run:              ## Start the MCP server on stdio; requires CB_STAFF_ID, e.g. make run CB_STAFF_ID=stf_bridge_cw
	CB_DB_PATH=$(DB) uv run python -m community_bridge

test:             ## Run the full test suite
	uv run pytest -v

lint:             ## Lint + formatting check
	uv run ruff check .
	uv run ruff format --check .

typecheck:        ## Static type check (also verifies Protocol implementations)
	uv run mypy

check: lint typecheck test   ## Verification gate: must be green before a task is done

demo:             ## Reset DB, then run the scripted MCP client demo (no LLM)
	$(MAKE) reset
	CB_DB_PATH=$(DB) uv run python scripts/demo.py
