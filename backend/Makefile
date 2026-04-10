.PHONY: install dev cli test

install:
	pip install -e ".[dev]"

dev:
	uvicorn agora.api.app:app --reload --host 0.0.0.0 --port 8000

cli:
	python -m agora

test:
	pytest -q
