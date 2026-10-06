# Variables
VENV_BIN = .venv/bin
PYTHON = $(VENV_BIN)/python
PYTEST = $(VENV_BIN)/pytest
RUFF = $(VENV_BIN)/ruff
MYPY = $(VENV_BIN)/mypy

# Clean environment wrapper for running tools without ROS conflicts
RUN_CLEAN = env -i HOME=$(HOME) PATH=$(PWD)/.venv/bin:/usr/local/bin:/usr/bin:/bin PYTHONPATH=$(PWD)/src:$(PWD)

# Installation
.PHONY: venv
venv:
	python3 -m venv .venv
	$(RUN_CLEAN) pip install --upgrade pip
	$(RUN_CLEAN) pip install -e ".[dev]"

.PHONY: install
install:
	$(RUN_CLEAN) pip install -e ".[dev]"

# Code quality
.PHONY: lint
lint:
	$(RUN_CLEAN) ruff check src tests
	$(RUN_CLEAN) ruff format --check src tests

.PHONY: format
format:
	$(RUN_CLEAN) ruff format src tests
	$(RUN_CLEAN) ruff check --fix src tests

.PHONY: typecheck
typecheck:
	$(RUN_CLEAN) mypy src

# Testing
.PHONY: test
test:
	$(RUN_CLEAN) pytest -m "not slow and not llm" --cov=reviewlens --cov-report=term-missing

.PHONY: test-all
test-all:
	$(RUN_CLEAN) pytest --cov=reviewlens --cov-report=term-missing

.PHONY: test-integration
test-integration:
	$(RUN_CLEAN) pytest -m integration

.PHONY: test-llm
test-llm:
	$(RUN_CLEAN) pytest -m llm

# Data pipeline
.PHONY: data
data: fetch clean build-warehouse

.PHONY: fetch
fetch:
	@mkdir -p data/raw
	$(RUN_CLEAN) python scripts/fetch_reviews.py --app-id com.nvsgames.snap --game "Marvel Snap" --target 8000 --out data/raw/marvel_snap.jsonl
	$(RUN_CLEAN) python scripts/fetch_reviews.py --app-id com.devsisters.ck --game "Cookie Run: Kingdom" --target 8000 --out data/raw/cookie_run_kingdom.jsonl

.PHONY: clean
clean:
	$(RUN_CLEAN) python scripts/clean_reviews.py

.PHONY: build-warehouse
build-warehouse:
	$(RUN_CLEAN) python scripts/build_warehouse.py

# Ingestion
.PHONY: ingest
ingest:
	$(RUN_CLEAN) python scripts/ingest.py --rebuild

# Evaluation
.PHONY: eval
eval: eval-retrieval eval-sql eval-e2e

.PHONY: eval-retrieval
eval-retrieval:
	$(RUN_CLEAN) python scripts/run_eval.py --suite retrieval

RUNS ?= 1

.PHONY: eval-sql
eval-sql:
	$(RUN_CLEAN) python scripts/run_eval.py --suite sql --runs $(RUNS)

.PHONY: eval-e2e
eval-e2e:
	$(RUN_CLEAN) python scripts/run_eval.py --suite e2e --runs $(RUNS)

# Development server
.PHONY: serve
serve:
	$(RUN_CLEAN) uvicorn reviewlens.api.main:app --reload --host 0.0.0.0 --port 8000

# Docker
.PHONY: docker-build
docker-build:
	docker build -t reviewlens .

.PHONY: docker-up
docker-up:
	docker compose up -d

.PHONY: docker-down
docker-down:
	docker compose down

# Deployment
.PHONY: deploy
deploy:
	gcloud run deploy reviewlens --source . --region asia-south1 --allow-unauthenticated \
	  --memory 2Gi --cpu 1 --concurrency 8 --timeout 120 --max-instances 2 \
	  --set-env-vars APP_ENV=prod,QDRANT_COLLECTION=reviews \
	  --set-secrets GEMINI_API_KEY=gemini-api-key:latest,QDRANT_URL=qdrant-url:latest,QDRANT_API_KEY=qdrant-api-key:latest,INGEST_API_KEY=ingest-api-key:latest

# Reports
.PHONY: report
report:
	$(RUN_CLEAN) python scripts/report.py

# Cleanup
.PHONY: clean-data
clean-data:
	rm -rf data/raw/* data/processed/* data/warehouse/*

.PHONY: clean-results
clean-results:
	rm -rf eval/results/*

# Help
.PHONY: help
help:
	@echo "Available targets:"
	@echo "  install       Install dependencies in editable mode"
	@echo "  lint          Run linting checks"
	@echo "  format        Format code and auto-fix lint"
	@echo "  typecheck     Run mypy type checking"
	@echo "  test          Run fast unit tests with coverage"
	@echo "  test-all      Run all tests"
	@echo "  data          Run full data pipeline"
	@echo "  ingest        Rebuild vector search index"
	@echo "  eval          Run full evaluation suite"
	@echo "  serve         Start development server"
	@echo "  docker-build  Build Docker image"
	@echo "  docker-up     Start services with docker-compose"
	@echo "  deploy        Deploy to Cloud Run"
	@echo "  report        Generate evaluation report"
