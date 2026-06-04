from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, Iterable, Sequence, Type, TypeVar

# Imports pointing to our actual contract schemas
from contracts.ports import Processor, SourcePort
from loguru import logger
from pydantic import BaseModel

S = TypeVar("S", bound=SourcePort)

T = TypeVar("T")
Domain = str  # "market" | "news" | "account" | "feature"


class SourceConfig(BaseModel):
    """One enabled source from config. (Really lives in ta-config; kept here so
    the registry contract is self-contained.)"""

    name: str
    params: dict = {}


class Registry:
    """Process-wide map (domain, name) -> class. Populated by @register."""

    def __init__(self) -> None:
        self._map: Dict[Domain, Dict[str, Type[Any]]] = {
            "market": {},
            "news": {},
            "account": {},
            "feature": {},
        }

    def register(self, domain: Domain, name: str) -> Callable[[Type[T]], Type[T]]:
        """Decorator. Records the class under (domain, name); returns it unchanged."""

        def decorator(cls: Type[T]) -> Type[T]:
            dom_lower = domain.lower()
            if dom_lower not in self._map:
                self._map[dom_lower] = {}
            self._map[dom_lower][name.lower()] = cls
            logger.info(f"Registered Class: {domain}/{name} -> {cls.__name__}")
            return cls

        return decorator

    def get(self, domain: Domain, name: str) -> Type[Any]:
        """Look up a registered class. Raises if unknown."""
        dom_lower = domain.lower()
        name_lower = name.lower()
        if dom_lower not in self._map or name_lower not in self._map[dom_lower]:
            raise KeyError(
                f"Unknown plugin requested: domain='{domain}', name='{name}'"
            )
        return self._map[dom_lower][name_lower]

    def names(self, domain: Domain) -> list[str]:
        """All registered names in a domain — used by the parametrized conformance suite."""
        dom_lower = domain.lower()
        return list(self._map.get(dom_lower, {}).keys())

    def build_sources(
        self,
        domain: Domain,
        enabled: Sequence[SourceConfig],
        *,
        as_: Type[S],
    ) -> list[S]:
        """Instantiate the enabled adapters for a domain from config."""
        instances: list[S] = []
        for cfg in enabled:
            try:
                cls = self.get(domain, cfg.name)
                # Instantiate with parameters if defined, otherwise empty
                inst = cls(**cfg.params) if cfg.params else cls()
                if not isinstance(inst, as_):
                    raise TypeError(
                        f"Adapter '{cfg.name}' (class {cls.__name__}) does not conform to {as_.__name__}"
                    )
                instances.append(inst)
            except Exception as e:
                logger.error(f"Failed to build adapter {domain}/{cfg.name}: {e}")
        return instances

    def build_processors(self, enabled: Sequence[SourceConfig]) -> list[Processor]:
        """Instantiate the enabled feature processors from config."""
        instances = []
        for cfg in enabled:
            try:
                cls = self.get("feature", cfg.name)
                inst = cls()
                inst.initialize(cfg.params)
                instances.append(inst)
            except Exception as e:
                logger.error(f"Failed to build feature processor '{cfg.name}': {e}")
        return instances


# Module-level singleton + thin convenience wrappers (what callers actually use).
registry = Registry()


def register(domain: Domain, name: str) -> Callable[[Type[T]], Type[T]]:
    """Sugar for `registry.register(domain, name)`."""
    return registry.register(domain, name)


def discover(packages: Iterable[str]) -> None:
    """Import the given adapter/feature packages so their @register decorators
    run.
    """
    for pkg in packages:
        try:
            importlib.import_module(pkg)
            logger.debug(f"Successfully discovered/imported module: {pkg}")
        except Exception as e:
            logger.error(f"Failed to auto-discover/import package '{pkg}': {e}")
