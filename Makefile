.PHONY: install dev cli test up down frontend

# Local development
install:
	cd backend && uv sync --locked --extra dev
	cd frontend && corepack pnpm install --frozen-lockfile

dev:
	cd backend && uv run uvicorn agora.api.app:app --host 127.0.0.1 --port 8000

dev-ui:
	cd frontend && corepack pnpm dev --hostname 127.0.0.1

cli:
	cd backend && uv run python -m agora

test:
	cd backend && uv run pytest tests/ -v --tb=short -m "not integration"

test-all:
	cd backend && uv run pytest tests/ -v --tb=short

frontend:
	cd frontend && corepack pnpm build

# Docker
up:
	docker compose up -d agora-api

down:
	docker compose down

cli-docker:
	docker compose run --rm agora-cli
