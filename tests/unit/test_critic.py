"""Multi-critic orchestrator + per-critic tests.

Mocks both the renderer and the Anthropic client so no real
subprocess and no real API calls fire. Each critic is exercised
individually; the orchestrator's parallel-dispatch + merge behaviour
is exercised once.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from fastcad.agent import critics
from fastcad.agent.critics import _common, general, threads
from fastcad.model.render import Render


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, response_text: str):
        self._text = response_text
        self.last_call: dict | None = None

    def create(self, **kwargs):
        self.last_call = kwargs
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, response_text: str):
        self.messages = _FakeMessages(response_text)


def _renders(angles=("front", "top", "iso")) -> list[Render]:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    return [Render(angle=a, png_bytes=png, width=64, height=64) for a in angles]


def _render_fn_returning(rs):
    def fn(spec_source, bbox, **kwargs):
        return list(rs)
    return fn


# ---------------------------------------------------------------------------
# Per-critic basic plumbing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [general, threads])
def test_critic_returns_empty_when_response_is_empty_defects(module):
    client = _FakeClient(json.dumps({"defects": []}))
    out = module.review(
        spec_source="cube([1,1,1]);",
        cache_md="# cache",
        user_prompt="cube",
        renders=_renders(),
        client=client,
    )
    assert out == []


@pytest.mark.parametrize("module", [general, threads])
def test_critic_parses_structured_defect(module):
    client = _FakeClient(json.dumps({
        "defects": [{
            "severity": "error",
            "where": "shaft",
            "what": "thread looks like stacked rings",
            "hint": "in module thread_helix(), change linear_extrude(twist=0) to twist=360*length/pitch",
        }]
    }))
    defects = module.review(
        spec_source="module thread_helix() { ... }",
        cache_md="# cache",
        user_prompt="screw",
        renders=_renders(),
        client=client,
    )
    assert len(defects) == 1
    d = defects[0]
    assert d.severity == "error"
    assert module.NAME in d.where  # tagged with critic name
    # The hint should keep its identifier reference (concrete-fix
    # requirement is set at the directive level; tests just check
    # the parser passes through whatever the model returned).
    assert "thread_helix" in d.hint


@pytest.mark.parametrize("module", [general, threads])
def test_critic_parses_json_in_fences(module):
    client = _FakeClient("```json\n" + json.dumps({"defects": [
        {"severity": "warning", "where": "x", "what": "y", "hint": "in module y(), change z"}
    ]}) + "\n```")
    out = module.review(
        spec_source="module y() {}",
        cache_md="# cache",
        user_prompt="x",
        renders=_renders(),
        client=client,
    )
    assert len(out) == 1


@pytest.mark.parametrize("module", [general, threads])
def test_critic_unparseable_response_returns_warning(module):
    client = _FakeClient("not JSON")
    defects = module.review(
        spec_source="x", cache_md="x", user_prompt="x",
        renders=_renders(), client=client,
    )
    assert len(defects) == 1
    assert defects[0].severity == "warning"
    assert module.NAME in defects[0].where


@pytest.mark.parametrize("module", [general, threads])
def test_critic_api_failure_surfaces_warning(module):
    class Boom:
        def create(self, **kwargs): raise RuntimeError("503")
    class C:
        messages = Boom()

    out = module.review(
        spec_source="x", cache_md="x", user_prompt="x",
        renders=_renders(), client=C(),
    )
    assert len(out) == 1
    assert out[0].severity == "warning"
    assert module.NAME in out[0].where


# ---------------------------------------------------------------------------
# Concrete-hint requirement at the directive level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [general, threads])
def test_critic_directive_demands_concrete_hints(module):
    """Every critic's directive must include the concrete-hint
    requirement (reference identifiers from source, code-level fix,
    no abstract advice). This is the option-3 enhancement: hints
    that reference the agent's actual modules / params, not vague
    advice."""
    text = module.DIRECTIVE.lower()
    # Both the explicit phrase and the structural clue should appear.
    assert "identifier" in text
    assert "abstract" in text
    assert "module" in text


@pytest.mark.parametrize("module", [general, threads])
def test_critic_directive_does_not_reference_specific_standards(module):
    """Per CLAUDE.md anti-hardcoding rule: no critic directive
    should name a specific standardized part."""
    text = module.DIRECTIVE.lower()
    forbidden = ["m3 ", "m4 ", "m5 ", "m6 ", "din 912", "iso 4762", "nema "]
    for needle in forbidden:
        assert needle not in text, f"{module.NAME!r} directive references {needle!r}"


# ---------------------------------------------------------------------------
# Orchestrator: parallel dispatch + merging
# ---------------------------------------------------------------------------


def test_orchestrator_runs_all_registered_critics_in_parallel():
    """review_all dispatches each enabled critic with the same
    inputs and merges all defects, tagged with critic names in
    progress events."""
    # Each critic returns one defect with a critic-specific marker.
    client_general = _FakeClient(json.dumps({"defects": [
        {"severity": "error", "where": "a", "what": "general defect", "hint": "in module x()"},
    ]}))
    client_threads = _FakeClient(json.dumps({"defects": [
        {"severity": "warning", "where": "b", "what": "thread defect", "hint": "in module thread()"},
    ]}))

    # Each call to client_factory returns a fresh client (one per
    # thread). To distinguish, we cycle through a list.
    clients = [client_general, client_threads]
    idx = {"i": 0}
    def factory():
        c = clients[idx["i"] % len(clients)]
        idx["i"] += 1
        return c

    events: list[dict] = []
    out = critics.review_all(
        spec_source="module x() { ... }",
        cache_md="# cache",
        user_prompt="something",
        bbox=(0, 0, 0, 1, 1, 1),
        on_progress=events.append,
        client_factory=factory,
        render_fn=_render_fn_returning(_renders()),
    )

    # Both critics produced one defect each → 2 total.
    assert len(out) == 2
    # Tags include both critic names.
    types_seen = [
        e.get("channel") for e in events
        if e.get("type") == "validation_defect"
    ]
    assert "vision:general" in types_seen
    assert "vision:threads" in types_seen
    # Bracketing events fire.
    types = [e["type"] for e in events]
    assert "critics_started" in types
    assert "critics_done" in types


def test_orchestrator_returns_warning_when_no_renders():
    """Renderer produced nothing — orchestrator surfaces a single
    warning, doesn't try to call any critic."""
    client_factory_calls = []
    def factory():
        client_factory_calls.append(1)
        return _FakeClient("")

    out = critics.review_all(
        spec_source="x",
        cache_md="x",
        user_prompt="x",
        bbox=(0, 0, 0, 1, 1, 1),
        client_factory=factory,
        render_fn=_render_fn_returning([]),  # no renders
    )
    assert len(out) == 1
    assert out[0].severity == "warning"
    assert "render" in out[0].where.lower()
    # Critics shouldn't have been instantiated.
    assert client_factory_calls == []


