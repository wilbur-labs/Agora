"""Model registry — creates providers from config."""
from __future__ import annotations

from agora.config.settings import get_config
from .base import ModelProvider


_PROVIDER_MAP = {
    "claude-cli": "agora.models.providers:ClaudeCLIProvider",
    "gemini-cli": "agora.models.providers:GeminiCLIProvider",
    "kiro-cli": "agora.models.providers:KiroCLIProvider",
    "openai-api": "agora.models.openai_provider:OpenAIProvider",
    "azure-openai": "agora.models.openai_provider:AzureOpenAIProvider",
}


class ModelRegistry:
    def __init__(self):
        self._cache: dict[str, ModelProvider] = {}

    def get(self, name: str) -> ModelProvider:
        if name not in self._cache:
            self._cache[name] = self._create(name)
        return self._cache[name]

    def _create(self, name: str) -> ModelProvider:
        models = get_config().get("models", {})
        cfg = models.get(name)
        if not cfg:
            raise ValueError(f"Model '{name}' not in config")

        provider_type = cfg.get("provider", "")
        ref = _PROVIDER_MAP.get(provider_type)
        if not ref:
            raise ValueError(f"Unknown provider: {provider_type}")

        module_path, cls_name = ref.rsplit(":", 1)
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, cls_name)

        # Pass config kwargs for API providers
        if provider_type == "openai-api":
            return cls(
                api_key=cfg.get("api_key", ""),
                base_url=cfg.get("base_url", "https://api.openai.com/v1"),
                model=cfg.get("model", "gpt-4o"),
            )
        if provider_type == "azure-openai":
            return cls(
                api_key=cfg.get("api_key", ""),
                base_url=cfg.get("base_url", ""),
                deployment=cfg.get("deployment", ""),
                api_version=cfg.get("api_version", "2024-02-01"),
            )
        return cls()

    def list_models(self) -> list[str]:
        return list(get_config().get("models", {}).keys())


_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry


def reset_registry():
    global _registry
    _registry = None
