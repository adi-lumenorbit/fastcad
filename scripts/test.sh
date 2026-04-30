#!/usr/bin/env bash
# Read-only pytest runner. `bash scripts/test.sh [pytest-args]`.
# Defaults to the unit suite when no args given.
ARGS=("$@")
if [ ${#ARGS[@]} -eq 0 ]; then
  ARGS=("tests/unit" "-q")
fi
exec /home/adi/src/fastcad/.venv/bin/pytest "${ARGS[@]}"
