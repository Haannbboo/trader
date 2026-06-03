"""
ta.config — the seam between WHERE secrets live and WHAT an adapter receives.

The whole point: an adapter NEVER reads os.environ. ta-config reads the root
.env (secrets) + config/*.yaml (non-secret choices), merges them per source, and
hands each adapter a plain `params` dict through the registry. Swap .env for AWS
Secrets Manager later by changing ONLY the SecretProvider here — no adapter moves.

Path of an alpaca key:
    .env: ALPACA_API_KEY / ALPACA_API_SECRET
        -> EnvSecretProvider (prefix "ALPACA_" -> source "alpaca")
        -> AppConfig.source_params("account", "alpaca")  (merged with yaml)
        -> registry.build_sources("account", [SourceConfig(name="alpaca", params=...)])
        -> AlpacaAccountAdapter(**params)   # api_key/api_secret arrive as kwargs

Depends only on contracts + plugins (for SourceConfig). Pydantic-settings does
the .env parsing in the real impl.
"""

from __future__ import annotations


import os
import yaml
from typing import Optional, Protocol, runtime_checkable, Any
from loguru import logger
from pydantic import BaseModel, Field, model_validator

# Correct flat imports based on project structure
from contracts.errors import ConfigurationError
from plugins import SourceConfig


# ---------------------------------------------------------------------------
# Secret provider — the ONLY thing that touches the environment / a vault.
# ---------------------------------------------------------------------------
@runtime_checkable
class SecretProvider(Protocol):
    def get(self, key: str) -> Optional[str]:
        """Single secret by canonical key, e.g. 'ALPACA_API_KEY'."""
        ...

    def for_source(self, domain: str, name: str) -> dict[str, Any]:
        """All secrets belonging to one source, stripped of their prefix.
        e.g. ('account','alpaca') -> {'api_key': ..., 'api_secret': ...}
        """
        ...


