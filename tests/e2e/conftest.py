"""Playwright e2e fixtures.

Spawns a uvicorn subprocess against a random free port for each test session
and yields the base URL. ANTHROPIC_FAKE=1 forces the agent into deterministic
scripted mode — no network calls, repeatable outcomes.

Skips the whole module if Chromium isn't installed (so unit tests on a fresh
checkout don't blow up; user runs `.venv/bin/playwright install chromium`
before e2e).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _have_chromium() -> bool:
    # pytest-playwright fails at collection if browsers missing; check up front.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(headless=True)
                b.close()
                return True
            except Exception:
                return False
    except Exception:
        return False


_HAS_CHROMIUM = _have_chromium()
_SKIP_REASON = "Chromium not installed; run `.venv/bin/playwright install chromium`"


def pytest_collection_modifyitems(config, items):
    """Skip every test in the e2e directory when Chromium is missing."""
    if _HAS_CHROMIUM:
        return
    skip_marker = pytest.mark.skip(reason=_SKIP_REASON)
    for item in items:
        if "tests/e2e" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def feedback_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("feedback")


@pytest.fixture(scope="session")
def live_server(feedback_dir: Path) -> str:
    """Boots fastcad on a random port. Yields http://127.0.0.1:<port>."""
    port = _free_port()
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(
        {
            "ANTHROPIC_FAKE": "1",
            "FASTCAD_FEEDBACK_DIR": str(feedback_dir),
            "PYTHONUNBUFFERED": "1",
        }
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "fastcad",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    last_err: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode() if proc.stdout else ""
            raise RuntimeError(f"server died during boot:\n{out}")
        try:
            urllib.request.urlopen(base + "/healthz", timeout=0.5).read()
            break
        except Exception as exc:
            last_err = exc
            time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError(f"server did not become ready: {last_err}")

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
