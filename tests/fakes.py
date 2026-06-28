"""In-process fakes for testing the pipeline without network access."""

from __future__ import annotations

from datetime import UTC, datetime

from avatar.core.types import (
    Capabilities,
    GenerationRequest,
    GenerationResult,
    Mention,
    ModelInfo,
    Post,
    PostResult,
    Ref,
    ScanRequest,
    ScanVerdict,
)


class FakeModel:
    name = "fake"

    def __init__(self, text: str = "hello world", refused: bool = False) -> None:
        self._text = text
        self._refused = refused
        self.calls: list[GenerationRequest] = []

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-1", provider=self.name)]

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        self.calls.append(req)
        return GenerationResult(
            text=self._text,
            model=req.model or "fake-1",
            provider=self.name,
            refused=self._refused,
            input_tokens=10,
            output_tokens=5,
        )

    async def healthcheck(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class FakePlatform:
    def __init__(self, name: str = "fake", max_chars: int = 300) -> None:
        self.name = name
        self._max_chars = max_chars
        self.posts: list[Post] = []
        self.replies: list[tuple[Post, Ref]] = []

    def capabilities(self) -> Capabilities:
        return Capabilities(max_chars=self._max_chars)

    async def post(self, content: Post) -> PostResult:
        self.posts.append(content)
        return PostResult(
            platform=self.name, post_id=f"p{len(self.posts)}", posted_at=datetime.now(UTC)
        )

    async def reply(self, content: Post, in_reply_to: Ref) -> PostResult:
        self.replies.append((content, in_reply_to))
        return PostResult(
            platform=self.name, post_id=f"r{len(self.replies)}", posted_at=datetime.now(UTC)
        )

    async def stream_mentions(self):  # pragma: no cover - not used in pipeline tests
        if False:
            yield Mention(platform=self.name, post_id="x", author_handle="y")

    async def healthcheck(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class FakeScanner:
    """Blocks any text containing one of ``bad_words``."""

    def __init__(self, name: str = "fake-scanner", bad_words: list[str] | None = None) -> None:
        self.name = name
        self.bad_words = bad_words or ["badword"]

    async def scan(self, req: ScanRequest) -> ScanVerdict:
        lowered = req.text.lower()
        for word in self.bad_words:
            if word in lowered:
                return ScanVerdict(
                    allowed=False, category="policy", reasons=[word], scanner=self.name
                )
        return ScanVerdict(allowed=True, scanner=self.name)

    async def aclose(self) -> None:
        return None
