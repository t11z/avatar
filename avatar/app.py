"""Engine: wires config + adapters + pipeline together and runs the event loop.

Adapter packages register themselves on import; the engine imports them
defensively so a slim build (missing an optional SDK) still starts with
whatever adapters are available.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
from pathlib import Path

from . import core  # noqa: F401 - ensures core package import
from .config import AppConfig, load_config
from .core import registry
from .core.model import ModelProvider
from .core.pipeline import Pipeline
from .core.platform import PlatformAdapter
from .core.security import ContentScanner
from .core.trigger import Trigger
from .obs.health import start_health_server
from .obs.logging import configure_logging, get_logger
from .obs.tracing import configure_tracing
from .store import build_store

log = get_logger("engine")

# Adapter packages whose import side-effects perform registration.
_ADAPTER_PACKAGES = (
    "avatar.models",
    "avatar.platforms",
    "avatar.security",
    "avatar.triggers",
)


def _load_adapter_packages() -> None:
    for pkg in _ADAPTER_PACKAGES:
        try:
            importlib.import_module(pkg)
        except ImportError as exc:
            log.debug("adapters.skip", package=pkg, error=str(exc))


class Engine:
    def __init__(self, config: AppConfig, base_dir: Path | None = None) -> None:
        self.config = config
        self.base_dir = base_dir
        self.store = build_store(config.store)
        self.platforms: dict[str, PlatformAdapter] = {}
        self.models: dict[str, ModelProvider] = {}
        self.scanners: list[ContentScanner] = []
        self.triggers: list[Trigger] = []
        self.pipeline: Pipeline | None = None
        self._tasks: list[asyncio.Task] = []

    async def setup(self) -> None:
        _load_adapter_packages()
        await self.store.init()
        self._build_models()
        self._build_platforms()
        self._build_scanners()
        self.pipeline = Pipeline(
            config=self.config,
            store=self.store,
            platforms=self.platforms,
            models=self.models,
            scanners=self.scanners,
            system_prompt=self.config.persona.resolved_system_prompt(self.base_dir),
        )
        self._build_triggers()

    # -- builders -------------------------------------------------------------
    def _provider_settings(self, provider: str) -> dict:
        extra = self.config.providers.model_dump()
        settings = dict(extra.get(provider) or {})
        return settings

    def _needed_providers(self) -> set[str]:
        providers = {self.config.model.provider}
        for sched in self.config.schedules:
            if sched.model:
                providers.add(sched.model.provider)
        if self.config.mentions.model:
            providers.add(self.config.mentions.model.provider)
        return providers

    def _build_models(self) -> None:
        for provider in self._needed_providers():
            try:
                self.models[provider] = registry.create(
                    "model", provider, self._provider_settings(provider)
                )
            except registry.RegistryError as exc:
                log.error("model.unavailable", provider=provider, error=str(exc))

    def _build_platforms(self) -> None:
        for pcfg in self.config.platforms:
            if not pcfg.enabled:
                continue
            try:
                self.platforms[pcfg.id] = registry.create("platform", pcfg.type, pcfg.model_dump())
            except registry.RegistryError as exc:
                log.error("platform.unavailable", platform=pcfg.type, error=str(exc))

    def _build_scanners(self) -> None:
        if not self.config.security.enabled:
            return
        for scfg in self.config.security.scanners:
            if not scfg.enabled:
                continue
            try:
                self.scanners.append(registry.create("scanner", scfg.type, scfg.model_dump()))
            except registry.RegistryError as exc:
                log.error("scanner.unavailable", scanner=scfg.type, error=str(exc))

    def _build_triggers(self) -> None:
        try:
            from .triggers import build_triggers
        except ImportError:
            log.warning("triggers.unavailable")
            return
        self.triggers = build_triggers(self.config, platforms=self.platforms, store=self.store)

    # -- lifecycle ------------------------------------------------------------
    async def _ready(self) -> bool:
        for adapter in self.platforms.values():
            with contextlib.suppress(Exception):
                if await adapter.healthcheck():
                    return True
        return not self.platforms  # nothing to authenticate => trivially ready

    async def run(self) -> None:
        assert self.pipeline is not None
        runner = await start_health_server(self.config.observability.health_addr, self._ready)
        log.info(
            "engine.start",
            dry_run=self.config.dry_run,
            platforms=list(self.platforms),
            models=list(self.models),
            triggers=[t.name for t in self.triggers],
        )
        try:
            for trigger in self.triggers:
                self._tasks.append(asyncio.create_task(trigger.run(self.pipeline.handle)))
            if self._tasks:
                await asyncio.gather(*self._tasks)
            else:
                log.warning("engine.no_triggers")
                await asyncio.Event().wait()
        finally:
            await runner.cleanup()
            await self.aclose()

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        for adapter in list(self.platforms.values()):
            with contextlib.suppress(Exception):
                await adapter.aclose()
        for provider in list(self.models.values()):
            with contextlib.suppress(Exception):
                await provider.aclose()
        for scanner in self.scanners:
            with contextlib.suppress(Exception):
                await scanner.aclose()
        await self.store.aclose()


async def run_from_config(path: str) -> None:
    config = load_config(path)
    configure_logging(config.log.level, config.log.format)
    configure_tracing(
        config.observability.tracing.enabled,
        config.observability.tracing.otlp_endpoint,
        config.observability.tracing.service_name,
    )
    base_dir = Path(path).resolve().parent  # noqa: ASYNC240 - one-off path resolution
    engine = Engine(config, base_dir=base_dir)
    await engine.setup()
    await engine.run()
