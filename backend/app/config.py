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

# Seed credentials for the initial BankManager account (created only when no
# bank manager exists yet).
ADMIN_EMAIL = os.environ.get("BEREBANK_ADMIN_EMAIL", "manager@berebank.nl")
ADMIN_PASSWORD = os.environ.get("BEREBANK_ADMIN_PASSWORD", "manager123")

BITVAVO_REST_URL = "https://api.bitvavo.com/v2"
BITVAVO_WS_URL = "wss://ws.bitvavo.com/v2/"

# Comma-separated list of allowed CORS origins (frontend dev server by default).
CORS_ORIGINS = os.environ.get(
    "BEREBANK_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

MIN_ORDER_EUR = 5  # Bitvavo minimum order size
