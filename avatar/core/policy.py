"""Authorization and content helpers shared by the pipeline and triggers."""

from __future__ import annotations

import regex  # grapheme-aware


def _norm(handle: str) -> str:
    return handle.lstrip("@").strip().casefold()


def is_authorized(
    handle: str,
    *,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    allow_patterns: list[str] | None = None,
    deny_patterns: list[str] | None = None,
) -> bool:
    """Whitelist/blacklist decision for who may trigger the bot.

    Semantics: deny always wins. A non-empty allow list (literals or patterns)
    means *only* matches are permitted; an empty allow list means everyone
    except those denied. Handle matching is case-insensitive; patterns are RE2-
    style regular expressions matched against the normalised handle.
    """
    h = _norm(handle)
    deny = [_norm(x) for x in (deny or [])]
    allow = [_norm(x) for x in (allow or [])]

    if h in deny:
        return False
    for pat in deny_patterns or []:
        if regex.fullmatch(pat, h, flags=regex.IGNORECASE):
            return False

    has_allow = bool(allow or allow_patterns)
    if not has_allow:
        return True
    if h in allow:
        return True
    for pat in allow_patterns or []:
        if regex.fullmatch(pat, h, flags=regex.IGNORECASE):
            return True
    return False


def truncate_graphemes(text: str, max_chars: int) -> str:
    """Trim ``text`` to ``max_chars`` grapheme clusters, preferring a sentence
    or word boundary so posts don't end mid-word."""
    if max_chars <= 0:
        return ""
    clusters = regex.findall(r"\X", text)
    if len(clusters) <= max_chars:
        return text
    truncated = "".join(clusters[:max_chars])
    # Prefer the last sentence boundary, then the last whitespace.
    for sep in (". ", "! ", "? ", "\n"):
        idx = truncated.rfind(sep)
        if idx >= max_chars * 0.6:
            return truncated[: idx + 1].rstrip()
    idx = truncated.rfind(" ")
    if idx >= max_chars * 0.6:
        return truncated[:idx].rstrip() + "…"
    return truncated.rstrip()


def strip_foreign_mentions(text: str, keep_handle: str | None = None) -> str:
    """Remove ``@handles`` the model emitted, except the original author, to
    prevent the bot from spam-mentioning third parties."""
    keep = _norm(keep_handle) if keep_handle else None

    def _repl(m: regex.Match[str]) -> str:
        handle = m.group(1)
        if keep is not None and _norm(handle) == keep:
            return m.group(0)
        return handle  # drop the leading @, keep the word

    return regex.sub(r"@([A-Za-z0-9_.\-]+)", _repl, text)
