"""Deep-research subagent driver.

Spawns the Claude Code CLI (`claude --print --output-format stream-json`)
as a subprocess to research a standardized part. The subagent runs in
the repo root with full access to its built-in WebSearch / WebFetch /
Read / Write tools, and is told to write a markdown summary to
`docs/research/<slug>.md` per the format in `docs/research/README.md`.

Stream-json events from the subprocess flow through an `on_progress`
callback so the WS layer can surface them in real time. Cache hits
(file already exists for a given slug) skip the subprocess entirely.

Notes on isolation: the subprocess is launched with explicit `cwd` and
inherits the current process's environment (so `ANTHROPIC_API_KEY`
propagates). `--permission-mode bypassPermissions` lets the subagent
write files without each-action prompts; the trade-off is intentional
since the only writes we expect are inside `docs/research/`.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator


# --- Paths -----------------------------------------------------------------

# repo root is three levels up: .../src/fastcad/agent/research.py
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_DIR = _REPO_ROOT / "docs" / "research"
DEFAULT_REPO_ROOT = _REPO_ROOT


# --- Subagent system-prompt directive --------------------------------------

RESEARCH_DIRECTIVE = """\
You are a research subagent inside the `fastcad` repository. Your sole
deliverable is a single markdown file at `docs/research/<slug>.md`
containing published spec data for the part the user names.

Read `docs/research/README.md` first — it defines the file format and
slug rules. Follow that format exactly. Constraints:

- All dimensions in millimetres unless the part is intrinsically
  imperial (e.g. UNF threads).
- Cite sources as URLs (standards PDFs, manufacturer datasheets,
  Wikipedia for triangulation only).
- Pick a kebab-case slug that another engineer could guess from the
  filename. Use it as the file's basename.
- Output the markdown file. Do not produce code, do not produce
  other artifacts, do not modify any other file in the repo.

