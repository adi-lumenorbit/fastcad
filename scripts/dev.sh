#!/usr/bin/env bash
# Run the fastcad dev server. WSL-aware: binds 0.0.0.0 so the Windows host can
# reach it via http://localhost:8765/.
HOST="${FASTCAD_HOST:-0.0.0.0}"
PORT="${FASTCAD_PORT:-8765}"

if [ ! -x .venv/bin/python ]; then
  echo "no .venv found - create one: python3 -m venv .venv && .venv/bin/pip install -e .[dev]"
  exit 1
fi

echo "fastcad dev server starting on http://localhost:${PORT}/"
echo "(if Windows can't reach it, allow inbound TCP ${PORT} in Windows Defender Firewall)"
exec .venv/bin/python -m fastcad --host "${HOST}" --port "${PORT}" --reload
