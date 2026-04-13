.PHONY: install dev cli test up down

# Local development
install:
	cd backend && pip install -e ".[dev]"

dev:
	cd backend && uvicorn agora.api.app:app --reload --host 0.0.0.0 --port 8000

cli:
	cd backend && python -m agora

test:
	cd backend && python -m pytest tests/ -v --tb=short -m "not integration"

test-all:
	cd backend && python -m pytest tests/ -v --tb=short

# Docker
up:
	docker compose up -d agora-api

down:
	docker compose down

cli-docker:
	docker compose run --rm agora-cli