class EnvSecretProvider:
    """Reads process env and optionally parses a local .env file.
    Convention:
      - Domain & source specific: <DOMAIN_UPPER>_<SOURCE_UPPER>_<PARAM_UPPER>
      - Source simple: <SOURCE_UPPER>_<PARAM_UPPER>
    """

    def __init__(self, *, env_file: str = ".env") -> None:
        self.secrets: dict[str, str] = {}
        # Simple self-contained .env parser to avoid third-party dependency issues
        if os.path.exists(env_file):
            try:
                with open(env_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip()
                            if (v.startswith('"') and v.endswith('"')) or (
                                v.startswith("'") and v.endswith("'")
                            ):
                                v = v[1:-1]
                            self.secrets[k] = v
            except Exception as e:
                logger.warning(f"Failed to read environment file {env_file}: {e}")

    def get(self, key: str) -> Optional[str]:
        return self.secrets.get(key) or os.getenv(key)

    def for_source(self, domain: str, name: str) -> dict[str, Any]:
        """Finds env/dotenv variables with matching prefixes, strips prefix, lowercases keys."""
        prefix_specific = f"{domain.upper()}_{name.upper()}_"
        prefix_simple = f"{name.upper()}_"

        merged_secrets = {}
        # Merge system env and .env file values
        all_vars = {**os.environ, **self.secrets}

        # 1. Check simple prefix first
        for k, v in all_vars.items():
            if k.upper().startswith(prefix_simple):
                param_name = k[len(prefix_simple) :].lower()
                merged_secrets[param_name] = v

        # 2. Check specific prefix second (overwrites simple if duplicate exists)
        for k, v in all_vars.items():
            if k.upper().startswith(prefix_specific):
                param_name = k[len(prefix_specific) :].lower()
                merged_secrets[param_name] = v

        return merged_secrets


# ---------------------------------------------------------------------------
# Non-secret config (from config/*.yaml) — matching project live/backtest yaml structure.
# ---------------------------------------------------------------------------
class SourceSettings(BaseModel):
    """One source's non-secret settings. Arbitrary yaml fields are nested into params."""

    name: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def populate_params(cls, data: Any) -> Any:
        """Dynamically moves extra yaml parameters under the params dict so it is clean."""
        if isinstance(data, dict):
            name = data.get("name")
            enabled = data.get("enabled", True)
            params = {
                k: v for k, v in data.items() if k not in ("name", "enabled", "params")
            }
            if "params" in data:
                params.update(data["params"])
            return {"name": name, "enabled": enabled, "params": params}
        return data


class AdaptersSettings(BaseModel):
    market: list[SourceSettings] = []
    news: list[SourceSettings] = []
    account: list[SourceSettings] = []


# ---------------------------------------------------------------------------
# infra — non-adapter infrastructure settings (bus, persistence, ...).
# Each sub-section is its own model so adding a new infra component is a
# one-class change.
# ---------------------------------------------------------------------------
class BusSettings(BaseModel):
    """Settings consumed by the event-bus implementation (RedisStreamBus today,
    maybe others later). `url` is the connection string passed to
    `redis.asyncio.from_url(...)`; `stream` and `maxlen` mirror the constructor
    kwargs of `RedisStreamBus` so the config and the runtime signature stay
    in lockstep."""

    url: Optional[str] = None
    stream: str = "trader:events"
    maxlen: Optional[int] = 100_000


class InfraSettings(BaseModel):
    """Non-adapter infrastructure (event bus, persistence, observability, ...).
    Each sub-section is its own model so adding a new infra component is a
    one-class change."""

    bus: BusSettings = Field(default_factory=BusSettings)


class Settings(BaseModel):
    """Refined Pydantic schema mapping exactly to config/*.yaml structures."""

    mode: str
    backtest: dict[str, Any] = Field(default_factory=dict)
    adapters: AdaptersSettings = Field(default_factory=AdaptersSettings)
    features: dict[str, list[SourceSettings]] = Field(default_factory=dict)
    guardrails: dict[str, Any] = Field(default_factory=dict)
    agent: dict[str, Any] = Field(default_factory=dict)
    infra: InfraSettings = Field(default_factory=InfraSettings)


# ---------------------------------------------------------------------------
# AppConfig — merges yaml + secrets into SourceConfig schemas.
# ---------------------------------------------------------------------------
class AppConfig:
    def __init__(self, settings: Settings, secrets: SecretProvider) -> None:
        self.settings = settings
        self.secrets = secrets

    @classmethod
    def load(cls, yaml_path: str, *, env_file: str = ".env") -> AppConfig:
        """Reads yaml -> Settings, builds EnvSecretProvider(env_file)."""
        if not os.path.exists(yaml_path):
            fallback_path = os.path.join("..", "..", yaml_path)
            if os.path.exists(fallback_path):
                yaml_path = fallback_path
            else:
                raise ConfigurationError(f"Configuration file not found: {yaml_path}")

        try:
            with open(yaml_path, "r") as f:
                config_data = yaml.safe_load(f) or {}
                logger.info(f"Loaded config from {yaml_path}")
        except Exception as e:
            raise ConfigurationError(f"Failed to parse config file '{yaml_path}': {e}")

        settings = Settings.model_validate(config_data)
        secrets = EnvSecretProvider(env_file=env_file)
        return cls(settings, secrets)

    def source_params(self, domain: str, name: str) -> dict[str, Any]:
        """Merges yaml non-secret parameters with the secret parameters.
        Secrets override YAML parameters if names collide.
        """
        yaml_params = {}
        found = False

        if domain in ("market", "news", "account"):
            sources = getattr(self.settings.adapters, domain, [])
            for src in sources:
                if src.name.lower() == name.lower():
                    yaml_params = dict(src.params)
                    found = True
                    break
        elif domain == "feature":
            for cat, sources in self.settings.features.items():
                for src in sources:
                    if src.name.lower() == name.lower():
                        yaml_params = dict(src.params)
                        found = True
                        break
                if found:
                    break

        secret_params = self.secrets.for_source(domain, name)
        return {**yaml_params, **secret_params}

    def enabled_sources(self, domain: str) -> list[SourceConfig]:
        """Builds a flat SourceConfig list for all enabled adapters in a domain."""
        if not hasattr(self.settings.adapters, domain):
            return []

        enabled = []
        sources = getattr(self.settings.adapters, domain)
        for src in sources:
            if src.enabled:
                merged_params = self.source_params(domain, src.name)
                enabled.append(SourceConfig(name=src.name, params=merged_params))
        return enabled

    def enabled_features(self) -> list[SourceConfig]:
        """Builds a flat SourceConfig list for all enabled features across categories."""
        enabled = []
        for category, sources in self.settings.features.items():
            for src in sources:
                if src.enabled:
                    merged_params = self.source_params("feature", src.name)
                    enabled.append(SourceConfig(name=src.name, params=merged_params))
        return enabled
