"""Input sanitization tests — slug whitelist, prompt cleaner, rate
limiter. These guard the deployed-on-cloud surface where untrusted
input meets subprocess spawns and the Anthropic API key."""
from __future__ import annotations

import pytest

from fastcad.agent.research import (
    InvalidSlugError,
    InvalidTopicError,
    sanitize_topic,
    validate_slug,
)
from fastcad.server.ws import (
    WSContext,
    _check_rate_limit,
    _clean_prompt_text,
    _MAX_PROMPT_CHARS,
    _RATE_BURST_MAX,
)


# ---------------------------------------------------------------------------
# slug whitelist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", [
    "m6-hex-head-bolt",
    "nema-17-stepper",
    "iso7045",
    "a",
    "a-b-c-1-2-3",
])
def test_validate_slug_accepts_kebab_case(slug):
    assert validate_slug(slug) == slug


@pytest.mark.parametrize("slug", [
    "",
    "-leading-dash",
    "UPPER",
    "with space",
    "with/slash",
    "../escape",
    "..\\escape",
    "with.dot",
    "with_underscore",
    "ñon-ascii",
    "x" * 65,    # over 64-char cap
])
def test_validate_slug_rejects_attacks(slug):
    with pytest.raises(InvalidSlugError):
        validate_slug(slug)


def test_validate_slug_rejects_non_string():
    with pytest.raises(InvalidSlugError):
        validate_slug(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# topic sanitizer
# ---------------------------------------------------------------------------


def test_sanitize_topic_strips_control_chars():
    assert sanitize_topic("hello\x00world") == "helloworld"
    assert sanitize_topic("ansi\x1b[31mred") == "ansi[31mred"


def test_sanitize_topic_collapses_whitespace():
    assert sanitize_topic("  m6   hex   bolt  ") == "m6 hex bolt"
    assert sanitize_topic("line1\nline2") == "line1 line2"


def test_sanitize_topic_rejects_empty():
    with pytest.raises(InvalidTopicError):
        sanitize_topic("")
    with pytest.raises(InvalidTopicError):
        sanitize_topic("   \t\n  ")


def test_sanitize_topic_rejects_too_long():
    with pytest.raises(InvalidTopicError):
        sanitize_topic("a" * 250)


def test_sanitize_topic_rejects_non_string():
    with pytest.raises(InvalidTopicError):
        sanitize_topic(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# prompt cleaner
# ---------------------------------------------------------------------------


def test_clean_prompt_text_returns_clean():
    assert _clean_prompt_text("design an M6 bolt") == "design an M6 bolt"


def test_clean_prompt_text_strips_control():
    assert _clean_prompt_text("hello\x00world") == "helloworld"
    assert _clean_prompt_text("\x1bx") == "x"


def test_clean_prompt_text_keeps_unicode():
    # Unicode prose is fine; we only strip ASCII control chars.
    assert _clean_prompt_text("μέγεθος 5 mm") == "μέγεθος 5 mm"


def test_clean_prompt_text_rejects_empty():
    assert _clean_prompt_text("") is None
    assert _clean_prompt_text("   ") is None


def test_clean_prompt_text_rejects_oversize():
    assert _clean_prompt_text("x" * (_MAX_PROMPT_CHARS + 1)) is None


def test_clean_prompt_text_rejects_non_string():
    assert _clean_prompt_text(None) is None
    assert _clean_prompt_text(42) is None
    assert _clean_prompt_text({"text": "x"}) is None


# ---------------------------------------------------------------------------
# rate limiter
# ---------------------------------------------------------------------------


def test_rate_limit_allows_burst_then_blocks():
    ctx = WSContext()
    for _ in range(_RATE_BURST_MAX):
        assert _check_rate_limit(ctx) is None
    blocked = _check_rate_limit(ctx)
    assert blocked is not None
    assert "rate limit" in blocked
