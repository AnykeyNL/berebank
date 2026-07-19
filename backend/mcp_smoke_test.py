"""End-to-end MCP smoke test against a running backend on localhost:8000.

Walks the full OAuth 2.1 flow (dynamic client registration, authorize with
PKCE, the login/consent form, code exchange, refresh) and then exercises every
MCP tool over Streamable HTTP. Requires the same test user as smoke_test.py
(alice@example.com, created there via the manager account).
"""
import asyncio
import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE = "http://127.0.0.1:8000"
REDIRECT_URI = "http://127.0.0.1:9999/callback"  # never actually served
USER_EMAIL = "alice@example.com"
USER_PASSWORD = "alice123"


def obtain_tokens(c: httpx.Client) -> dict:
    """Run the OAuth 2.1 authorization-code + PKCE flow like an MCP client would."""
    # Discovery
    r = c.get("/.well-known/oauth-authorization-server")
    r.raise_for_status()
    meta = r.json()
    print("discovery OK:", meta["authorization_endpoint"], meta["token_endpoint"])

    # Dynamic client registration
    r = c.post(meta["registration_endpoint"], json={
        "client_name": "MCP smoke test",
        "redirect_uris": [REDIRECT_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
    })
    assert r.status_code == 201, r.text
    client_info = r.json()
    print("registered client:", client_info["client_id"])

    # Authorize with PKCE -> redirected to the login page
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    r = c.get(meta["authorization_endpoint"], params={
        "response_type": "code",
        "client_id": client_info["client_id"],
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    assert r.status_code in (302, 307), r.text
    login_url = r.headers["location"]
    assert "/oauth/login" in login_url
    txn = parse_qs(urlparse(login_url).query)["txn"][0]

    # Login page renders
    r = c.get(login_url)
    assert r.status_code == 200 and "de BereBank" in r.text
    print("login page OK")

    # Wrong password is rejected
    r = c.post("/oauth/login", data={
        "txn": txn, "email": USER_EMAIL, "password": "wrong", "action": "allow",
    })
    assert r.status_code == 401
    print("wrong password rejected")

    # Correct login -> redirect back with code
    r = c.post("/oauth/login", data={
        "txn": txn, "email": USER_EMAIL, "password": USER_PASSWORD, "action": "allow",
    })
    assert r.status_code == 303, r.text
    cb = urlparse(r.headers["location"])
    q = parse_qs(cb.query)
    assert cb.netloc == "127.0.0.1:9999" and q["state"][0] == state
    code = q["code"][0]
    print("authorization code obtained")

    # Token exchange (PKCE verified server-side)
    r = c.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_info["client_id"],
        "client_secret": client_info["client_secret"],
        "code_verifier": verifier,
    })
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert tokens["token_type"].lower() == "bearer" and tokens["refresh_token"]
    print("token exchange OK, expires_in", tokens["expires_in"])

    # Refresh token rotation
    r = c.post(meta["token_endpoint"], data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": client_info["client_id"],
        "client_secret": client_info["client_secret"],
    })
    assert r.status_code == 200, r.text
    new_tokens = r.json()
    assert new_tokens["access_token"] and new_tokens["refresh_token"] != tokens["refresh_token"]
    print("refresh + rotation OK")

    # Old refresh token no longer works
    r = c.post(meta["token_endpoint"], data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": client_info["client_id"],
        "client_secret": client_info["client_secret"],
    })
    assert r.status_code == 400
    print("old refresh token rejected")

    return new_tokens


