# de BereBank 🐻

A simulated crypto exchange for practicing trading with paper money. Users trade in EUR
against **live Bitvavo market data**, with realistic [Bitvavo trading fees](https://bitvavo.com/en/fees)
(Category A maker/taker tiers based on trailing 30-day volume).

No real money or real orders are ever involved — only the market data is real.

## Features

- Live prices for all ~430 EUR markets via the [Bitvavo public WebSocket API](https://docs.bitvavo.com/docs/websocket-api/) (no API key required)
- Market orders (instant fill at live bid/ask, taker fee) and limit orders (filled by a background matcher when the live price crosses, maker fee)
- Portfolio view: EUR cash, reserved funds, crypto holdings with live valuation, total account value, current fee tier
- Role system: regular users trade; **BankManager** creates accounts, sets initial/current balances, enables/disables users, and manages the Bitvavo API configuration
- Fee tiers identical to Bitvavo Category A (0.15% maker / 0.25% taker at the base tier, decreasing with 30-day volume)

## Stack

| Part | Technology |
| --- | --- |
| Backend | Python 3.12+, FastAPI, SQLAlchemy, SQLite |
| Frontend | React + TypeScript (Vite), Tailwind CSS |
| Market data | Bitvavo WebSocket (`ticker24h` channel) with REST prefill and auto-reconnect |

## Development setup (Windows)

Prerequisites: Python 3.12+, Node.js 20+.

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload
```

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` and `/ws` to the backend
on port 8000.

### Default BankManager account

On first start the backend seeds a BankManager account:

- Email: `manager@berebank.nl`
- Password: `manager123`

**Change this password immediately** (or set `BEREBANK_ADMIN_EMAIL` / `BEREBANK_ADMIN_PASSWORD`
before first start). Log in as the manager, go to **Admin**, and create user accounts with
their initial EUR balance.

### Smoke test

With the backend running:

```powershell
cd backend
.\.venv\Scripts\python smoke_test.py
```

This exercises login, account creation, market/limit orders, cancellation, fees,
portfolio, and role checks against the live API.

## Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `BEREBANK_DATABASE_URL` | `sqlite:///backend/berebank.db` | SQLAlchemy database URL |
| `BEREBANK_SECRET_KEY` | dev value | JWT signing key — **set a random value in production** |
| `BEREBANK_ADMIN_EMAIL` | `manager@berebank.nl` | Seed BankManager email |
| `BEREBANK_ADMIN_PASSWORD` | `manager123` | Seed BankManager password |
| `BEREBANK_CORS_ORIGINS` | `http://localhost:5173,...` | Allowed CORS origins (not needed behind nginx) |

## Production deployment (Ubuntu Linux 24.04)

The `deploy/install.sh` script performs a complete production installation:

- PostgreSQL as the database server
- Backend in a Python venv, run by a systemd service (`berebank`)
- Frontend built to static files
- nginx serving the frontend, proxying the API/WebSocket, and doing SSL offloading
- Let's Encrypt certificate via certbot (auto-renewed by `certbot.timer`)

Requirements: a fresh Ubuntu 24.04 server, and a DNS record for your domain
pointing at it (needed for the Let's Encrypt HTTP challenge).

```bash
# on the server, from a copy of this repository:
cd deploy
cp berebank.conf.example berebank.conf
nano berebank.conf     # set DOMAIN and LETSENCRYPT_EMAIL at minimum
sudo ./install.sh
```

All available settings (domain, certificate email, database name/user,
seed admin account, install directory) are documented in
[`deploy/berebank.conf.example`](deploy/berebank.conf.example). Anything
secret that you leave empty (database password, JWT key, admin password) is
auto-generated and stored in `/etc/berebank/berebank.env`; the generated
admin password is printed at the end of the install.

The script is idempotent: re-run it after pulling new code to update the
deployment while keeping the database and generated secrets.

## How the simulation works

- **Prices**: the backend keeps an in-memory cache of best bid/ask/last for every EUR
  market, fed by Bitvavo's `ticker24h` WebSocket channel and prefetched over REST at startup.
- **Market orders** fill immediately at the live ask (buy) or bid (sell) and pay the taker fee.
- **Limit orders** reserve funds up front (EUR incl. fee for buys, crypto for sells).
  A matcher fills them at the limit price when the live market crosses it, paying the maker fee.
- **Fees** are deducted in EUR and rounded up to 8 decimals, matching Bitvavo's rules.
  Your tier is recalculated from your trailing 30-day executed volume on every order.
- Minimum order value is EUR 5, like Bitvavo.
