#!/usr/bin/env bash
#
# de BereBank installer for Ubuntu 24.04.
#
# Installs and configures:
#   - PostgreSQL (database server)
#   - Python backend (FastAPI/uvicorn) as a systemd service
#   - Frontend (React/Vite) built to static files
#   - nginx serving the frontend and SSL-offloading the backend
#   - Let's Encrypt certificate via certbot
#
# Usage:
#   cp berebank.conf.example berebank.conf
#   edit berebank.conf   (at minimum: DOMAIN and LETSENCRYPT_EMAIL)
#   sudo ./install.sh
#
# The script is idempotent: re-running it updates the code, rebuilds the
# frontend and reloads services, but keeps the database and generated secrets.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="${1:-$SCRIPT_DIR/berebank.conf}"
ENV_DIR="/etc/berebank"
ENV_FILE="$ENV_DIR/berebank.env"
SERVICE_NAME="berebank"

log()  { echo -e "\e[1;32m==>\e[0m $*"; }
fail() { echo -e "\e[1;31mERROR:\e[0m $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "Run as root: sudo ./install.sh"
[[ -f "$CONFIG_FILE" ]] || fail "Config file not found: $CONFIG_FILE
Copy berebank.conf.example to berebank.conf and edit it first."

# shellcheck source=berebank.conf.example
source "$CONFIG_FILE"

[[ -n "${DOMAIN:-}" && "$DOMAIN" != "berebank.example.com" ]] \
    || fail "Set DOMAIN in $CONFIG_FILE to your real domain name."
[[ -n "${LETSENCRYPT_EMAIL:-}" && "$LETSENCRYPT_EMAIL" != "you@example.com" ]] \
    || fail "Set LETSENCRYPT_EMAIL in $CONFIG_FILE."

INSTALL_DIR="${INSTALL_DIR:-/opt/berebank}"
DB_NAME="${DB_NAME:-berebank}"
DB_USER="${DB_USER:-berebank}"
ADMIN_EMAIL="${ADMIN_EMAIL:-manager@berebank.nl}"

# ---------------------------------------------------------------------------
log "Installing system packages"
# ---------------------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -qy python3 python3-venv python3-pip postgresql \
    nginx certbot python3-certbot-nginx rsync curl ca-certificates

# Ubuntu 24.04 ships Node 18, which is too old for the frontend build.
if ! command -v node >/dev/null || [[ "$(node -v | cut -dv -f2 | cut -d. -f1)" -lt 20 ]]; then
    log "Installing Node.js 22 (NodeSource)"
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -qy nodejs
fi

# ---------------------------------------------------------------------------
log "Configuring PostgreSQL"
# ---------------------------------------------------------------------------
systemctl enable --now postgresql

# Reuse the DB password from a previous run unless one is set in the config.
if [[ -z "${DB_PASSWORD:-}" && -f "$ENV_FILE" ]]; then
    DB_PASSWORD="$(grep -oP '(?<=^BEREBANK_DB_PASSWORD=).*' "$ENV_FILE" || true)"
fi
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -hex 24)}"

# cd to a directory the postgres user can read to avoid a psql warning.
cd /tmp
sudo -u postgres psql -v ON_ERROR_STOP=1 \
    -v db="$DB_NAME" -v user="$DB_USER" -v pass="$DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'user', :'pass')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'user') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'user', :'pass') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db') \gexec
SQL

# ---------------------------------------------------------------------------
log "Deploying application code to $INSTALL_DIR"
# ---------------------------------------------------------------------------
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
    --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
    --exclude 'dist' --exclude '__pycache__' --exclude '*.db' \
    --exclude 'deploy/berebank.conf' \
    "$REPO_DIR/" "$INSTALL_DIR/"

log "Setting up Python backend"
cd "$INSTALL_DIR/backend"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt 'psycopg[binary]>=3.2'

log "Building frontend"
cd "$INSTALL_DIR/frontend"
npm ci --no-audit --no-fund
npm run build

