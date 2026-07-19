# MCP Server for de BereBank — Design

Date: 2026-07-19
Status: Approved (chat), implementing

## Goal

Let users connect AI assistants (Claude, Cursor, etc.) to de BereBank through an MCP
server at `https://berebank.behold.com/mcp`. Every active user can read market data and
their own portfolio via MCP; a per-user profile toggle controls whether the assistant
may also place and cancel orders.

## Decisions (from brainstorming)

- **Auth:** OAuth 2.1 authorization-code flow with PKCE and dynamic client
  registration, as the MCP spec prescribes. No personal API keys.
- **Scope of tools:** full parity with the web app (markets, candles, portfolio,
  orders, trades, PnL history; plus place/cancel order when allowed).
- **Permission model:** two-state. MCP read access is always available to active
  users; a profile toggle `mcp_trading_enabled` (default **off**) gates the two
  trading tools. The flag is checked **at call time**, so switching it off takes
  effect immediately.
- **Architecture:** MCP server mounted inside the existing FastAPI backend
  (Option A) using the official `mcp` Python SDK (>= 1.28), Streamable HTTP
  transport, stateless mode with JSON responses. Tools call the existing service
  layer directly — no internal HTTP.

## Components

### 1. OAuth 2.1 authorization server (`backend/app/oauth.py`)

Implements the SDK's `OAuthAuthorizationServerProvider` protocol. The SDK's
`create_auth_routes` supplies the endpoints; we supply storage and identity:

- `/.well-known/oauth-authorization-server` — metadata (SDK-generated).
- `POST /register` — dynamic client registration; clients persisted in DB.
- `GET /authorize` — SDK validates the request, then our provider redirects to
  `GET /oauth/login?txn=…`, a server-rendered login + consent page
  (`backend/app/routers/oauth_login.py`). Pending authorizations are kept in an
  in-memory dict with a 10-minute TTL (single-process uvicorn).
- `POST /oauth/login` — verifies email/password against the existing `users`
  table, issues an authorization code, redirects back to the client.
- `POST /token` — code exchange (SDK verifies PKCE) and refresh-token rotation.
- `POST /revoke` — revokes refresh tokens.

Tokens:

- **Access token:** JWT signed with the existing `SECRET_KEY`, `aud: "berebank-mcp"`,
  `sub: user id`, 1-hour expiry. Never interchangeable with web-login JWTs
  (those have no `aud`; the web verifier rejects tokens with one — verified by test).
- **Refresh token:** opaque `secrets.token_urlsafe`, stored in DB, 30-day expiry,
  rotated on use, revocable.

New tables (created by `Base.metadata.create_all`): `oauth_clients` (client_id,
client metadata JSON), `oauth_auth_codes` (code, user_id, client_id, PKCE
challenge, redirect data, expiry), `oauth_refresh_tokens` (token, user_id,
client_id, scopes, expiry, revoked flag).

### 2. MCP server (`backend/app/mcp_server.py`)

`FastMCP` instance with `auth_server_provider` + `token_verifier` + `AuthSettings`
(issuer = `BEREBANK_PUBLIC_URL`, resource = `…/mcp`). The Starlette app from
`streamable_http_app()` is mounted at `/` in FastAPI **after** all existing
routes, so it serves `/mcp`, `/authorize`, `/token`, `/register`, `/revoke` and
both `.well-known` documents without touching existing paths. Its session
manager runs inside the existing lifespan. DNS-rebinding protection is disabled
(server binds 127.0.0.1; nginx fronts it).

Tools resolve the current user from the verified access token (`get_access_token()`
→ `sub` → DB, re-checking `is_active`):

| Tool | Access | Backed by |
| --- | --- | --- |
| `list_markets` | read | `routers.markets.list_markets` (optional substring filter) |
| `get_candles` | read | `routers.markets.get_candles` |
| `get_portfolio` | read | `routers.portfolio.get_portfolio` |
| `list_orders` | read | `routers.orders.list_orders` (optional status filter) |
| `list_trades` | read | `routers.orders.list_trades` |
| `get_trade_history` | read | `routers.orders.trade_history` (FIFO PnL) |
| `place_order` | trading | `services.trading.place_order` under `trade_lock` |
| `cancel_order` | trading | `services.trading.cancel_order` under `trade_lock` |

Router functions are called directly with explicit `user`/`db` arguments (they are
plain functions; `Depends` defaults are bypassed). Responses are the existing
Pydantic schemas dumped in JSON mode, so Decimals serialize as strings exactly like
the REST API. Trading tools raise a clear error when `mcp_trading_enabled` is off;
`TradingError` messages pass through unchanged.

### 3. Profile setting

- `users.mcp_trading_enabled` boolean, default false; additive migration in
  `main.migrate_schema()`.
- `UserOut` and `ProfileUpdate` gain the field; `PUT /auth/profile` updates it.
- `ProfilePage.tsx` gets an "MCP access" card: the server URL
  (`{origin}/mcp`) with copy button, an explanation, and the trading toggle.
  New i18n strings in `en.json` / `nl.json`.

### 4. Config, deploy, docs

- `BEREBANK_PUBLIC_URL` env (default `http://127.0.0.1:8000` for dev; HTTPS
  required otherwise by RFC 8414). `deploy/install.sh` writes
  `BEREBANK_PUBLIC_URL=https://$DOMAIN` and adds nginx locations for `/mcp`
  (buffering off, long read timeout), `/oauth/`, `= /authorize`, `= /token`,
  `= /register`, `= /revoke`, `/.well-known/oauth-authorization-server`, and
  `/.well-known/oauth-protected-resource`. The ACME path is untouched (no
  blanket `/.well-known/` proxy), keeping certbot renewals safe.
- README gains an MCP section (URL, connecting a client, the profile toggle).
- `requirements.txt`: add `mcp>=1.28`.

### 5. Testing

`backend/mcp_smoke_test.py` against a running backend, mirroring `smoke_test.py`:
registers an OAuth client, walks the authorize → login-form → code → token flow
with PKCE over httpx, connects with the MCP client SDK, lists tools, calls every
read tool, verifies trading tools are rejected while the toggle is off, enables
the toggle via `PUT /auth/profile`, places and cancels a limit order, and checks
that a web JWT is rejected at `/mcp` and an MCP token is rejected at `/portfolio`.

## Error handling

- Invalid/expired/aud-mismatched tokens → 401 from the SDK middleware with
  `WWW-Authenticate` pointing at the resource metadata (drives client re-auth).
- Deactivated user → token verification fails (checked on every request).
- Trading disabled → tool error `"Trading via MCP is disabled in your profile…"`.
- `TradingError` (insufficient balance, unknown market, minimum order, …) →
  tool error with the same message as the REST API.

## Out of scope

- Per-client (as opposed to per-user) permissions, order-size limits via MCP,
  MCP resources/prompts, admin tools over MCP, revoking web sessions.
