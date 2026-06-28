"""A tiny, namespaced registry so adapters plug in without core changes.

Adapters register a *factory* under a category and a name. The engine looks
the factory up by the ``type`` field in config and instantiates it with that
component's settings sub-dict. Adding a new platform/model/trigger/scanner is
therefore: write a class, decorate it, ship it — no edits to the engine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

T = TypeVar("T")

# category -> name -> factory(settings) -> instance
_REGISTRY: dict[str, dict[str, Callable[..., Any]]] = {
    "platform": {},
    "model": {},
    "trigger": {},
    "scanner": {},
}


class RegistryError(KeyError):
    pass


def _register(category: str, name: str, factory: Callable[..., Any]) -> None:
    bucket = _REGISTRY.setdefault(category, {})
    if name in bucket:
        raise RegistryError(f"{category} adapter {name!r} already registered")
    bucket[name] = factory


def register(category: str, name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Generic decorator: ``@register("platform", "bluesky")``."""

    def deco(factory: Callable[..., T]) -> Callable[..., T]:
        _register(category, name, factory)
        return factory

    return deco


# Convenience decorators per category ----------------------------------------
def register_platform(name: str):
    return register("platform", name)


def register_model(name: str):
    return register("model", name)


def register_trigger(name: str):
    return register("trigger", name)


def register_scanner(name: str):
    return register("scanner", name)


def create(category: str, name: str, settings: Mapping[str, Any] | None = None) -> Any:
    bucket = _REGISTRY.get(category, {})
    if name not in bucket:
        available = ", ".join(sorted(bucket)) or "<none>"
        raise RegistryError(f"unknown {category} adapter {name!r}; available: {available}")
    return bucket[name](settings or {})


def available(category: str) -> list[str]:
    return sorted(_REGISTRY.get(category, {}))


def clear() -> None:
    """Test helper — wipe registrations between test modules if needed."""
    for bucket in _REGISTRY.values():
        bucket.clear()
