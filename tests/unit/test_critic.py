"""Critic module tests. Mocks both the renderer and the Anthropic
client so no real subprocess and no real API calls fire."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from fastcad.agent import critic as _critic
from fastcad.model.render import Render
from fastcad.model.validate import Defect


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeBlock:
    """Mimics anthropic.types.TextBlock — has .type and .text."""

    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, response_text: str):
        self._response_text = response_text
        self.last_call: dict | None = None

    def create(self, **kwargs):  # noqa: ANN001
        self.last_call = kwargs
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text: str):
        self.messages = _FakeMessages(response_text)


def _fake_renders(angles=("front", "top", "iso")) -> list[Render]:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    return [Render(angle=a, png_bytes=png, width=64, height=64) for a in angles]


def _fake_render_fn(renders: list[Render]):
    def fn(spec_source, bbox, **kwargs):  # noqa: ARG001
        return list(renders)
    return fn


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_review_no_defects_returns_empty():
    client = _FakeClient(json.dumps({"defects": []}))
    defects = _critic.review(
        spec_source="cube([1,1,1]);",
        cache_md="# fixture\n## Acceptance\n```json\n{}\n```\n",
        user_prompt="Make a small cube",
        bbox=(0, 0, 0, 1, 1, 1),
        client=client,
        render_fn=_fake_render_fn(_fake_renders()),
    )
    assert defects == []


def test_review_returns_structured_defects():
    response = json.dumps({
        "defects": [
            {
                "severity": "error",
                "where": "head — top view",
                "what": "head looks elliptical, expected round",
                "hint": "remove scale([2,1,1]) on the head module",
            },
        ]
    })
    client = _FakeClient(response)
    defects = _critic.review(
        spec_source="cube([1,1,1]);",
        cache_md="# fixture",
        user_prompt="Make a screw",
        bbox=(0, 0, 0, 5, 5, 20),
        client=client,
        render_fn=_fake_render_fn(_fake_renders()),
    )
    assert len(defects) == 1
    d = defects[0]
    assert d.severity == "error"
    assert "head" in d.where
    assert "elliptical" in d.actual
    assert "scale" in d.hint


def test_review_strips_json_fences():
    """Models sometimes wrap JSON in ```json fences. Critic must
    parse those out."""
    response = "```json\n" + json.dumps({"defects": [
        {"severity": "warning", "where": "x", "what": "y", "hint": "z"}
    ]}) + "\n```"
    client = _FakeClient(response)
    defects = _critic.review(
        spec_source="x", cache_md="x", user_prompt="x",
        bbox=(0, 0, 0, 1, 1, 1),
        client=client,
        render_fn=_fake_render_fn(_fake_renders()),
    )
    assert len(defects) == 1


def test_review_emits_progress_events():
    response = json.dumps({"defects": [
        {"severity": "error", "where": "shaft", "what": "bad", "hint": "fix"},
    ]})
    client = _FakeClient(response)
    events: list[dict] = []
    _critic.review(
        spec_source="x", cache_md="x", user_prompt="x",
        bbox=(0, 0, 0, 1, 1, 1),
        client=client,
        render_fn=_fake_render_fn(_fake_renders()),
        on_progress=events.append,
    )
    types = [e["type"] for e in events]
    assert "critic_started" in types
    # Vision defects have channel='vision' for the UI to label.
    vision = [e for e in events if e.get("type") == "validation_defect"]
    assert vision and vision[0].get("channel") == "vision"


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_review_no_renders_returns_warning():
    """Renderer returned nothing (openscad missing). Critic must
    surface a warning rather than block the turn."""
    client = _FakeClient("")
    defects = _critic.review(
        spec_source="x", cache_md="x", user_prompt="x",
        bbox=(0, 0, 0, 1, 1, 1),
        client=client,
        render_fn=_fake_render_fn([]),  # empty
    )
    assert len(defects) == 1
    assert defects[0].severity == "warning"
    assert "render" in defects[0].where


def test_review_unparseable_response_returns_warning():
    client = _FakeClient("not json at all")
    defects = _critic.review(
        spec_source="x", cache_md="x", user_prompt="x",
        bbox=(0, 0, 0, 1, 1, 1),
        client=client,
        render_fn=_fake_render_fn(_fake_renders()),
    )
    assert len(defects) == 1
    assert defects[0].severity == "warning"
    assert "critic_response" in defects[0].where


def test_review_api_error_returns_warning():
    """Anthropic API raises → critic surfaces a warning, doesn't
    block the agent's turn."""
    class BoomMessages:
        def create(self, **kwargs):
            raise RuntimeError("503 Service Unavailable")
    class BoomClient:
        messages = BoomMessages()

    defects = _critic.review(
        spec_source="x", cache_md="x", user_prompt="x",
        bbox=(0, 0, 0, 1, 1, 1),
        client=BoomClient(),
        render_fn=_fake_render_fn(_fake_renders()),
    )
    assert len(defects) == 1
    assert defects[0].severity == "warning"
    assert "critic_api" in defects[0].where


# ---------------------------------------------------------------------------
# Generic / no part-specific knowledge baked into the directive
# ---------------------------------------------------------------------------


def test_critic_directive_does_not_reference_specific_standards():
    """Per CLAUDE.md's anti-hardcoding rule: the critic prompt must
    not steer toward a specific standardized part by name. Worked
    examples in the directive would overfit the critic to whatever
    was named."""
    text = _critic.CRITIC_DIRECTIVE.lower()
    forbidden = ["m3 ", "m4 ", "m5 ", "din 912", "iso 4762", "nema "]
    for needle in forbidden:
        assert needle not in text, f"directive references {needle!r}"