When the file is written and well-formed, you are done. Print one
line confirming the slug and stop.
"""


# --- Data types ------------------------------------------------------------


@dataclass
class ResearchResult:
    slug: str
    cache_path: str | None    # repo-relative path, or None on error
    summary: str              # title pulled from H1 (or empty)
    cached_hit: bool = False
    error: str | None = None
    transcript_path: str | None = None  # tmp file with full stream-json log

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "cache_path": self.cache_path,
            "summary": self.summary,
            "cached_hit": self.cached_hit,
            "error": self.error,
        }


@dataclass
class CacheEntry:
    slug: str
    title: str
    researched: str
    path: str  # repo-relative


# --- Per-slug locking ------------------------------------------------------

_locks: dict[str, threading.Lock] = {}
_locks_meta_lock = threading.Lock()


def _slug_lock(slug: str) -> threading.Lock:
    with _locks_meta_lock:
        if slug not in _locks:
            _locks[slug] = threading.Lock()
        return _locks[slug]


# --- Cache I/O -------------------------------------------------------------


def list_research(cache_dir: Path | None = None) -> list[CacheEntry]:
    """Enumerate cached research entries. Cheap; the agent calls this
    every turn it's modeling a standardized part."""
    cd = cache_dir or DEFAULT_CACHE_DIR
    if not cd.exists():
        return []
    out: list[CacheEntry] = []
    for path in sorted(cd.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        title, researched = _parse_header(path)
        try:
            rel = path.relative_to(DEFAULT_REPO_ROOT)
        except ValueError:
            rel = path
        out.append(
            CacheEntry(
                slug=path.stem,
                title=title,
                researched=researched,
                path=str(rel),
            )
        )
    return out


def read_research(slug: str, cache_dir: Path | None = None) -> str | None:
    """Return the full markdown content for a slug, or None if missing."""
    cd = cache_dir or DEFAULT_CACHE_DIR
    p = cd / f"{slug}.md"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def _parse_header(path: Path) -> tuple[str, str]:
    """Pull the H1 title and `researched:` date from the first ~15 lines.
    Both fall back to empty string if absent."""
    title = ""
    researched = ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[:20]:
            stripped = line.strip()
            if stripped.startswith("# ") and not title:
                title = stripped[2:].strip()
            elif stripped.lower().startswith("researched:"):
                researched = stripped.split(":", 1)[1].strip()
            if title and researched:
                break
    except OSError:
        pass
    return title, researched


# --- Subprocess driver -----------------------------------------------------

ProgressCallback = Callable[[dict], None]


def run_research(
    topic: str,
    slug: str | None = None,
    on_progress: ProgressCallback | None = None,
    *,
    cache_dir: Path | None = None,
    repo_root: Path | None = None,
    max_turns: int = 20,
    timeout_s: float = 600.0,
    model: str = "claude-opus-4-7",
    claude_bin: str = "claude",
    extra_env: dict[str, str] | None = None,
    spawn: Callable | None = None,
) -> ResearchResult:
    """Run the deep-research subagent.

    On cache hit (slug provided and `<cache_dir>/<slug>.md` exists),
    returns immediately with `cached_hit=True`.

    `spawn` is a hook for tests to inject a fake `subprocess.Popen`.
    """
    cd = cache_dir or DEFAULT_CACHE_DIR
    rr = repo_root or DEFAULT_REPO_ROOT

    # Cache hit — short-circuit.
    if slug:
        cache_path = cd / f"{slug}.md"
        if cache_path.exists():
            title, _ = _parse_header(cache_path)
            try:
                rel = cache_path.relative_to(DEFAULT_REPO_ROOT)
            except ValueError:
                rel = cache_path
            return ResearchResult(
                slug=slug,
                cache_path=str(rel),
                summary=title,
                cached_hit=True,
            )

    cd.mkdir(parents=True, exist_ok=True)

    lock_key = slug or topic
    lock = _slug_lock(lock_key)
    with lock:
        # Re-check after acquiring the lock — another caller may have
        # populated the cache while we were waiting.
        if slug:
            cache_path = cd / f"{slug}.md"
            if cache_path.exists():
                title, _ = _parse_header(cache_path)
                try:
                    rel = cache_path.relative_to(DEFAULT_REPO_ROOT)
                except ValueError:
                    rel = cache_path
                return ResearchResult(
                    slug=slug,
                    cache_path=str(rel),
                    summary=title,
                    cached_hit=True,
                )

        return _run_subprocess(
            topic=topic,
            slug=slug,
            on_progress=on_progress,
            cache_dir=cd,
            repo_root=rr,
            max_turns=max_turns,
            timeout_s=timeout_s,
            model=model,
            claude_bin=claude_bin,
            extra_env=extra_env,
            spawn=spawn,
        )


def _build_user_prompt(topic: str, slug: str | None) -> str:
    if slug:
        return (
            f"Research \"{topic}\" for use in a CAD model. "
            f"Save your findings to docs/research/{slug}.md, following "
            f"the format in docs/research/README.md. Use the slug "
            f"\"{slug}\" verbatim."
        )
    return (
        f"Research \"{topic}\" for use in a CAD model. "
        f"Pick a kebab-case slug for the part and save your findings "
        f"to docs/research/<slug>.md, following the format in "
        f"docs/research/README.md."
    )


def _run_subprocess(
    *,
    topic: str,
    slug: str | None,
    on_progress: ProgressCallback | None,
    cache_dir: Path,
    repo_root: Path,
    max_turns: int,
    timeout_s: float,
    model: str,
    claude_bin: str,
    extra_env: dict[str, str] | None,
    spawn: Callable | None,
) -> ResearchResult:
    cmd = [
        claude_bin,
        "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(max_turns),
        "--model", model,
        "--append-system-prompt", RESEARCH_DIRECTIVE,
        "--permission-mode", "bypassPermissions",
        _build_user_prompt(topic, slug),
    ]

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    spawn_fn = spawn or subprocess.Popen
    pre_files = _snapshot_cache(cache_dir)

    if on_progress:
        on_progress({"type": "research_started", "topic": topic, "slug": slug})

    transcript: list[dict] = []
    error: str | None = None
    try:
        proc = spawn_fn(
            cmd,
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
    except FileNotFoundError:
        error = (
            f"`{claude_bin}` not found on PATH. Install Claude Code "
            f"(npm install -g @anthropic-ai/claude-code) or pass "
            f"`claude_bin=` with the right path."
        )
        if on_progress:
            on_progress({"type": "research_error", "error": error})
        return ResearchResult(slug=slug or "", cache_path=None, summary="", error=error)

    deadline = time.time() + timeout_s
    try:
        for line in proc.stdout or []:
            if time.time() > deadline:
                proc.kill()
                error = f"research timed out after {timeout_s:.0f}s"
                break
            line = line.strip()
            if not line:
                continue
            event = _parse_event(line)
            transcript.append(event)
            if on_progress:
                on_progress(event)
        rc = proc.wait(timeout=10.0)
        if rc != 0 and error is None:
            stderr = (proc.stderr.read() if proc.stderr else "") or ""
            error = f"subagent exited {rc}: {stderr.strip()[:500]}"
    except Exception as exc:  # noqa: BLE001
        error = f"subprocess driver error: {exc}"
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass

    transcript_path = _persist_transcript(transcript, repo_root)

    # Resolve the slug + cache file.
    new_files = _new_md_files(cache_dir, pre_files)
    resolved_slug = slug
    cache_path: Path | None = None

    if resolved_slug:
        candidate = cache_dir / f"{resolved_slug}.md"
        if candidate.exists():
            cache_path = candidate

    if cache_path is None and new_files:
        # Take the newest .md file in cache_dir that wasn't there before.
        cache_path = max(new_files, key=lambda p: p.stat().st_mtime)
        resolved_slug = cache_path.stem

    if cache_path is None:
        if error is None:
            error = "subagent finished without producing a cache file"
        if on_progress:
            on_progress({"type": "research_error", "error": error})
        return ResearchResult(
            slug=resolved_slug or "",
            cache_path=None,
            summary="",
            error=error,
            transcript_path=transcript_path,
        )

    title, _ = _parse_header(cache_path)
    try:
        rel = cache_path.relative_to(DEFAULT_REPO_ROOT)
    except ValueError:
        rel = cache_path

    if on_progress:
        on_progress(
            {
                "type": "research_done",
                "slug": resolved_slug,
                "cache_path": str(rel),
                "title": title,
            }
        )

    return ResearchResult(
        slug=resolved_slug or "",
        cache_path=str(rel),
        summary=title,
        cached_hit=False,
        error=error,
        transcript_path=transcript_path,
    )


# --- Helpers ---------------------------------------------------------------


def _parse_event(line: str) -> dict:
    """Parse one stream-json line. On JSON failure, wrap as raw event."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"type": "raw", "text": line}


def _snapshot_cache(cache_dir: Path) -> set[Path]:
    if not cache_dir.exists():
        return set()
    return {p.resolve() for p in cache_dir.glob("*.md") if p.name.lower() != "readme.md"}


def _new_md_files(cache_dir: Path, pre: set[Path]) -> list[Path]:
    if not cache_dir.exists():
        return []
    after = {
        p.resolve()
        for p in cache_dir.glob("*.md")
        if p.name.lower() != "readme.md"
    }
    return sorted(after - pre)


def _persist_transcript(transcript: list[dict], repo_root: Path) -> str | None:
    """Write the full stream-json transcript to tmp/research/ for debug."""
    if not transcript:
        return None
    out_dir = repo_root / "tmp" / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"transcript-{ts}.jsonl"
    try:
        with path.open("w", encoding="utf-8") as fh:
            for ev in transcript:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        return None
    try:
        return str(path.relative_to(DEFAULT_REPO_ROOT))
    except ValueError:
        return str(path)


__all__ = [
    "RESEARCH_DIRECTIVE",
    "ResearchResult",
    "CacheEntry",
    "DEFAULT_CACHE_DIR",
    "list_research",
    "read_research",
    "run_research",
]
