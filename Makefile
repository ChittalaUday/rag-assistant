SHELL := /bin/bash
.PHONY: setup dev check test evaluate
setup:
	uv sync --extra documents
	npm --prefix frontend ci
dev:
	./scripts/dev.sh
check:
	uv run --no-sync ruff check backend scripts
	uv run --no-sync ruff format --check backend scripts
	uv run --no-sync mypy
	npm --prefix frontend run lint
	npm --prefix frontend run check
	npm --prefix frontend run test
	npm --prefix frontend run build
test:
	uv run --no-sync pytest --cov=company_assistant --cov-report=term-missing
evaluate:
	uv run --no-sync python scripts/evaluate.py
