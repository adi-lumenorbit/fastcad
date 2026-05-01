"""Tests for agent/research.py.

The Claude Code CLI is never actually invoked: each test injects a
fake `spawn` callable that mimics `subprocess.Popen` — emits canned
stream-json on stdout and writes a fixture cache file as a side
effect to simulate the subagent doing its job.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Iterable

import pytest

from fastcad.agent import research


# ---------------------------------------------------------------------------
# Fake subprocess
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, stdout_lines: Iterable[str], rc: int = 0, stderr_text: str = ""):
        joined = "\n".join(stdout_lines) + ("\n" if stdout_lines else "")
        self.stdout = io.StringIO(joined)
        self.stderr = io.StringIO(stderr_text)
        self._rc = rc

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return self._rc

    def kill(self) -> None:
        pass


def _make_spawn(
    cache_dir: Path,
    *,
    slug: str,
    body: str,
    stdout_lines: list[str] | None = None,
    rc: int = 0,
    fail_with: type[Exception] | None = None,
):
    """Returns a callable that, when invoked, writes the cache file
    and returns a _FakeProc emitting the canned stream."""

    def spawn(cmd, **kwargs):  # noqa: ARG001
        if fail_with is not None:
            raise fail_with("nope")
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{slug}.md").write_text(body, encoding="utf-8")
        lines = stdout_lines or [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": f"researching {slug}"}]}}),
            json.dumps({"type": "result", "subtype": "success", "result": f"saved docs/research/{slug}.md"}),
        ]
        return _FakeProc(lines, rc=rc)

    return spawn


# ---------------------------------------------------------------------------
# list_research / read_research
# ---------------------------------------------------------------------------


def test_list_empty(tmp_path):
    assert research.list_research(cache_dir=tmp_path) == []


def test_list_skips_readme(tmp_path):
    (tmp_path / "README.md").write_text("# README\n")
    (tmp_path / "thing.md").write_text("# Thing\nresearched: 2026-04-30\n")
    out = research.list_research(cache_dir=tmp_path)
    assert [e.slug for e in out] == ["thing"]
    assert out[0].title == "Thing"
    assert out[0].researched == "2026-04-30"


def test_read_present_and_missing(tmp_path):
    p = tmp_path / "abc.md"
    p.write_text("# Abc\nbody\n", encoding="utf-8")
    assert "# Abc" in (research.read_research("abc", cache_dir=tmp_path) or "")
    assert research.read_research("doesnotexist", cache_dir=tmp_path) is None


# ---------------------------------------------------------------------------
# run_research — cache hits
# ---------------------------------------------------------------------------


def test_cache_hit_short_circuits(tmp_path):
    (tmp_path / "widget.md").write_text(
        "# Widget\nresearched: 2026-04-30\nbody\n", encoding="utf-8"
    )

    def spawn_should_not_be_called(*a, **kw):  # noqa: ARG001
        raise AssertionError("subprocess must not be spawned on cache hit")

    result = research.run_research(
        "widget topic",
        slug="widget",
        cache_dir=tmp_path,
        repo_root=tmp_path.parent,
        spawn=spawn_should_not_be_called,
    )
    assert result.cached_hit is True
    assert result.slug == "widget"
    assert result.cache_path and result.cache_path.endswith("widget.md")


# ---------------------------------------------------------------------------
# run_research — fresh subagent run
# ---------------------------------------------------------------------------


CACHE_BODY = """\
# Test widget

researched: 2026-04-30
researcher: claude-opus-4-7 via Claude Code (subagent)
slug: test-widget

## Canonical name
A widget for testing.

## Key dimensions
- length: 10 mm

## Variants
- standard

