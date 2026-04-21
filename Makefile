.PHONY: install dev cli test up down frontend

# Local development
install:
	cd backend && pip3 install -e ".[dev]"
	cd frontend && pnpm install

dev:
	cd backend && uvicorn agora.api.app:app --reload --host 0.0.0.0 --port 8000

dev-ui:
	cd frontend && pnpm dev --hostname 0.0.0.0

cli:
	cd backend && python3 -m agora

test:
	cd backend && python3 -m pytest tests/ -v --tb=short -m "not integration"

test-all:
	cd backend && python3 -m pytest tests/ -v --tb=short

frontend:
	cd frontend && pnpm build

# Docker
up:
	docker compose up -d agora-api

down:
	docker compose down

cli-docker:
	docker compose run --rm agora-cli
