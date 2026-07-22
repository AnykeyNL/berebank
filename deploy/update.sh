#!/usr/bin/env bash
#
# de BereBank updater.
#
# Pulls the latest code from GitHub and redeploys the backend and frontend
# without touching any data:
#   - PostgreSQL database:            untouched
#   - /etc/berebank/berebank.env:     untouched (secrets, DB password, admin)
#   - nginx config and certificates:  untouched
#
# Usage (on the server, from the repository clone used for installation):
#   cd deploy
#   sudo ./update.sh            # pull and redeploy if there are new commits
#   sudo ./update.sh --force    # redeploy even when already up to date
#
# The script pulls the current branch from origin, syncs the code to the
# install directory, updates Python/Node dependencies, rebuilds the frontend
# and restarts the backend service.

set -euo pipefail

REPO_URL="https://github.com/AnykeyNL/berebank.git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$SCRIPT_DIR/berebank.conf"
SERVICE_NAME="berebank"

log()  { echo -e "\e[1;32m==>\e[0m $*"; }
fail() { echo -e "\e[1;31mERROR:\e[0m $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "Run as root: sudo ./update.sh"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

# INSTALL_DIR may be customised in berebank.conf; everything else in the
# config is only needed for the initial installation.
INSTALL_DIR="/opt/berebank"
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=berebank.conf.example
    source "$CONFIG_FILE"
    INSTALL_DIR="${INSTALL_DIR:-/opt/berebank}"
fi
[[ -d "$INSTALL_DIR" ]] || fail "Install directory $INSTALL_DIR not found. Run install.sh first."

# ---------------------------------------------------------------------------
log "Pulling latest code from $REPO_URL"
# ---------------------------------------------------------------------------
git() { command git -c safe.directory="$REPO_DIR" "$@"; }

[[ -d "$REPO_DIR/.git" ]] \
    || fail "$REPO_DIR is not a git clone. Update requires a clone of $REPO_URL
(e.g. git clone $REPO_URL and copy deploy/berebank.conf into its deploy/ directory)."

# Untracked files (e.g. a hand-copied update.sh or notes) are harmless and
# don't block a pull; only modifications to tracked files do.
DIRTY="$(git -C "$REPO_DIR" status --porcelain --untracked-files=no)"
if [[ -n "$DIRTY" ]]; then
    fail "Tracked files in $REPO_DIR have local modifications:
$DIRTY
Revert them first (git -C $REPO_DIR checkout -- <file>), or stash them."
fi

BRANCH="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)"
OLD_HEAD="$(git -C "$REPO_DIR" rev-parse HEAD)"
git -C "$REPO_DIR" pull --ff-only "$REPO_URL" "$BRANCH"
NEW_HEAD="$(git -C "$REPO_DIR" rev-parse HEAD)"
log "Now at commit $(git -C "$REPO_DIR" rev-parse --short HEAD) on branch $BRANCH"

if [[ "$OLD_HEAD" == "$NEW_HEAD" && $FORCE -eq 0 ]]; then
    log "Already up to date; nothing to deploy. (Use --force to redeploy anyway.)"
    exit 0
fi

# ---------------------------------------------------------------------------
log "Deploying application code to $INSTALL_DIR"
# ---------------------------------------------------------------------------
# Same excludes as install.sh: never overwrite local databases or config,
# and keep the existing venv/node_modules so dependency installs are fast.
rsync -a --delete \
    --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
    --exclude 'dist' --exclude '__pycache__' --exclude '*.db' \
    --exclude 'deploy/berebank.conf' \
    "$REPO_DIR/" "$INSTALL_DIR/"

log "Updating Python backend dependencies"
cd "$INSTALL_DIR/backend"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt 'psycopg[binary]>=3.2'

log "Rebuilding frontend"
cd "$INSTALL_DIR/frontend"
npm ci --no-audit --no-fund
npm run build

chown -R www-data:www-data "$INSTALL_DIR"

# ---------------------------------------------------------------------------
log "Restarting backend service"
# ---------------------------------------------------------------------------
systemctl restart "$SERVICE_NAME"

sleep 2
if curl -fsS http://127.0.0.1:8000/health >/dev/null; then
    log "Backend is up."
else
    fail "Backend health check failed after update. Inspect: journalctl -u $SERVICE_NAME -n 50"
fi

log "Update complete. Database, secrets and certificates were left untouched."