def test_orchestrator_only_filter_runs_subset(monkeypatch):
    """Setting `only=['general']` runs just the general critic, not
    threads."""
    threads_called = {"n": 0}

    def boom_threads_review(**kwargs):
        threads_called["n"] += 1
        return []

    monkeypatch.setattr(threads, "review", boom_threads_review)

    client = _FakeClient(json.dumps({"defects": []}))
    critics.review_all(
        spec_source="x",
        cache_md="x",
        user_prompt="x",
        bbox=(0, 0, 0, 1, 1, 1),
        client_factory=lambda: client,
        render_fn=_render_fn_returning(_renders()),
        only=["general"],
    )
    assert threads_called["n"] == 0


def test_orchestrator_critic_crash_tagged_but_others_run():
    """If one critic raises, the orchestrator surfaces a warning
    Defect for that critic but still runs the others."""
    crashing = _FakeClient(json.dumps({"defects": []}))  # ignored — see below

    # Stub the threads critic to raise.
    def boom(**kwargs):
        raise RuntimeError("boom from threads")

    with patch.object(threads, "review", boom):
        client_ok = _FakeClient(json.dumps({"defects": []}))
        out = critics.review_all(
            spec_source="x",
            cache_md="x",
            user_prompt="x",
            bbox=(0, 0, 0, 1, 1, 1),
            client_factory=lambda: client_ok,
            render_fn=_render_fn_returning(_renders()),
        )
    # One warning Defect from the crashed threads critic; general
    # produced none.
    assert len(out) == 1
    assert out[0].severity == "warning"
    assert "threads" in out[0].where.lower()


def test_orchestrator_env_var_filters_critics(monkeypatch):
    """FASTCAD_VISION_CRITICS allow-list narrows the registry."""
    monkeypatch.setenv("FASTCAD_VISION_CRITICS", "general")
    enabled = critics.enabled_critics()
    assert enabled == ["general"]

    monkeypatch.setenv("FASTCAD_VISION_CRITICS", "threads,general")
    enabled = critics.enabled_critics()
    assert set(enabled) == {"threads", "general"}


