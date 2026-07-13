.PHONY: install dev cli test up down frontend

# Local development
install:
	cd backend && uv sync --extra dev
	cd frontend && pnpm install

dev:
	cd backend && uv run uvicorn agora.api.app:app --reload --host 0.0.0.0 --port 8000

dev-ui:
	cd frontend && pnpm dev --hostname 0.0.0.0

cli:
	cd backend && uv run python -m agora

test:
	cd backend && uv run pytest tests/ -v --tb=short -m "not integration"

test-all:
	cd backend && uv run pytest tests/ -v --tb=short

frontend:
	cd frontend && pnpm build

# Docker
up:
	docker compose up -d agora-api

down:
	docker compose down

cli-docker:
	docker compose run --rm agora-cli
