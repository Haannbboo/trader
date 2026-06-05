from __future__ import annotations

import importlib
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Optional,
    Sequence,
    Tuple,
    Type,
    TypeVar,
)

# Imports pointing to our actual contract schemas
from contracts.ports import Processor, SourcePort
from loguru import logger
from pydantic import BaseModel, Field

S = TypeVar("S", bound=SourcePort)

T = TypeVar("T")
Domain = str  # "market" | "news" | "account" | "feature"


class SourceConfig(BaseModel):
    """One enabled source from config. (Really lives in ta-config; kept here so
    the registry contract is self-contained.)

    Carries the same decomposed key shape as ``register(domain, source, name)``:
    ``source`` is the vendor/provider and ``name`` is the optional sub-name
    within that source.
    """

    source: str
    name: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def label(self) -> str:
        """Flat display label used in logs and compatibility helpers."""
        return f"{self.source}_{self.name}" if self.name else self.source


class Registry:
    """Process-wide map ``(domain, source, name) -> class`` populated by
    :py:func:`register`.

    The key hierarchy is:
      ``domain``                — which port family (market / account / ...)
      ``source``                — the vendor (alpaca / polygon / rss / ...)
      ``name`` (optional)       — a sub-name of that vendor (stock / option / ...)

    Plain single-segment sources (e.g. ``account/alpaca``) live in the
    ``name=""`` slot; flavored sources (e.g. ``market/alpaca/stock``) live in
    the ``name="stock"`` slot under ``market/alpaca``.
    """

    def __init__(self) -> None:
        # domain -> source -> {name ("" or "stock"/"option"/...): cls}
        self._map: Dict[Domain, Dict[str, Dict[str, Type[Any]]]] = {
            "market": {},
            "news": {},
            "account": {},
            "feature": {},
        }

    def register(
        self, domain: Domain, source: str, name: Optional[str] = None
    ) -> Callable[[Type[T]], Type[T]]:
        """Decorator. Records the class under (domain, source[, name])."""

        def decorator(cls: Type[T]) -> Type[T]:
            dom = domain.lower()
            src = source.lower()
            sub_name = (name or "").lower()
            self._map.setdefault(dom, {}).setdefault(src, {})[sub_name] = cls
            label = f"{dom}/{src}/{sub_name}" if sub_name else f"{dom}/{src}"
            logger.info(f"Registered Class: {label} -> {cls.__name__}")
            return cls

        return decorator

    def get(self, domain: Domain, source: str, name: Optional[str] = None) -> Type[Any]:
        """Look up a registered class. Raises KeyError if unknown."""
        dom = domain.lower()
        src = source.lower()
        sub_name = (name or "").lower()
        if (
            dom not in self._map
            or src not in self._map[dom]
            or sub_name not in self._map[dom][src]
        ):
            raise KeyError(
                f"Unknown plugin requested: domain='{domain}', "
                f"source='{source}', name={name!r}"
            )
        return self._map[dom][src][sub_name]

    def split_name(self, name: str) -> Tuple[str, Optional[str]]:
        """Decompose a flat registry label into ``(source, name)``.

        ``"alpaca"``       -> ``("alpaca", None)``
        ``"alpaca_stock"`` -> ``("alpaca", "stock")``
        """
        parts = name.split("_", 1)
        return parts[0], (parts[1] if len(parts) > 1 else None)

    def names(self, domain: Domain) -> list[str]:
        """All registered names in a domain as flat identifiers.

        The conformance suite uses this to parametrize per-source tests, so we
        keep the flat shape (``"alpaca"`` / ``"alpaca_stock"``) instead of
        nested ``[[source, name], ...]`` tuples.
        """
        out: list[str] = []
        for src, names in self._map.get(domain.lower(), {}).items():
            for name in names:
                out.append(f"{src}_{name}" if name else src)
        return out

    def build_sources(
        self,
        domain: Domain,
        enabled: Sequence[SourceConfig],
        *,
        as_: Type[S],
    ) -> list[S]:
        """Instantiate the enabled adapters for a domain from config.

        Each :class:`SourceConfig` carries the decomposed registry key directly,
        matching the ``register(domain, source, name)`` decorator shape.
        """
        instances: list[S] = []
        for cfg in enabled:
            try:
                cls = self.get(domain, cfg.source, cfg.name)
                inst = cls(**cfg.params) if cfg.params else cls()
                if not isinstance(inst, as_):
                    raise TypeError(
                        f"Adapter '{cfg.label}' (class {cls.__name__}) does not "
                        f"conform to {as_.__name__}"
                    )
                instances.append(inst)
            except Exception as e:
                logger.error(f"Failed to build adapter {domain}/{cfg.label}: {e}")
        return instances

    def build_processors(self, enabled: Sequence[SourceConfig]) -> list[Processor]:
        """Instantiate the enabled feature processors from config."""
        instances = []
        for cfg in enabled:
            try:
                cls = self.get("feature", cfg.source, cfg.name)
                inst = cls()
                inst.initialize(cfg.params)
                instances.append(inst)
            except Exception as e:
                logger.error(f"Failed to build feature processor '{cfg.label}': {e}")
        return instances


# Module-level singleton + thin convenience wrappers (what callers actually use).
registry = Registry()


def register(
    domain: Domain, source: str, name: Optional[str] = None
) -> Callable[[Type[T]], Type[T]]:
    """Sugar for ``registry.register(domain, source[, name])``.

    Single-segment sources pass just ``(domain, source)`` (e.g.
    ``@register("account", "alpaca")``). Source sub-names pass all three
    (e.g. ``@register("market", "alpaca", "stock")``).
    """
    return registry.register(domain, source, name)


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
