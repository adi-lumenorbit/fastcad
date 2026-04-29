#!/usr/bin/env bash
# Run Playwright e2e tests. Headless Chromium runs entirely inside WSL.
exec .venv/bin/pytest tests/e2e -q "$@"