def test_orchestrator_no_filter_returns_all(monkeypatch):
    monkeypatch.delenv("FASTCAD_VISION_CRITICS", raising=False)
    enabled = set(critics.enabled_critics())
    assert "general" in enabled
    assert "threads" in enabled


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def test_build_user_message_includes_source_and_cache():
    msg = _common.build_user_message(
        user_prompt="make widget",
        cache_md="cache content",
        spec_source="cube([1,1,1]);",
        n_renders=3,
    )
    assert "make widget" in msg
    assert "cache content" in msg
    assert "cube([1,1,1])" in msg
    assert "3 renders" in msg


def test_persistence_detection_finds_repeated_keys():
    """detect_persistent_defects spots a defect class that repeats
    across the last N iterations."""
    history = [
        [{"where": "axial_consistency"}, {"where": "horizontal_slices_at_z[0].outer_protrusions"}],
        [{"where": "axial_consistency"}, {"where": "horizontal_slices_at_z[2].outer_protrusions"}],
        [{"where": "axial_consistency"}, {"where": "horizontal_slices_at_z[4].outer_protrusions"}],
    ]
    keys = critics.detect_persistent_defects(history, threshold=3)
    # axial_consistency appears in all three; the indexed
    # outer_protrusions collapse to the same prefix and also persist.
    assert "axial_consistency" in keys
    assert "horizontal_slices_at_z" in keys


def test_persistence_detection_below_threshold_returns_empty():
    history = [[{"where": "x"}], [{"where": "x"}]]
    assert critics.detect_persistent_defects(history, threshold=3) == []


def test_persistence_detection_streak_broken_returns_empty():
    """An empty defect list mid-streak breaks persistence — the
    agent fixed something."""
    history = [
        [{"where": "x"}],
        [],   # agent passed an iteration
        [{"where": "x"}],
    ]
    assert critics.detect_persistent_defects(history, threshold=3) == []


def test_fixit_critic_skips_when_no_prior_defects():
    """Without prior defects, the fix-it critic returns nothing —
    it's escalation-only."""
    from fastcad.agent.critics import fixit
    out = fixit.review(
        spec_source="x", cache_md="x", user_prompt="x",
        renders=_renders(),
        client=_FakeClient("ignored"),
        prior_defects=None,
    )
    assert out == []


def test_fixit_critic_includes_prior_defects_in_directive():
    """The fix-it critic embeds a summary of prior defects in the
    directive so the model knows what's been tried and failed."""
    from fastcad.agent.critics import fixit
    captured: dict = {}

    class CapturingClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                captured["system"] = kwargs.get("system")
                return _FakeResponse(json.dumps({"defects": []}))
    fixit.review(
        spec_source="module shaft() { }",
        cache_md="# cache",
        user_prompt="screw",
        renders=_renders(),
        client=CapturingClient(),
        prior_defects=[
            [{"where": "axial_consistency", "actual": "stacked rings"}],
            [{"where": "axial_consistency", "actual": "stacked rings"}],
            [{"where": "axial_consistency", "actual": "stacked rings"}],
        ],
    )
    sys_text = captured["system"]
    assert "axial_consistency" in sys_text
    assert "iteration" in sys_text.lower()


def test_orchestrator_only_runs_fixit_when_persistence_detected():
    """fixit is in the registry but should NOT run in normal
    iterations. It fires only when prior_defects shows persistence."""
    from fastcad.agent.critics import fixit
    fixit_calls = {"n": 0}
    def fake_fixit_review(**kwargs):
        fixit_calls["n"] += 1
        return []
    with patch.object(fixit, "review", fake_fixit_review):
        # No prior defects → fixit must NOT run.
        critics.review_all(
            spec_source="x", cache_md="x", user_prompt="x",
            bbox=(0, 0, 0, 1, 1, 1),
            client_factory=lambda: _FakeClient(json.dumps({"defects": []})),
            render_fn=_render_fn_returning(_renders()),
            prior_defects=None,
        )
        assert fixit_calls["n"] == 0

        # Persistent prior defects → fixit DOES run.
        persistent = [[{"where": "axial_consistency"}]] * 3
        critics.review_all(
            spec_source="x", cache_md="x", user_prompt="x",
            bbox=(0, 0, 0, 1, 1, 1),
            client_factory=lambda: _FakeClient(json.dumps({"defects": []})),
            render_fn=_render_fn_returning(_renders()),
            prior_defects=persistent,
        )
        assert fixit_calls["n"] == 1


def test_parse_defects_handles_prose_around_json():
    text = "Here is my analysis.\n\n{\"defects\":[{\"severity\":\"error\",\"where\":\"x\",\"what\":\"y\",\"hint\":\"z\"}]}\n\nDone."
    out = _common.parse_defects(text, "test")
    assert len(out) == 1
    assert out[0].severity == "error"
