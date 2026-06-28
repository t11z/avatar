from __future__ import annotations

from avatar.core.policy import (
    is_authorized,
    strip_foreign_mentions,
    truncate_graphemes,
)


def test_deny_beats_allow():
    assert not is_authorized("alice", allow=["alice"], deny=["alice"])


def test_empty_allow_permits_all_except_deny():
    assert is_authorized("bob")
    assert not is_authorized("bob", deny=["bob"])


def test_allow_list_is_exclusive():
    assert is_authorized("alice", allow=["alice"])
    assert not is_authorized("carol", allow=["alice"])


def test_handle_match_is_case_insensitive_and_at_agnostic():
    assert is_authorized("@Alice", allow=["alice"])


def test_allow_patterns():
    assert is_authorized("team.bot", allow_patterns=[r"team\..*"])
    assert not is_authorized("other", allow_patterns=[r"team\..*"])


def test_truncate_graphemes_keeps_short_text():
    assert truncate_graphemes("short", 300) == "short"


def test_truncate_graphemes_trims_long_text():
    text = "Sentence one. Sentence two is much longer than the limit allows here."
    out = truncate_graphemes(text, 20)
    assert len(out) <= 21


def test_strip_foreign_mentions_keeps_author():
    out = strip_foreign_mentions("hi @alice and @bob", keep_handle="alice")
    assert "@alice" in out
    assert "@bob" not in out
    assert "bob" in out