## Sources
- https://example.com/widget
"""


def test_fresh_run_writes_cache(tmp_path):
    cache = tmp_path / "docs" / "research"
    spawn = _make_spawn(cache, slug="test-widget", body=CACHE_BODY)

    events: list[dict] = []
    result = research.run_research(
        "test widget",
        slug="test-widget",
        on_progress=events.append,
        cache_dir=cache,
        repo_root=tmp_path,
        spawn=spawn,
    )
    assert result.error is None
    assert result.cached_hit is False
    assert result.slug == "test-widget"
    assert result.cache_path and result.cache_path.endswith("test-widget.md")
    assert result.summary == "Test widget"

    # Progress events should bracket with started + done.
    kinds = [e.get("type") for e in events]
    assert "research_started" in kinds
    assert "research_done" in kinds


def test_fresh_run_resolves_slug_from_new_file(tmp_path):
    """When called without a slug, the driver picks up whichever .md
    file the subagent dropped into the cache dir."""
    cache = tmp_path / "docs" / "research"
    spawn = _make_spawn(cache, slug="auto-slug", body=CACHE_BODY)

    result = research.run_research(
        "an auto-named part",
        slug=None,
        cache_dir=cache,
        repo_root=tmp_path,
        spawn=spawn,
    )
    assert result.slug == "auto-slug"
    assert result.cache_path and result.cache_path.endswith("auto-slug.md")


def test_subagent_failure_bubbles_up(tmp_path):
    cache = tmp_path / "docs" / "research"
    cache.mkdir(parents=True)

    def spawn(cmd, **kwargs):  # noqa: ARG001
        # Simulate non-zero exit, no file written.
        return _FakeProc(
            [json.dumps({"type": "result", "subtype": "error", "error": "boom"})],
            rc=2,
            stderr_text="something exploded",
        )

    result = research.run_research(
        "fails",
        slug="fails",
        cache_dir=cache,
        repo_root=tmp_path,
        spawn=spawn,
    )
    assert result.error is not None
    assert result.cache_path is None


def test_claude_binary_missing(tmp_path):
    cache = tmp_path / "docs" / "research"
    spawn = _make_spawn(cache, slug="x", body=CACHE_BODY, fail_with=FileNotFoundError)
    result = research.run_research(
        "x", slug="x", cache_dir=cache, repo_root=tmp_path, spawn=spawn
    )
    assert result.error is not None
    assert "claude" in result.error.lower() or "not found" in result.error.lower()


def test_progress_callback_receives_events(tmp_path):
    cache = tmp_path / "docs" / "research"
    spawn = _make_spawn(
        cache,
        slug="streamy",
        body=CACHE_BODY,
        stdout_lines=[
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch"}]}}),
            json.dumps({"type": "user", "message": {"content": [{"type": "tool_result"}]}}),
            json.dumps({"type": "result", "subtype": "success", "result": "done"}),
        ],
    )
    events: list[dict] = []
    research.run_research(
        "streamy", slug="streamy", on_progress=events.append,
        cache_dir=cache, repo_root=tmp_path, spawn=spawn,
    )
    # research_started + 4 stream events + research_done = 6
    assert len(events) >= 5
    types = [e.get("type") for e in events]
    assert types[0] == "research_started"
    assert types[-1] == "research_done"


def test_concurrent_calls_serialise_per_slug(tmp_path, monkeypatch):
    """Two threads calling research for the same slug must not both
    spawn — second waits, sees cache, returns cached_hit."""
    import threading

    cache = tmp_path / "docs" / "research"
    cache.mkdir(parents=True)

    spawn_count = {"n": 0}

    def spawn(cmd, **kwargs):  # noqa: ARG001
        spawn_count["n"] += 1
        # Pretend the subagent writes the file.
        (cache / "shared.md").write_text(CACHE_BODY, encoding="utf-8")
        return _FakeProc([json.dumps({"type": "result", "subtype": "success"})])

    results: list[research.ResearchResult] = []

    def worker():
        results.append(
            research.run_research(
                "shared topic",
                slug="shared",
                cache_dir=cache,
                repo_root=tmp_path,
                spawn=spawn,
            )
        )

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert spawn_count["n"] == 1
    # One was a fresh run; the other a cache hit (any order).
    cached = [r for r in results if r.cached_hit]
    fresh = [r for r in results if not r.cached_hit]
    assert len(cached) == 1
    assert len(fresh) == 1


def test_repeat_after_first_run_is_cache_hit(tmp_path):
    cache = tmp_path / "docs" / "research"
    spawn = _make_spawn(cache, slug="reuse", body=CACHE_BODY)

    research.run_research(
        "reuse topic", slug="reuse",
        cache_dir=cache, repo_root=tmp_path, spawn=spawn,
    )

    def spawn_should_not_be_called(*a, **kw):  # noqa: ARG001
        raise AssertionError("second call must not spawn")

    second = research.run_research(
        "reuse topic", slug="reuse",
        cache_dir=cache, repo_root=tmp_path, spawn=spawn_should_not_be_called,
    )
    assert second.cached_hit is True
