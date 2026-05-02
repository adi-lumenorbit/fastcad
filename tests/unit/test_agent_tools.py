"""Tests for the rewritten agent tool dispatcher and the deterministic
fake-mode loop. Real-mode (Anthropic) is exercised in e2e."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("ANTHROPIC_FAKE", "1")

from fastcad.agent.client import _reset_fake, run_turn  # noqa: E402
from fastcad.agent.tools import dispatch  # noqa: E402
from fastcad.model.kernel import volume  # noqa: E402
from fastcad.session import SessionState  # noqa: E402


def _fresh() -> SessionState:
    s = SessionState()
    _reset_fake(s)
    return s


# ---- direct tool dispatch ---------------------------------------------------


def test_dispatch_set_source_cube():
    s = _fresh()
    res = dispatch("set_source", {"text": "cube([5, 5, 5]);"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert "cube" in payload["added"]
    assert "cube" in s.cache
    assert res.changes is not None
    assert res.changes.added


def test_dispatch_set_source_parse_error():
    s = _fresh()
    res = dispatch("set_source", {"text": "this is not scad"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False
    assert "error" in payload


def test_dispatch_validate_does_not_mutate():
    s = _fresh()
    s.set_source("cube([5, 5, 5]);")
    prev = s.current_source
    res = dispatch("validate", {"text": "cube([10, 10, 10]);"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert s.current_source == prev


def test_dispatch_validate_reports_error():
    s = _fresh()
    res = dispatch("validate", {"text": "function f(x) = x+1;"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False
    assert "function" in payload["error"]


def test_dispatch_read_source():
    s = _fresh()
    s.set_source("cube([1, 1, 1]);")
    res = dispatch("read_source", {}, s)
    payload = json.loads(res.content)
    assert payload["source"] == s.current_source


def test_dispatch_select_face_unknown_node():
    s = _fresh()
    res = dispatch("select_face", {"node_id": "doesnotexist", "face_name": "+Z"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False


def test_dispatch_select_face_returns_point_normal():
    s = _fresh()
    s.set_source("cube([10, 10, 10]);")
    res = dispatch("select_face", {"node_id": "cube", "face_name": "+Z"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert payload["point"] == [5.0, 5.0, 10.0]
    assert payload["normal"] == [0.0, 0.0, 1.0]


def test_dispatch_unknown_tool():
    s = _fresh()
    res = dispatch("not_a_tool", {}, s)
    assert "error" in res.content


def test_dispatch_inspect_section_axis_aligned():
    s = _fresh()
    s.set_source("cylinder(h = 10, r = 3, $fn = 32);")
    res = dispatch("inspect_section", {"plane": "XY", "offset": 5.0}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is True
    metrics = payload["metrics"]
    assert metrics["plane_label"] == "XY@z=5"
    # XY section of a cylinder: smooth ring, no protrusions.
    assert metrics["radial"]["outer_protrusions"] == 0
    # Polygons returned by default.
    assert payload["polygons"]


def test_dispatch_inspect_section_axial_returns_axial_peaks():
    s = _fresh()
    s.set_source("cylinder(h = 10, r = 3, $fn = 32);")
    res = dispatch("inspect_section", {"plane": "XZ", "offset": 0.0}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert "axial_peaks" in payload["metrics"]
    # Smooth cylinder has no thread peaks.
    assert payload["metrics"]["axial_peaks"]["count"] == 0


def test_dispatch_inspect_section_oblique():
    s = _fresh()
    s.set_source("cube([10, 10, 10], center = true);")
    res = dispatch(
        "inspect_section",
        {"plane": "oblique", "normal": [1, 0, 1], "point": [0, 0, 0]},
        s,
    )
    payload = json.loads(res.content)
    assert payload["ok"] is True
    # Diagonal section through cube: bbox spans the diagonal.
    bb = payload["metrics"]["bbox_2d"]
    assert bb["umax"] - bb["umin"] >= 9.5
    assert bb["vmax"] - bb["vmin"] >= 13.0  # 10 * sqrt(2)


def test_dispatch_inspect_section_no_geometry():
    s = _fresh()
    res = dispatch("inspect_section", {"plane": "XY", "offset": 0.0}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False
    assert "no 3D geometry" in payload["error"]


def test_dispatch_inspect_section_bad_plane():
    s = _fresh()
    s.set_source("cube([5, 5, 5]);")
    res = dispatch("inspect_section", {"plane": "ABC"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False
    assert "ABC" in payload["error"] or "unknown plane" in payload["error"].lower()


def test_dispatch_inspect_section_oblique_requires_normal():
    s = _fresh()
    s.set_source("cube([5, 5, 5]);")
    res = dispatch("inspect_section", {"plane": "oblique"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False
    assert "normal" in payload["error"].lower()


def test_run_turn_records_elapsed_in_stats():
    """Even fake-mode runs populate `stats.elapsed_s` so the UI gets
    a footer."""
    from fastcad.agent.client import run_turn
    s = _fresh()
    turn = run_turn("Make a 20mm cube", s)
    assert turn.stats.elapsed_s >= 0
    # Fake mode doesn't touch the API → no tokens, no cost.
    assert turn.stats.input_tokens == 0
    assert turn.stats.output_tokens == 0
    assert turn.stats.cost_usd == 0.0


def test_cost_usd_known_model():
    """Pricing math: 1M input + 1M output on opus = 15 + 75 = $90."""
    from fastcad.agent.client import _cost_usd
    cost = _cost_usd("claude-opus-4-7", 1_000_000, 1_000_000, 0, 0)
    assert cost == pytest.approx(90.0, rel=1e-9)


def test_cost_usd_includes_cache():
    """Cache reads at 0.10× input rate: 1M cache_read on opus = $1.50."""
    from fastcad.agent.client import _cost_usd
    cost = _cost_usd("claude-opus-4-7", 0, 0, 1_000_000, 0)
    assert cost == pytest.approx(1.50, rel=1e-9)


def test_cost_usd_unknown_model_returns_zero():
    from fastcad.agent.client import _cost_usd
    assert _cost_usd("nonexistent-model", 1000, 2000, 0, 0) == 0.0


def test_dispatch_inspect_section_skip_polygons():
    s = _fresh()
    s.set_source("cube([5, 5, 5]);")
    res = dispatch(
        "inspect_section",
        {"plane": "XY", "offset": 0.0, "include_polygons": False},
        s,
    )
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert "polygons" not in payload


# ---- fake-mode patterns ---------------------------------------------------


def test_fake_make_cube():
    s = _fresh()
    turn = run_turn("Make a 20mm cube", s)
    assert turn.text and "cube" in turn.text.lower()
    assert "cube_1" in s.cache
    assert volume(s.cache["cube_1"].manifold) == pytest.approx(8000.0)


def test_fake_sphere_on_top_anchors_to_only_solid():
    s = _fresh()
    run_turn("Make a 20mm cube", s)
    turn = run_turn("Add a 10mm sphere on top centered", s)
    assert turn.ask_user is None
    assert "sphere_1" in s.cache
    bb = s.cache["sphere_1"].bbox
    # Sphere center should land at z=20 (top face of cube).
    assert bb.zmin == pytest.approx(15.0, abs=0.5)
    assert bb.zmax == pytest.approx(25.0, abs=0.5)


def test_fake_sphere_on_top_with_two_cubes_asks_user():
    s = _fresh()
    run_turn("Make a 20mm cube", s)
    run_turn("Make a 30mm cube", s)
    turn = run_turn("Add a sphere on top", s)
    assert turn.ask_user is not None
    assert len(turn.ask_user["options"]) >= 2
    # No new sphere yet.
    assert not any(k.startswith("sphere_") for k in s.cache)


def test_fake_resume_after_ask_user():
    s = _fresh()
    run_turn("Make a 20mm cube", s)
    run_turn("Make a 30mm cube", s)
    turn = run_turn("Add a sphere on top", s)
    assert turn.ask_user is not None
    chosen = turn.ask_user["options"][1]
    follow = run_turn(chosen, s, pending_ask=turn.ask_user)
    assert follow.text and "placed" in follow.text.lower()
    assert any(k.startswith("sphere_") for k in s.cache)


def test_fake_subtract_creates_difference():
    s = _fresh()
    run_turn("Make a 20mm cube", s)
    vol_before = volume(s.cache["cube_1"].manifold)
    turn = run_turn("Subtract a 5mm cylinder through it", s)
    assert turn.text and "subtract" in turn.text.lower()
    # cube_1 is still the only top-level visible node — its body changed.
    assert set(s.cache.keys()) == {"cube_1"}
    assert volume(s.cache["cube_1"].manifold) < vol_before


def test_fake_unknown_prompt_returns_help_text():
    s = _fresh()
    turn = run_turn("teach me python", s)
    assert turn.text is not None
    assert s.cache == {}


# ---- research tool dispatch -----------------------------------------------


def test_dispatch_list_research_empty(tmp_path, monkeypatch):
    """list_research returns an empty list when no cache entries
    exist. We point DEFAULT_CACHE_DIR at an empty tmp_path."""
    from fastcad.agent import research as _research
    monkeypatch.setattr(_research, "DEFAULT_CACHE_DIR", tmp_path)
    s = _fresh()
    res = dispatch("list_research", {}, s)
    payload = json.loads(res.content)
    assert payload == {"entries": []}


def test_dispatch_list_research_returns_entries(tmp_path, monkeypatch):
    from fastcad.agent import research as _research
    (tmp_path / "widget.md").write_text(
        "# Widget\nresearched: 2026-04-30\nbody\n", encoding="utf-8"
    )
    monkeypatch.setattr(_research, "DEFAULT_CACHE_DIR", tmp_path)
    s = _fresh()
    res = dispatch("list_research", {}, s)
    payload = json.loads(res.content)
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["slug"] == "widget"
    assert payload["entries"][0]["title"] == "Widget"


def test_dispatch_read_research_present(tmp_path, monkeypatch):
    from fastcad.agent import research as _research
    (tmp_path / "thing.md").write_text("# Thing\nbody\n", encoding="utf-8")
    monkeypatch.setattr(_research, "DEFAULT_CACHE_DIR", tmp_path)
    s = _fresh()
    res = dispatch("read_research", {"slug": "thing"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert "# Thing" in payload["content"]


def test_dispatch_read_research_missing(tmp_path, monkeypatch):
    from fastcad.agent import research as _research
    monkeypatch.setattr(_research, "DEFAULT_CACHE_DIR", tmp_path)
    s = _fresh()
    res = dispatch("read_research", {"slug": "nope"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False
    assert "nope" in payload["error"]


def test_dispatch_research_uses_run_research(tmp_path, monkeypatch):
    """`research` tool delegates to research.run_research and returns
    its result as the tool content."""
    from fastcad.agent import research as _research

    captured: dict = {}

    def fake_run_research(topic, slug=None, on_progress=None, **kwargs):
        captured["topic"] = topic
        captured["slug"] = slug
        captured["progress"] = on_progress
        return _research.ResearchResult(
            slug=slug or "auto",
            cache_path="docs/research/auto.md",
            summary="Auto",
            cached_hit=False,
        )

    monkeypatch.setattr(_research, "run_research", fake_run_research)
    s = _fresh()
    progress_events = []
    res = dispatch(
        "research",
        {"topic": "test topic", "slug": "test-slug"},
        s,
        on_progress=progress_events.append,
    )
    payload = json.loads(res.content)
    assert payload["slug"] == "test-slug"
    assert payload["cached_hit"] is False
    assert captured["topic"] == "test topic"
    assert captured["slug"] == "test-slug"
    # on_progress must be threaded through.
    assert captured["progress"] is not None


def test_dispatch_emits_progress_around_tool_call():
    """Every dispatch call emits tool_call_started + tool_call_done
    progress events when on_progress is provided."""
    s = _fresh()
    events = []
    dispatch("read_source", {}, s, on_progress=events.append)
    types = [e["type"] for e in events]
    assert types == ["tool_call_started", "tool_call_done"]
    assert events[0]["tool"] == "read_source"
    assert events[1]["tool"] == "read_source"


def test_dispatch_read_research_sets_last_research_slug(tmp_path, monkeypatch):
    """read_research records the slug on the session so set_source
    can auto-validate against it later."""
    from fastcad.agent import research as _research
    (tmp_path / "thing.md").write_text("# Thing\nbody\n", encoding="utf-8")
    monkeypatch.setattr(_research, "DEFAULT_CACHE_DIR", tmp_path)
    s = _fresh()
    assert s.last_research_slug is None
    dispatch("read_research", {"slug": "thing"}, s)
    assert s.last_research_slug == "thing"


CACHE_WITH_ACCEPTANCE = """\
# Test cube

