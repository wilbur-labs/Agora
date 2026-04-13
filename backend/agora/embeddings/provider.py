"""Embedding provider — get vector representations of text."""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
import numpy as np

from agora.config.settings import get_config


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> np.ndarray:
        """Return shape (len(texts), dim) float32 array."""
        ...


class AzureEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *, api_key: str, base_url: str, deployment: str, api_version: str = "2024-12-01-preview"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.deployment = deployment
        self.api_version = api_version

    async def embed(self, texts: list[str]) -> np.ndarray:
        url = f"{self.base_url}/openai/deployments/{self.deployment}/embeddings?api-version={self.api_version}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                url,
                headers={"api-key": self.api_key, "Content-Type": "application/json"},
                json={"input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        vecs = [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
        return np.array(vecs, dtype=np.float32)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *, api_key: str, base_url: str = "https://api.openai.com/v1", model: str = "text-embedding-3-small"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def embed(self, texts: list[str]) -> np.ndarray:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"input": texts, "model": self.model},
            )
            resp.raise_for_status()
            data = resp.json()
        vecs = [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
        return np.array(vecs, dtype=np.float32)


def get_embedding_provider() -> EmbeddingProvider | None:
    cfg = get_config().get("embeddings", {})
    if not cfg.get("enabled"):
        return None
    provider_type = cfg.get("provider", "")
    if provider_type == "azure-openai":
        return AzureEmbeddingProvider(
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("base_url", ""),
            deployment=cfg.get("deployment", ""),
            api_version=cfg.get("api_version", "2024-12-01-preview"),
        )
    if provider_type == "openai-api":
        return OpenAIEmbeddingProvider(
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("base_url", "https://api.openai.com/v1"),
            model=cfg.get("model", "text-embedding-3-small"),
        )
    return None