# ---------------------------------------------------------------------------
log "Writing backend environment file ($ENV_FILE)"
# ---------------------------------------------------------------------------
# Reuse previously generated secrets unless overridden in the config file.
if [[ -f "$ENV_FILE" ]]; then
    SECRET_KEY="${SECRET_KEY:-$(grep -oP '(?<=^BEREBANK_SECRET_KEY=).*' "$ENV_FILE" || true)}"
    ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(grep -oP '(?<=^BEREBANK_ADMIN_PASSWORD=).*' "$ENV_FILE" || true)}"
fi
SECRET_KEY="${SECRET_KEY:-$(openssl rand -hex 32)}"
GENERATED_ADMIN_PASSWORD=""
if [[ -z "${ADMIN_PASSWORD:-}" ]]; then
    ADMIN_PASSWORD="$(openssl rand -base64 12)"
    GENERATED_ADMIN_PASSWORD="$ADMIN_PASSWORD"
fi

mkdir -p "$ENV_DIR"
cat > "$ENV_FILE" <<EOF
BEREBANK_DATABASE_URL=postgresql+psycopg://$DB_USER:$DB_PASSWORD@127.0.0.1:5432/$DB_NAME
BEREBANK_SECRET_KEY=$SECRET_KEY
BEREBANK_ADMIN_EMAIL=$ADMIN_EMAIL
BEREBANK_ADMIN_PASSWORD=$ADMIN_PASSWORD
BEREBANK_DB_PASSWORD=$DB_PASSWORD
BEREBANK_PUBLIC_URL=https://$DOMAIN
EOF
chmod 600 "$ENV_FILE"

# ---------------------------------------------------------------------------
log "Installing systemd service ($SERVICE_NAME)"
# ---------------------------------------------------------------------------
chown -R www-data:www-data "$INSTALL_DIR"

cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=de BereBank backend
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=$INSTALL_DIR/backend
EnvironmentFile=$ENV_FILE
ExecStart=$INSTALL_DIR/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# ---------------------------------------------------------------------------
log "Configuring nginx for $DOMAIN"
# ---------------------------------------------------------------------------
cat > "/etc/nginx/sites-available/$SERVICE_NAME" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    root $INSTALL_DIR/frontend/dist;
    index index.html;

    # React router: serve index.html for unknown paths
    location / {
        try_files \$uri /index.html;
    }

    # REST API (strip the /api prefix, same as the Vite dev proxy)
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Live price WebSocket
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }

    # MCP server (Streamable HTTP), no prefix stripping
    location = /mcp {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # OAuth endpoints for the MCP flow (exact paths; ACME /.well-known/acme-challenge
    # is deliberately NOT proxied so certbot renewals keep working)
    location /oauth/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location = /authorize {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location = /token {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location = /register {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location = /revoke {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location = /.well-known/oauth-authorization-server {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location ~ ^/\.well-known/oauth-protected-resource(/.*)?\$ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

ln -sf "/etc/nginx/sites-available/$SERVICE_NAME" "/etc/nginx/sites-enabled/$SERVICE_NAME"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# ---------------------------------------------------------------------------
log "Requesting Let's Encrypt certificate (SSL offloading in nginx)"
# ---------------------------------------------------------------------------
certbot --nginx -d "$DOMAIN" -m "$LETSENCRYPT_EMAIL" \
    --agree-tos --no-eff-email --redirect --non-interactive --keep-until-expiring
systemctl reload nginx

# ---------------------------------------------------------------------------
log "Verifying installation"
# ---------------------------------------------------------------------------
sleep 2
if curl -fsS http://127.0.0.1:8000/health >/dev/null; then
    log "Backend is up."
else
    fail "Backend health check failed. Inspect: journalctl -u $SERVICE_NAME -n 50"
fi

echo
log "Installation complete: https://$DOMAIN"
echo "    BankManager login: $ADMIN_EMAIL"
if [[ -n "$GENERATED_ADMIN_PASSWORD" ]]; then
    echo "    Generated password: $GENERATED_ADMIN_PASSWORD"
    echo "    (also stored in $ENV_FILE)"
else
    echo "    Password: as configured"
fi
echo "    Secrets and DB credentials: $ENV_FILE"
echo "    Certificate renewal is automatic (systemd timer 'certbot.timer')."
