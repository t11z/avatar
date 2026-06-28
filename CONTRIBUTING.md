# Contributing to avatar

Thanks for your interest in improving avatar! This guide covers setting up a dev
environment, running the checks, and adding a new adapter.

By participating you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Dev environment

avatar targets **Python 3.12+**. Set up a virtualenv and install the package in
editable mode with the `dev` and `all` extras (the latter pulls in every optional
provider/platform SDK so you can run the full test suite locally):

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev,all]"
```

The optional provider/platform SDKs are declared as extras in `pyproject.toml`
(`anthropic`, `openai`, `gemini`, `platforms`, `otel`). Adapter modules must
**lazy-import** their SDKs so the package imports cleanly even when an extra is
not installed.

## Running the checks

```bash
. .venv/bin/activate
ruff check .          # lint (rules E, F, I, UP, B, ASYNC, RUF; line length 100)
ruff format .         # optional auto-format
mypy avatar           # type-check
pytest -q             # tests
```

Please make sure `ruff check`, `mypy`, and `pytest` all pass before opening a PR.
Every source file should start with `from __future__ import annotations` and use
full type hints.

## Adding a new adapter

avatar discovers adapters through a small registry and verifies them with reusable
**contract tests**. Adding one is a self-contained change: a single new module
plus a single new test file. There are three adapter kinds:

| Kind     | Package            | Decorator                          | Protocol          | Contract            |
| -------- | ------------------ | ---------------------------------- | ----------------- | ------------------- |
| Platform | `avatar/platforms` | `@register_platform("<type>")`     | `PlatformAdapter` | `PlatformContract`  |
| Model    | `avatar/models`    | `@register_model("<provider>")`    | `ModelProvider`   | `ModelContract`     |
| Scanner  | `avatar/security`  | `@register_scanner("<type>")`      | `ContentScanner`  | `ScannerContract`   |

The package `__init__.py` files auto-discover submodules, so you do not need to
register your module anywhere by hand — just drop the file in.

### 1. Create the adapter module

Create one new file in the matching package, e.g. `avatar/models/mistral.py`:

```python
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from avatar.core.registry import register_model
from avatar.core.types import GenerationRequest, GenerationResult, ModelInfo


@register_model("mistral")
class MistralProvider:
    def __init__(self, settings: Mapping[str, Any]) -> None:
        # Read your keys here. Tolerate missing secrets at construction time —
        # the module must import even when the SDK is not installed and the
        # secret is absent. Fail lazily on first use instead.
        self.name = "mistral"
        self._api_key = settings.get("api_key")

    def _client(self):
        # Lazy-import the optional SDK INSIDE methods, never at module top level.
        import mistralai  # noqa: F401
        ...

    async def list_models(self) -> list[ModelInfo]: ...
    async def generate(self, req: GenerationRequest) -> GenerationResult: ...
    async def healthcheck(self) -> bool: ...
    async def aclose(self) -> None: ...
```

The engine instantiates every adapter as `cls(settings)`, where `settings` is the
already env-interpolated config dict for that component:

- **model providers** receive the provider settings, e.g. `{"api_key": "..."}`;
- **platform adapters** receive the platform config
  (`{id, type, enabled, handle, poll_interval_seconds, ...}`);
- **scanners** receive the scanner config (`{name, type, enabled, ...}`).

Always set `self.name: str` and read what you need from `settings`. Use
`httpx.AsyncClient` for HTTP and honor configured timeouts.

### 2. Add a contract test

Create `tests/test_mistral.py`, subclass the matching contract, and implement the
`make_*` factory with the **network layer mocked** (monkeypatch the SDK or patch
`httpx`) so the test passes with no network access and no real SDK installed:

```python
from __future__ import annotations

from tests.contract import ModelContract


class TestMistral(ModelContract):
    async def make_model(self):
        # construct your adapter with a mocked transport / SDK
        ...
```

Subclassing the contract inherits the conformance checks for free; add a couple of
adapter-specific tests too (request shaping, error handling, etc.). See
`tests/contract.py` for the available suites (`PlatformContract`,
`ModelContract`, `ScannerContract`) and `tests/fakes.py` for helpers.

### 3. Verify

```bash
. .venv/bin/activate
ruff check avatar/models/mistral.py tests/test_mistral.py
pytest tests/test_mistral.py -q
```

## Pull requests

- Keep changes focused; one adapter or fix per PR where possible.
- Update docs (`README.md`, `config.example.yaml`) if you add user-facing config.
- Fill in the PR template and make sure CI is green.
