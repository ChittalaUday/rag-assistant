SHELL := /bin/bash
.PHONY: setup env db-create migrate migrate-data dev check test evaluate ingest reindex export eval-retrieval eval-extraction
setup: env
	uv sync --extra documents
	npm --prefix frontend ci
	$(MAKE) db-create migrate
env:
	@test -f backend/.env || cp backend/.env.example backend/.env
	@test -f frontend/.env || cp frontend/.env.example frontend/.env
# Creates the app and test databases (and the vector/trigram extensions) if missing.
# pgvector must be installed for your PostgreSQL (macOS: brew install pgvector).
db-create:
	@for db in knowledge_engine knowledge_engine_test; do \
	  psql -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$$db'" | grep -q 1 || createdb $$db; \
	  psql -d $$db -qc "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"; \
	done
migrate:
	uv run --no-sync alembic -c backend/alembic.ini upgrade head
# One-time copy of the legacy data/assistant.sqlite3 into PostgreSQL.
migrate-data:
	PYTHONPATH=backend uv run --no-sync python scripts/migrate_sqlite_to_postgres.py
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
	uv run --no-sync pytest --cov=knowledge_engine --cov-report=term-missing
evaluate:
	uv run --no-sync python scripts/evaluate.py

# Pipeline commands (no web app needed): index documents, rebuild derived data, export outputs.
#   make ingest PATHS="fixtures/documents fixtures/testing" LABELS=synthetic
ingest:
	PYTHONPATH=backend uv run --no-sync python -m knowledge_engine ingest $(PATHS) $(if $(LABELS),--labels $(LABELS))
reindex:
	PYTHONPATH=backend uv run --no-sync python -m knowledge_engine reindex $(ARGS)
export:
	PYTHONPATH=backend uv run --no-sync python -m knowledge_engine export $(or $(OUT),out)
# Stage evaluations: retrieval (no LLM except follow-up rewriting) and extraction vs gold labels.
eval-retrieval:
	PYTHONPATH=backend uv run --no-sync python -m knowledge_engine.evaluation retrieval $(ARGS)
eval-extraction:
	PYTHONPATH=backend uv run --no-sync python -m knowledge_engine.evaluation extraction -v $(ARGS)