def check_token_separation(c: httpx.Client, mcp_access_token: str) -> str:
    """Web JWTs and MCP tokens must not be interchangeable."""
    r = c.post("/auth/login", json={"email": USER_EMAIL, "password": USER_PASSWORD})
    r.raise_for_status()
    web_token = r.json()["access_token"]

    r = c.get("/portfolio", headers={"Authorization": f"Bearer {mcp_access_token}"})
    assert r.status_code == 401, f"MCP token must not work on the REST API ({r.status_code})"
    r = c.post("/mcp", headers={
        "Authorization": f"Bearer {web_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }, json={"jsonrpc": "2.0", "method": "ping", "id": 1})
    assert r.status_code == 401, f"web token must not work on /mcp ({r.status_code})"
    print("token separation OK (web<->MCP tokens rejected crosswise)")
    return web_token


def set_trading(c: httpx.Client, web_token: str, enabled: bool) -> None:
    r = c.put("/auth/profile", headers={"Authorization": f"Bearer {web_token}"},
              json={"mcp_trading_enabled": enabled})
    r.raise_for_status()
    assert r.json()["mcp_trading_enabled"] is enabled


def tool_text(result) -> str:
    return " ".join(b.text for b in result.content if b.type == "text")


async def exercise_tools(access_token: str, c: httpx.Client, web_token: str) -> None:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with streamablehttp_client(f"{BASE}/mcp", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            expected = {"list_markets", "get_candles", "get_news", "get_portfolio", "list_orders",
                        "list_trades", "get_trade_history", "place_order", "cancel_order"}
            assert expected <= tools, f"missing tools: {expected - tools}"
            print("tools listed:", sorted(tools))

            r = await session.call_tool("list_markets", {"filter": "BTC-EUR"})
            assert not r.isError, tool_text(r)
            assert "BTC-EUR" in tool_text(r)
            print("list_markets OK")

            r = await session.call_tool("get_candles", {"market": "BTC-EUR"})
            assert not r.isError, tool_text(r)
            print("get_candles OK")

            r = await session.call_tool("get_news", {"market": "ASML-EUR", "limit": 3})
            assert not r.isError, tool_text(r)
            print("get_news OK")

            r = await session.call_tool("get_portfolio", {})
            assert not r.isError, tool_text(r)
            assert "balance_eur" in tool_text(r)
            print("get_portfolio OK")

            for tool in ("list_orders", "list_trades", "get_trade_history"):
                r = await session.call_tool(tool, {})
                assert not r.isError, f"{tool}: {tool_text(r)}"
                print(f"{tool} OK")

            # Trading disabled -> clear error
            set_trading(c, web_token, False)
            r = await session.call_tool("place_order", {
                "market": "BTC-EUR", "side": "buy", "order_type": "market", "amount_quote": "10",
            })
            assert r.isError and "disabled" in tool_text(r), tool_text(r)
            print("trading blocked while toggle off OK")

            # Enable trading -> place a far-below-market limit order, then cancel it
            set_trading(c, web_token, True)
            r = await session.call_tool("list_markets", {"filter": "BTC-EUR"})
            import json as _json
            last = _json.loads(tool_text(r))
            last_price = float((last[0] if isinstance(last, list) else last)["last"])
            r = await session.call_tool("place_order", {
                "market": "BTC-EUR", "side": "buy", "order_type": "limit",
                "amount": "0.001", "limit_price": str(round(last_price * 0.5)),
            })
            assert not r.isError, tool_text(r)
            order = _json.loads(tool_text(r))
            assert order["status"] == "open"
            print("place_order (limit) OK, id", order["id"])

            r = await session.call_tool("cancel_order", {"order_id": order["id"]})
            assert not r.isError, tool_text(r)
            assert _json.loads(tool_text(r))["status"] == "cancelled"
            print("cancel_order OK")

            # Invalid order -> engine error passes through
            r = await session.call_tool("place_order", {
                "market": "BTC-EUR", "side": "buy", "order_type": "market", "amount_quote": "1",
            })
            assert r.isError and "Minimum order value" in tool_text(r), tool_text(r)
            print("minimum order validation OK")

            set_trading(c, web_token, False)


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=30, follow_redirects=False)

    # Unauthenticated /mcp must 401 and point at the resource metadata
    r = c.post("/mcp", json={"jsonrpc": "2.0", "method": "ping", "id": 1},
               headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401, r.status_code
    print("unauthenticated /mcp rejected, WWW-Authenticate:",
          r.headers.get("www-authenticate", "")[:80])

    r = c.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200, r.status_code
    print("protected resource metadata OK:", r.json()["authorization_servers"])

    tokens = obtain_tokens(c)
    web_token = check_token_separation(c, tokens["access_token"])
    asyncio.run(exercise_tools(tokens["access_token"], c, web_token))

    print("\nALL MCP SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
