FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY frontend/ .
RUN pnpm build

FROM python:3.12-slim

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONIOENCODING=utf-8

WORKDIR /app

COPY backend/ backend/
RUN pip install --no-cache-dir -e backend/

RUN apt-get update && apt-get install -y --no-install-recommends curl procps && rm -rf /var/lib/apt/lists/*

COPY --from=frontend-build /app/frontend/out frontend/out
COPY config.yaml .
RUN mkdir -p \
    /app/backend/skills/public \
    /app/backend/skills/learned \
    /app/backend/skills/custom \
    /app/.agora

WORKDIR /app/backend

EXPOSE 8000

CMD ["python", "-m", "agora"]
