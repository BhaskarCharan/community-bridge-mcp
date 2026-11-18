# Community Bridge MCP server — common commands. All run through uv (pinned via uv.lock).
.PHONY: install reset run test lint typecheck check demo package

DB ?= data/community_bridge.db

install:          ## Install exact pinned dependencies (Python 3.12)
	uv sync --locked

reset:            ## Recreate and seed the SQLite DB (separate from server startup)
	@uv run python -m community_bridge.seed --db $(DB)

# '@' prefixes matter: make would otherwise echo the command onto stdout = the MCP stream.
run:              ## Start the MCP server on stdio; requires CB_STAFF_ID, e.g. make run CB_STAFF_ID=stf_bridge_cw
	@CB_DB_PATH=$(DB) uv run python -m community_bridge

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
	@CB_DB_PATH=$(DB) uv run python scripts/demo.py

package:          ## Zip of committed files + git history (proves spec-before-code), no .venv/data
	rm -rf dist && mkdir -p dist
	git clone -q . dist/community-bridge
	git -C dist/community-bridge remote remove origin
	cd dist && zip -qr community-bridge.zip community-bridge && rm -rf community-bridge
	@echo "Created dist/community-bridge.zip"
