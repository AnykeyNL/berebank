import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.environ.get(
    "BEREBANK_DATABASE_URL", f"sqlite:///{BASE_DIR / 'berebank.db'}"
)

SECRET_KEY = os.environ.get(
    "BEREBANK_SECRET_KEY", "dev-secret-change-me-in-production"
)
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

# Public base URL of this deployment, used as the OAuth issuer and to build
# the MCP endpoint URL. Must be HTTPS in production (RFC 8414); the localhost
# default keeps development working without configuration.
PUBLIC_URL = os.environ.get("BEREBANK_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")

# MCP OAuth token lifetimes.
MCP_ACCESS_TOKEN_EXPIRE_SECONDS = 3600  # 1 hour
MCP_REFRESH_TOKEN_EXPIRE_DAYS = 30
MCP_AUDIENCE = "berebank-mcp"  # `aud` claim separating MCP tokens from web JWTs

# Seed credentials for the initial BankManager account (created only when no
# bank manager exists yet).
ADMIN_EMAIL = os.environ.get("BEREBANK_ADMIN_EMAIL", "manager@berebank.nl")
ADMIN_PASSWORD = os.environ.get("BEREBANK_ADMIN_PASSWORD", "manager123")

BITVAVO_REST_URL = "https://api.bitvavo.com/v2"
BITVAVO_WS_URL = "wss://ws.bitvavo.com/v2/"

# GitHub webhook for automatic deployments. The endpoint at /webhook/github is
# disabled unless a secret is configured (deploy/install.sh generates one).
GITHUB_WEBHOOK_SECRET = os.environ.get("BEREBANK_GITHUB_WEBHOOK_SECRET", "")
GITHUB_WEBHOOK_BRANCH = os.environ.get("BEREBANK_GITHUB_WEBHOOK_BRANCH", "main")
# Flag file touched by the webhook; watched by the berebank-update.path
# systemd unit which then runs deploy/update.sh as root.
UPDATE_FLAG_FILE = os.environ.get(
    "BEREBANK_UPDATE_FLAG_FILE", "/run/berebank/update-requested"
)

# Comma-separated list of allowed CORS origins (frontend dev server by default).
CORS_ORIGINS = os.environ.get(
    "BEREBANK_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

MIN_ORDER_EUR = 5  # Bitvavo minimum order size
