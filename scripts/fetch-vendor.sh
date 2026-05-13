#!/usr/bin/env bash
# Fetch pinned third-party browser libs into web/vendor/. Idempotent.
set -u

VENDOR=web/vendor
mkdir -p "$VENDOR"

# three.js (ES module build, bundled core)
THREE_VER=0.162.0
if [ ! -f "$VENDOR/three.module.js" ]; then
  echo "fetching three.js ${THREE_VER}"
  curl -fsSL -o "$VENDOR/three.module.js" \
    "https://unpkg.com/three@${THREE_VER}/build/three.module.js"
fi
if [ ! -f "$VENDOR/OrbitControls.js" ]; then
  curl -fsSL -o "$VENDOR/OrbitControls.js" \
    "https://unpkg.com/three@${THREE_VER}/examples/jsm/controls/OrbitControls.js"
fi
if [ ! -f "$VENDOR/TransformControls.js" ]; then
  curl -fsSL -o "$VENDOR/TransformControls.js" \
    "https://unpkg.com/three@${THREE_VER}/examples/jsm/controls/TransformControls.js"
fi

# rrweb (recorder only, no replay UI bundled here)
RRWEB_VER=2.0.0-alpha.13
if [ ! -f "$VENDOR/rrweb.min.js" ]; then
  echo "fetching rrweb ${RRWEB_VER}"
  curl -fsSL -o "$VENDOR/rrweb.min.js" \
    "https://unpkg.com/rrweb@${RRWEB_VER}/dist/rrweb.min.js"
fi

# html2canvas
H2C_VER=1.4.1
if [ ! -f "$VENDOR/html2canvas.min.js" ]; then
  echo "fetching html2canvas ${H2C_VER}"
  curl -fsSL -o "$VENDOR/html2canvas.min.js" \
    "https://unpkg.com/html2canvas@${H2C_VER}/dist/html2canvas.min.js"
fi

echo "vendor ready: $(ls $VENDOR)"
