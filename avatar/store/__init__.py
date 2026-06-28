"""State stores. ``build_store`` picks an implementation from config."""

from __future__ import annotations

from ..config import StoreConfig
from ..core.store import Store
from .memory import MemoryStore
from .sqlite import SQLiteStore


def build_store(cfg: StoreConfig) -> Store:
    if cfg.type == "memory":
        return MemoryStore()
    if cfg.type == "sqlite":
        return SQLiteStore(cfg.path)
    raise ValueError(f"unknown store type: {cfg.type!r}")


__all__ = ["MemoryStore", "SQLiteStore", "Store", "build_store"]
