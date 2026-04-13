FROM python:3.12-slim

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONIOENCODING=utf-8

WORKDIR /app

COPY backend/pyproject.toml backend/
RUN pip install --no-cache-dir -e backend/

COPY backend/ backend/
COPY config.yaml .
COPY skills/ skills/

WORKDIR /app/backend

EXPOSE 8000

# Default: run CLI. Override with docker compose for API mode.
CMD ["python", "-m", "agora"]
