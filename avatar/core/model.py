"""ModelProvider contract — the seam for Claude, OpenAI, Gemini, Ollama, ...

Providers must support *rolling* model discovery via ``list_models`` so new
models offered by a provider become usable without a code change. The selected
model string is validated against the live list rather than a hard-coded enum.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import GenerationRequest, GenerationResult, ModelInfo


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    async def list_models(self) -> list[ModelInfo]:
        """Fetch the provider's currently-available models (live, cacheable)."""
        ...

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        """Produce content for the given request."""
        ...

    async def healthcheck(self) -> bool: ...

    async def aclose(self) -> None: ...
