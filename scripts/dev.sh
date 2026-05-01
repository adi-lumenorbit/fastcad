#!/usr/bin/env bash
# Run the fastcad dev server. WSL-aware: binds 0.0.0.0 so the Windows host can
# reach it via http://localhost:8765/.

# Source .env (if present) so ANTHROPIC_API_KEY etc. propagate. Existing
# shell-exported vars take precedence — set -a triggers auto-export of
# anything assigned while it's on, but only for vars not already set.
if [ -f .env ]; then
  set -o allexport
  # shellcheck disable=SC1091
  . ./.env
  set +o allexport
fi

HOST="${FASTCAD_HOST:-0.0.0.0}"
PORT="${FASTCAD_PORT:-8765}"

# Reload mode: pass --no-reload to disable. Default is to auto-reload
# on source change (useful for interactive development). Disable when
# running empirical tests so source edits don't reload the server
# mid-test and conflate behaviour. Also set via FASTCAD_RELOAD=0.
RELOAD=1
for arg in "$@"; do
  case "$arg" in
    --no-reload) RELOAD=0 ;;
  esac
done
if [ "${FASTCAD_RELOAD:-}" = "0" ]; then
  RELOAD=0
fi

if [ ! -x .venv/bin/python ]; then
  echo "no .venv found - create one: python3 -m venv .venv && .venv/bin/pip install -e .[dev]"
  exit 1
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ "${ANTHROPIC_FAKE:-}" != "1" ]; then
  echo "warning: ANTHROPIC_API_KEY is unset and ANTHROPIC_FAKE is not 1."
  echo "  fill in .env (copy .env.template) or export the key, or set ANTHROPIC_FAKE=1."
fi

echo "fastcad dev server starting on http://localhost:${PORT}/"
if [ "$RELOAD" = "1" ]; then
  echo "  (reload mode: source edits auto-reload — use --no-reload for stable test mode)"
  echo "(if Windows can't reach it, allow inbound TCP ${PORT} in Windows Defender Firewall)"
  exec .venv/bin/python -m fastcad --host "${HOST}" --port "${PORT}" --reload
else
  echo "  (no-reload mode: source edits NOT loaded; restart to pick up changes)"
  echo "(if Windows can't reach it, allow inbound TCP ${PORT} in Windows Defender Firewall)"
  exec .venv/bin/python -m fastcad --host "${HOST}" --port "${PORT}"
fi
