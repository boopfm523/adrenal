.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help setup fmt lint types imports env-check pagination-check test test-fast test-pg eval audit secrets frontend-generate frontend-check check up down logs migrate ready qwen38-preflight qwen38-qualify qwen38-activate qwen3-rollback

QWEN38_MODEL := qwen3.8:27b-q8_0
QWEN38_QUALIFICATION := evals/candidates/qwen3.8-27b-q8_0/qualification.json

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Install the pinned runtime and dependencies (ADR-0006)
	uv python install 3.13
	uv sync --dev --extra documents
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

env-check: ## Reject supported environment variables missing from .env.example
	uv run python scripts/check_env_example.py

pagination-check: ## Reject unclassified record-list and table pagination surfaces
	uv run python scripts/check_pagination_inventory.py

test: ## Run the whole suite
	uv run pytest

test-fast: ## Skip tests needing a real PostgreSQL
	uv run pytest -m "not postgres and not slow"

test-pg: ## Only the tests that need real PostgreSQL constraints
	uv run pytest -m postgres

eval: ## Verify the checked-in local-model extraction regression baseline
	uv run python scripts/evaluate_extraction.py
	uv run python scripts/evaluate_vision.py
	uv run python scripts/evaluate_analysis.py
	uv run python scripts/evaluate_chatbot.py

qwen38-preflight: ## Check the non-default Qwen3.8 Q8 candidate locally
	uv run python scripts/preflight_ollama_candidate.py --model $(QWEN38_MODEL)

qwen38-qualify: ## Run all synthetic gates against Qwen3.8 Q8 without selecting it
	uv run python scripts/qualify_ollama_candidate.py --model $(QWEN38_MODEL)

qwen38-activate: ## Activate the qualified Qwen3.8 text model (owner approval required)
	uv run python scripts/select_ollama_model.py activate \
		--qualification $(QWEN38_QUALIFICATION)
	docker compose -f docker-compose.yml -f deploy/credentials.compose.yml \
		up -d --force-recreate api worker

qwen3-rollback: ## Restore qwen3:30b and recreate model-using services
	uv run python scripts/select_ollama_model.py rollback
	docker compose -f docker-compose.yml -f deploy/credentials.compose.yml \
		up -d --force-recreate api worker

audit: ## Dependency vulnerability scan (threat model T6)
	@# Audit the lockfile, not the installed environment: pip-audit cannot resolve
	@# the editable local package, and the lock is what CI and production install.
	uv export --format requirements-txt --no-emit-project --no-hashes --all-groups \
		--all-extras \
		-o .audit-requirements.txt
	uv run pip-audit --strict -r .audit-requirements.txt
	@rm -f .audit-requirements.txt
	cd frontend && npm audit --audit-level=high

secrets: ## Secret scan (SAFE-29)
	uv run detect-secrets scan --baseline .secrets.baseline \
		--exclude-files 'uv\.lock|\.beads/|^\.secrets\.history-reviews\.json$$'
	uv run python scripts/check_secret_baseline.py
	uv run python scripts/check_history_secrets.py

frontend-generate: ## Regenerate the committed OpenAPI contract and TypeScript client types
	uv run python scripts/export_openapi.py
	cd frontend && npm run generate:api

frontend-check: ## Verify OpenAPI drift, frontend lint/tests, and production build
	uv run python scripts/export_openapi.py --check
	cd frontend && npm run check

check: lint types imports env-check pagination-check test eval audit secrets frontend-check ## Everything CI runs

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
