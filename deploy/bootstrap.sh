#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 24.04 VM into a hardened fastcad host.
# Run as root after first SSH:   sudo bash deploy/bootstrap.sh
#
# Idempotent — re-running fixes drift without breaking state.

set -o errexit
set -o nounset
set -o pipefail

REPO_URL="${REPO_URL:-https://github.com/adi-lumenorbit/fastcad.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/fastcad}"
ETC_DIR="/etc/fastcad"
SERVICE_USER="fastcad"
DEPLOY_DIR="${INSTALL_DIR}/deploy"

# These three are interactive prompts the first time; subsequent runs
# read the saved values from /etc/fastcad/deploy.env.
DEPLOY_ENV_FILE="${ETC_DIR}/deploy.env"

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Step 0 — load or prompt for deploy parameters
# ---------------------------------------------------------------------------

mkdir -p "${ETC_DIR}"
chmod 700 "${ETC_DIR}"

if [ -f "${DEPLOY_ENV_FILE}" ]; then
  # shellcheck disable=SC1090
  . "${DEPLOY_ENV_FILE}"
fi

prompt_if_unset() {
  local var="$1" message="$2"
  if [ -z "${!var:-}" ]; then
    read -r -p "${message}: " value
    printf -v "${var}" '%s' "${value}"
  fi
}

prompt_if_unset HOST          "fully-qualified host name (e.g. fastcad.example.com OR 34-72-1-2.nip.io)"
prompt_if_unset ACME_EMAIL    "email for Let's Encrypt notifications"
prompt_if_unset BASIC_USER    "basic-auth username"
prompt_if_unset ANTHROPIC_API "ANTHROPIC_API_KEY (will be stored in ${ETC_DIR}/fastcad.env, mode 0600)"

# Generate a strong password if not already saved. Keeps the plaintext
# in deploy.env (root-only readable) so `caddy hash-password` can be
# re-run on upgrades without prompting again.
if [ -z "${BASIC_PASS:-}" ]; then
  BASIC_PASS="$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)"
fi

cat > "${DEPLOY_ENV_FILE}" <<EOF
HOST=${HOST}
ACME_EMAIL=${ACME_EMAIL}
BASIC_USER=${BASIC_USER}
BASIC_PASS=${BASIC_PASS}
ANTHROPIC_API=${ANTHROPIC_API}
EOF
chmod 600 "${DEPLOY_ENV_FILE}"

echo
echo "=========================================================="
echo "  Deploy parameters loaded:"
echo "    host       : ${HOST}"
echo "    acme email : ${ACME_EMAIL}"
echo "    basic user : ${BASIC_USER}"
echo "    basic pass : ${BASIC_PASS}   <-- save this somewhere safe"
echo "=========================================================="
echo

# ---------------------------------------------------------------------------
# Step 1 — system packages
# ---------------------------------------------------------------------------

DEBIAN_FRONTEND=noninteractive apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  ca-certificates curl wget gnupg git \
  python3.12 python3.12-venv python3-pip \
  openscad-nox \
  nodejs npm \
  ufw fail2ban \
  unattended-upgrades

# Caddy from the official repo (its build of `mholt/caddy-ratelimit`
# is in the standard package; if not, replace this block with an
# xcaddy build that pulls the module).
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/setup.deb.sh' | bash
  DEBIAN_FRONTEND=noninteractive apt-get install -y caddy
fi

# Claude Code CLI for the research subagent.
if ! command -v claude >/dev/null 2>&1; then
  npm install -g @anthropic-ai/claude-code
fi

# ---------------------------------------------------------------------------
# Step 2 — service user + repo checkout
# ---------------------------------------------------------------------------

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/var/lib/${SERVICE_USER}" \
          --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

if [ ! -d "${INSTALL_DIR}/.git" ]; then
  git clone "${REPO_URL}" "${INSTALL_DIR}"
else
  git -C "${INSTALL_DIR}" pull --ff-only
fi

