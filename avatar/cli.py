"""Command-line entrypoint (``avatar ...``)."""

from __future__ import annotations

import asyncio

import typer

from . import __version__
from .app import run_from_config
from .config import load_config

app = typer.Typer(help="avatar — a lightweight, trigger-based social media bot.")


@app.command()
def run(
    config: str = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Start the bot."""
    asyncio.run(run_from_config(config))


@app.command()
def validate(
    config: str = typer.Option("config.yaml", "--config", "-c"),
) -> None:
    """Validate a config file and print a summary."""
    cfg = load_config(config)
    typer.echo(
        f"OK: dry_run={cfg.dry_run} platforms={[p.id for p in cfg.platforms]} "
        f"schedules={[s.name for s in cfg.schedules]} mentions={cfg.mentions.enabled} "
        f"security={cfg.security.enabled}"
    )


@app.command()
def models(
    config: str = typer.Option("config.yaml", "--config", "-c"),
    provider: str = typer.Option(None, "--provider", "-p", help="Limit to one provider"),
) -> None:
    """List models discovered from configured providers (rolling discovery)."""
    from .app import Engine

    async def _list() -> None:
        cfg = load_config(config)
        engine = Engine(cfg)
        engine._build_models()
        for name, prov in engine.models.items():
            if provider and name != provider:
                continue
            try:
                discovered = await prov.list_models()
                for m in discovered:
                    typer.echo(f"{name}: {m.id}")
            except Exception as exc:
                typer.echo(f"{name}: <error: {exc}>")
            finally:
                await prov.aclose()

    asyncio.run(_list())


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
