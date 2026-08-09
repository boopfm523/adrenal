.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help setup fmt lint types imports test test-fast test-pg eval audit secrets frontend-generate frontend-check check up down logs migrate ready

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Install the pinned runtime and dependencies (ADR-0006)
	uv python install 3.13
	uv sync --dev
	cd frontend && npm ci --ignore-scripts
	uv run pre-commit install

fmt: ## Format
	uv run ruff format src tests migrations scripts
	uv run ruff check --fix src tests migrations scripts

lint: ## Lint
	uv run ruff check src tests migrations scripts
	uv run ruff format --check src tests migrations scripts

types: ## Type check
	uv run pyright

imports: ## Enforce module boundaries (ADR-0002)
	uv run lint-imports

test: ## Run the whole suite
	uv run pytest

test-fast: ## Skip tests needing a real PostgreSQL
	uv run pytest -m "not postgres and not slow"

test-pg: ## Only the tests that need real PostgreSQL constraints
	uv run pytest -m postgres

eval: ## Verify the checked-in local-model extraction regression baseline
	uv run python scripts/evaluate_extraction.py

audit: ## Dependency vulnerability scan (threat model T6)
	@# Audit the lockfile, not the installed environment: pip-audit cannot resolve
	@# the editable local package, and the lock is what CI and production install.
	uv export --format requirements-txt --no-emit-project --no-hashes --all-groups \
		-o .audit-requirements.txt
	uv run pip-audit --strict -r .audit-requirements.txt
	@rm -f .audit-requirements.txt
	cd frontend && npm audit --audit-level=high

secrets: ## Secret scan (SAFE-29)
	uv run detect-secrets scan --baseline .secrets.baseline

frontend-generate: ## Regenerate the committed OpenAPI contract and TypeScript client types
	uv run python scripts/export_openapi.py
	cd frontend && npm run generate:api

frontend-check: ## Verify OpenAPI drift, frontend lint/tests, and production build
	uv run python scripts/export_openapi.py --check
	cd frontend && npm run check

check: lint types imports test eval audit secrets frontend-check ## Everything CI runs

up: ## Start the local stack
	docker compose up -d --build

down: ## Stop the local stack
	docker compose down

logs: ## Tail the stack
	docker compose logs -f --tail=100

migrate: ## Apply migrations (never automatic on container start -- ADR-0002)
	uv run alembic upgrade head

ready: ## Next claimable Beads issue
	bd ready