slug: test-cube

## Acceptance

```json
{
  "bbox_z_extent": [9, 11],
  "bbox_xy_max": [9, 11],
  "volume_range": [900, 1100],
  "connected_components": 1,
  "expected_modules": ["box"],
  "horizontal_slices_at_z": [
    {"z": 5, "outer_protrusions": 0}
  ]
}
```
"""


def test_dispatch_validate_design_passes(tmp_path, monkeypatch):
    """A geometry that matches the cache schema produces ok=true,
    empty defects."""
    from fastcad.agent import research as _research
    (tmp_path / "test-cube.md").write_text(CACHE_WITH_ACCEPTANCE, encoding="utf-8")
    monkeypatch.setattr(_research, "DEFAULT_CACHE_DIR", tmp_path)

    s = _fresh()
    s.set_source("module box() { cube([10, 10, 10]); } box();")
    dispatch("read_research", {"slug": "test-cube"}, s)

    res = dispatch("validate_design", {}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is True
    assert payload["defects"] == []
    assert payload["validated_against"] == "test-cube"


def test_dispatch_validate_design_reports_defects(tmp_path, monkeypatch):
    from fastcad.agent import research as _research
    (tmp_path / "test-cube.md").write_text(CACHE_WITH_ACCEPTANCE, encoding="utf-8")
    monkeypatch.setattr(_research, "DEFAULT_CACHE_DIR", tmp_path)

    s = _fresh()
    # Wrong volume — 5×5×5=125, spec wants [900, 1100].
    s.set_source("module box() { cube([5, 5, 5]); } box();")
    dispatch("read_research", {"slug": "test-cube"}, s)

    res = dispatch("validate_design", {}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False
    assert len(payload["defects"]) > 0
    assert any(d["where"] == "volume_range" for d in payload["defects"])


def test_dispatch_validate_design_no_slug_errors():
    s = _fresh()
    res = dispatch("validate_design", {}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is False
    assert "slug" in payload["error"]


def test_dispatch_set_source_auto_validates(tmp_path, monkeypatch):
    """set_source auto-runs the validator when last_research_slug is
    set and FASTCAD_AUTO_VALIDATE is on (default)."""
    from fastcad.agent import research as _research
    (tmp_path / "test-cube.md").write_text(CACHE_WITH_ACCEPTANCE, encoding="utf-8")
    monkeypatch.setattr(_research, "DEFAULT_CACHE_DIR", tmp_path)
    monkeypatch.setenv("FASTCAD_AUTO_VALIDATE", "structural")

    s = _fresh()
    dispatch("read_research", {"slug": "test-cube"}, s)
    res = dispatch("set_source", {"text": "module box() { cube([5,5,5]); } box();"}, s)
    payload = json.loads(res.content)
    assert payload["ok"] is True   # spec was committed
    assert payload["validated_against"] == "test-cube"
    assert len(payload["defects"]) > 0


def test_dispatch_set_source_skips_validation_when_off(tmp_path, monkeypatch):
    from fastcad.agent import research as _research
    (tmp_path / "test-cube.md").write_text(CACHE_WITH_ACCEPTANCE, encoding="utf-8")
    monkeypatch.setattr(_research, "DEFAULT_CACHE_DIR", tmp_path)
    monkeypatch.setenv("FASTCAD_AUTO_VALIDATE", "off")

    s = _fresh()
    dispatch("read_research", {"slug": "test-cube"}, s)
    res = dispatch("set_source", {"text": "module box() { cube([5,5,5]); } box();"}, s)
    payload = json.loads(res.content)
    assert "defects" not in payload   # not validated


def test_validate_design_emits_progress_events(tmp_path, monkeypatch):
    from fastcad.agent import research as _research
    (tmp_path / "test-cube.md").write_text(CACHE_WITH_ACCEPTANCE, encoding="utf-8")
    monkeypatch.setattr(_research, "DEFAULT_CACHE_DIR", tmp_path)

    s = _fresh()
    s.set_source("module box() { cube([5, 5, 5]); } box();")
    dispatch("read_research", {"slug": "test-cube"}, s)
    events: list[dict] = []
    dispatch("validate_design", {}, s, on_progress=events.append)
    types = [e["type"] for e in events]
    assert "validation_defect" in types
    # tool_call_started + tool_call_done bracket the validation_defect
    # events, so they're somewhere in the middle.
    assert types[0] == "tool_call_started"
    assert types[-1] == "tool_call_done"


def test_dispatch_set_source_progress_args_truncated():
    """Long set_source text is summarized in progress events so the
    WS doesn't ship megabytes per call."""
    s = _fresh()
    events = []
    big_text = "// " + ("x" * 5000) + "\ncube([1,1,1]);"
    dispatch("set_source", {"text": big_text}, s, on_progress=events.append)
    started = events[0]
    assert started["type"] == "tool_call_started"
    assert "text" in started["args"]
    assert started["args"]["text"].startswith("<")
    assert "chars>" in started["args"]["text"]
