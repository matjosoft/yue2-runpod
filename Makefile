# Studio make targets (PRD section 10). Requires bash, uv and pnpm; see README.md.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV := uv run --locked
PNPM := pnpm -C web

.PHONY: help setup dev dev-server dev-web run-fake test lint typecheck contracts build-worker

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install Python and Node dependencies
	uv sync --locked
	$(PNPM) install --frozen-lockfile

dev: ## Run the server with reload and the Vite dev server
	$(MAKE) -j2 dev-server dev-web

dev-server:
	$(UV) uvicorn studio_server.main:create_app --factory --reload --reload-dir server \
		--host 127.0.0.1 --port 8080

dev-web:
	$(PNPM) dev

run-fake: ## Run a fake-engine worker and the server together
	scripts/run_fake.sh

test: ## Python tests, web tests, and the contracts-up-to-date check
	$(UV) pytest
	$(PNPM) test
	scripts/gen_api_types.sh --check

typecheck: ## mypy (strict) and tsc --noEmit
	$(UV) mypy
	$(UV) mypy server/tests
	$(PNPM) typecheck

lint: ## ruff, type checkers, ESLint
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(MAKE) typecheck
	$(PNPM) lint

contracts: ## Regenerate OpenAPI documents and TypeScript types
	scripts/gen_api_types.sh

build-worker: ## Build the worker image (from M4)
	scripts/build_worker.sh