# Vendor browser libs and create the venv (idempotent).
if [ ! -x "${INSTALL_DIR}/.venv/bin/python" ]; then
  python3.12 -m venv "${INSTALL_DIR}/.venv"
fi
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip wheel >/dev/null
"${INSTALL_DIR}/.venv/bin/pip" install -e "${INSTALL_DIR}" >/dev/null

# Browser libs (three.js / rrweb / html2canvas) into web/vendor/.
sudo -u "${SERVICE_USER}" -H bash "${INSTALL_DIR}/scripts/fetch-vendor.sh"

# Tighten ownership so the service user can read the tree but only
# write within its sanctioned subdirs (matches systemd ReadWritePaths).
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
chmod 750 "${INSTALL_DIR}"

# ---------------------------------------------------------------------------
# Step 3 — write the API-key environment file
# ---------------------------------------------------------------------------

# This is the .env the systemd unit loads. Mode 0600, root-owned, so
# only systemd's loader (running as root) can read it before it
# drops privileges to fastcad. The fastcad UID never sees the
# plaintext file — it only inherits the env vars.
cat > "${ETC_DIR}/fastcad.env" <<EOF
ANTHROPIC_API_KEY=${ANTHROPIC_API}
FASTCAD_ALLOWED_ORIGINS=https://${HOST}
EOF
chmod 600 "${ETC_DIR}/fastcad.env"
chown root:root "${ETC_DIR}/fastcad.env"

# ---------------------------------------------------------------------------
# Step 4 — render Caddyfile + install systemd unit + fail2ban
# ---------------------------------------------------------------------------

BCRYPT_HASH="$(caddy hash-password --plaintext "${BASIC_PASS}")"

# Substitute placeholders into Caddyfile and install.
sed \
  -e "s|__HOST__|${HOST}|g" \
  -e "s|__ACME_EMAIL__|${ACME_EMAIL}|g" \
  -e "s|__USER__|${BASIC_USER}|g" \
  -e "s|__BCRYPT_HASH__|${BCRYPT_HASH}|g" \
  "${DEPLOY_DIR}/Caddyfile" > /etc/caddy/Caddyfile

mkdir -p /var/log/caddy
chown caddy:caddy /var/log/caddy

install -m 0644 "${DEPLOY_DIR}/fastcad.service"               /etc/systemd/system/fastcad.service
install -m 0644 "${DEPLOY_DIR}/fail2ban-jail.local"           /etc/fail2ban/jail.local
install -m 0644 "${DEPLOY_DIR}/fail2ban-filter-caddy-auth.conf" /etc/fail2ban/filter.d/caddy-auth.conf
install -m 0644 "${DEPLOY_DIR}/fastcad-cleanup.cron"          /etc/cron.d/fastcad-cleanup

systemctl daemon-reload

# ---------------------------------------------------------------------------
# Step 5 — firewall: only :22, :80, :443 from anywhere
# ---------------------------------------------------------------------------

ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow http
ufw allow https
yes | ufw enable

# ---------------------------------------------------------------------------
# Step 6 — start everything
# ---------------------------------------------------------------------------

systemctl enable --now caddy
systemctl restart caddy
systemctl enable --now fastcad
systemctl restart fastcad
systemctl enable --now fail2ban
systemctl restart fail2ban

# Auto-apply security upgrades.
dpkg-reconfigure -f noninteractive unattended-upgrades || true

echo
echo "=========================================================="
echo "  fastcad deployed."
echo "    URL : https://${HOST}/"
echo "    user: ${BASIC_USER}"
echo "    pass: ${BASIC_PASS}"
echo
echo "  Tail logs       :  journalctl -fu fastcad"
echo "  Caddy access    :  tail -f /var/log/caddy/access.log"
echo "  fail2ban status :  fail2ban-client status caddy-auth"
echo "  Rotate password :  rm ${DEPLOY_ENV_FILE} && bash ${0}"
echo "=========================================================="
