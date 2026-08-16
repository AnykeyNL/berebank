"""End-to-end MCP smoke test against a running backend on localhost:8000.

Walks the full OAuth 2.1 flow (dynamic client registration, authorize with
PKCE, the login/consent form, code exchange, refresh) and then exercises every
MCP tool over Streamable HTTP. Requires the same test user as smoke_test.py
(alice@example.com, created there via the manager account).
"""
import asyncio
import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
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


def tool_dict(result) -> dict:
    return json.loads(tool_text(result))


def tool_list(result) -> list:
    """Tools returning a list emit one JSON block per item."""
    items: list = []
    for block in result.content:
        if block.type != "text":
            continue
        parsed = json.loads(block.text)
        items.extend(parsed if isinstance(parsed, list) else [parsed])
    return items


async def exercise_tools(access_token: str, c: httpx.Client, web_token: str) -> None:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with streamablehttp_client(f"{BASE}/mcp", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            expected = {
                "list_markets", "get_candles", "analyze_market",
                "get_kimi_analysis", "get_fable5_analysis",
                "get_gtp56sol_analysis", "get_news", "get_outlooks",
                "get_opus_rankings", "get_opus_analysis",
                "get_opus_portfolio_advice", "get_account_status",
                "get_portfolio", "get_portfolio_history", "list_orders",
                "list_trades", "get_trade_history", "get_leaderboard",
                "get_market_hours", "get_leaderboard_history",
                "place_order", "cancel_order",
            }
            assert expected <= tools, f"missing tools: {expected - tools}"
            print("tools listed:", sorted(tools))

            r = await session.call_tool("list_markets", {"filter": "BTC-EUR"})
            assert not r.isError, tool_text(r)
            assert "BTC-EUR" in tool_text(r)
            btc = tool_list(r)[0]
            for field in ("tick_size", "amount_decimals", "min_order_base",
                          "amount_quantum", "min_order_eur"):
                assert field in btc, f"list_markets missing {field}"
            assert "E" not in str(btc["tick_size"]), btc["tick_size"]
            assert btc["next_open"] is None and btc["next_close"] is None, btc
            print("list_markets OK, sizing rules:",
                  {k: btc[k] for k in ("tick_size", "amount_decimals", "min_order_eur")})

            r = await session.call_tool("list_markets", {"filter": "AAPL-EUR"})
            aapl = tool_list(r)[0]
            assert aapl["next_close"], "stocks need calendar timestamps"
            print("list_markets carries exchange hours OK, AAPL next close",
                  aapl["next_close"])

            r = await session.call_tool("get_market_hours", {"asset_class": "stock"})
            assert not r.isError, tool_text(r)
            hours = tool_dict(r)["hours"][0]
            assert hours["calendar"] == "XNYS" and hours["always_open"] is False, hours
            assert hours["next_close"], hours
            assert hours["is_open"] or hours["next_open"], hours
            r = await session.call_tool("get_market_hours", {"market": "BTC-EUR"})
            crypto_hours = tool_dict(r)["hours"][0]
            assert crypto_hours["always_open"] is True, crypto_hours
            assert crypto_hours["is_open"] is True, crypto_hours
            print("get_market_hours OK, NYSE", "open" if hours["is_open"] else "closed")

            r = await session.call_tool("get_candles", {"market": "BTC-EUR"})
            assert not r.isError, tool_text(r)
            print("get_candles OK")

            r = await session.call_tool("get_gtp56sol_analysis", {
                "market": "BTC-EUR", "horizon": "1w",
            })
            assert not r.isError, tool_text(r)
            assert "probabilities" in tool_text(r)
            print("get_gtp56sol_analysis OK")

            r = await session.call_tool("get_news", {"market": "AAPL-EUR", "limit": 3})
            assert not r.isError, tool_text(r)
            print("get_news OK")

            # Compact by default: the chart payload only on request.
            r = await session.call_tool("analyze_market", {"market": "BTC-EUR"})
            assert not r.isError, tool_text(r)
            compact = tool_dict(r)
            assert "candles" not in compact, "analyze_market leaked candles by default"
            assert all(not s.get("series") for s in compact["strategies"].values())
            assert compact["strategies"]["rsi"]["signal"], "signals must survive the trim"
            compact_size = len(tool_text(r))

            r = await session.call_tool("analyze_market", {"market": "BTC-EUR", "verbose": True})
            assert not r.isError, tool_text(r)
            verbose = tool_dict(r)
            assert verbose["candles"], "verbose=true must return candles"
            assert any(s.get("series") for s in verbose["strategies"].values())
            print(f"analyze_market compact by default OK "
                  f"({compact_size} vs {len(tool_text(r))} chars verbose)")

            r = await session.call_tool("get_outlooks", {
                "markets": ["BTC-EUR", "ETH-EUR"], "engines": ["technical", "kimi"],
            })
            assert not r.isError, tool_text(r)
            batch = tool_dict(r)
            assert set(batch["engines"]) == {"technical", "kimi"}, batch["engines"]
            for market, per_engine in batch["outlooks"].items():
                assert market in ("BTC-EUR", "ETH-EUR"), market
                for outlook in per_engine.values():
                    assert "direction" in outlook and "score" in outlook, outlook
            assert "candles" not in tool_text(r)
            print("get_outlooks OK,", len(batch["outlooks"]), "markets")

            r = await session.call_tool("get_outlooks", {"markets": ["NOPE-EUR"]})
            assert r.isError and "unknown market" in tool_text(r).lower(), tool_text(r)
            r = await session.call_tool("get_outlooks", {})
            assert r.isError and "asset_class" in tool_text(r), tool_text(r)
            print("get_outlooks argument validation OK")

            r = await session.call_tool("get_opus_rankings", {"horizon": "1w", "limit": 5})
            assert not r.isError, tool_text(r)
            assert "net_edge_pct" in tool_text(r)
            print("get_opus_rankings OK")

            r = await session.call_tool("get_opus_analysis", {"market": "BTC-EUR"})
            assert not r.isError, tool_text(r)
            assert "recommendation" in tool_text(r)
            print("get_opus_analysis OK")

            r = await session.call_tool("get_opus_portfolio_advice", {})
            assert not r.isError, tool_text(r)
            assert "suggested_allocation" in tool_text(r)
            print("get_opus_portfolio_advice OK")

            r = await session.call_tool("get_portfolio", {})
            assert not r.isError, tool_text(r)
            assert "balance_eur" in tool_text(r)
            print("get_portfolio OK")

            for tool in ("list_orders", "list_trades", "get_trade_history"):
                r = await session.call_tool(tool, {})
                assert not r.isError, f"{tool}: {tool_text(r)}"
                print(f"{tool} OK")

            r = await session.call_tool("get_leaderboard", {})
            assert not r.isError, tool_text(r)
            entries = tool_list(r)
            assert entries and entries[0]["rank"] == 1
            assert any(e.get("is_you") for e in entries), "connected user missing from leaderboard"
            assert "user_id" not in entries[0]
            print("get_leaderboard OK,", len(entries), "traders")

            r = await session.call_tool("get_leaderboard_history", {"days": 7})
            assert not r.isError, tool_text(r)
            history = tool_dict(r)
            assert history["days"] == 7 and history["interval"] == "day", history
            for point in history["points"]:
                assert point["rank"] >= 1 and point["traders"] >= point["rank"], point
                assert point["created_at"].endswith("Z"), point
            r = await session.call_tool("get_leaderboard_history", {"interval": "week"})
            assert r.isError and "interval" in tool_text(r), tool_text(r)
            print("get_leaderboard_history OK,", len(history["points"]), "points")

            # Trading disabled -> get_account_status says so before any order
            set_trading(c, web_token, False)
            r = await session.call_tool("get_account_status", {})
            assert not r.isError, tool_text(r)
            status = tool_dict(r)
            assert status["trading_enabled"] is False, status
            assert status["trading_tools"] == [], status
            assert status["trading_disabled_reason"], status
            assert status["fee_tier"]["taker_pct"], status
            assert status["minimum_order_eur"] == "5", status
            print("get_account_status reports trading off OK")

            r = await session.call_tool("place_order", {
                "market": "BTC-EUR", "side": "buy", "order_type": "market", "amount_quote": "10",
            })
            assert r.isError and "disabled" in tool_text(r), tool_text(r)
            print("trading blocked while toggle off OK")

            set_trading(c, web_token, True)
            r = await session.call_tool("get_account_status", {})
            status = tool_dict(r)
            assert status["trading_enabled"] is True, status
            assert "place_order" in status["trading_tools"], status
            print("get_account_status reflects the toggle immediately OK")

            # Dry run: priced, validated, but nothing stored
            before = tool_list(await session.call_tool("list_orders", {}))
            r = await session.call_tool("place_order", {
                "market": "BTC-EUR", "side": "buy", "order_type": "market",
                "amount_quote": "10", "validate_only": True,
            })
            assert not r.isError, tool_text(r)
            preview = tool_dict(r)
            assert preview["validated_only"] is True, preview
            assert preview["fee_type"] == "taker" and preview["fee_eur"], preview
            after = tool_list(await session.call_tool("list_orders", {}))
            assert len(after) == len(before), "validate_only placed an order"
            print("place_order validate_only OK, fee", preview["fee_eur"])

            r = await session.call_tool("place_order", {
                "market": "BTC-EUR", "side": "buy", "order_type": "market",
                "amount_quote": "1", "validate_only": True,
            })
            assert r.isError and "Minimum order value" in tool_text(r), tool_text(r)
            print("validate_only rejects like a real placement OK")

            # Place a far-below-market limit order, then cancel it
            r = await session.call_tool("list_markets", {"filter": "BTC-EUR"})
            last_price = float(tool_list(r)[0]["last"])
            client_id = f"smoke-{int(time.time())}"
            args = {
                "market": "BTC-EUR", "side": "buy", "order_type": "limit",
                "amount": "0.001", "limit_price": str(round(last_price * 0.5)),
                "client_order_id": client_id,
            }
            r = await session.call_tool("place_order", args)
            assert not r.isError, tool_text(r)
            order = tool_dict(r)
            assert order["status"] == "open"
            assert order["client_order_id"] == client_id, order
            assert order["duplicate"] is False, order
            print("place_order (limit) OK, id", order["id"])

            # Replaying the identical call must not place a second order
            r = await session.call_tool("place_order", args)
            assert not r.isError, tool_text(r)
            replay = tool_dict(r)
            assert replay["id"] == order["id"], (replay, order)
            assert replay["duplicate"] is True, replay
            open_orders = tool_list(
                await session.call_tool("list_orders", {"status": "open"})
            )
            assert sum(1 for o in open_orders if o["client_order_id"] == client_id) == 1
            print("client_order_id replay returned the original order OK")

            r = await session.call_tool("cancel_order", {"order_id": order["id"]})
            assert not r.isError, tool_text(r)
            assert tool_dict(r)["status"] == "cancelled"
            print("cancel_order OK")

            # Expiry is resolved to a concrete moment at placement
            r = await session.call_tool("place_order", {
                "market": "BTC-EUR", "side": "buy", "order_type": "limit",
                "amount": "0.001", "limit_price": str(round(last_price * 0.5)),
                "expires_in_sessions": 2,
            })
            assert not r.isError, tool_text(r)
            expiring = tool_dict(r)
            assert expiring["time_in_force"] == "gtd", expiring
            assert expiring["expires_after_sessions"] == 2, expiring
            resolved = datetime.fromisoformat(expiring["expires_at"].replace("Z", "+00:00"))
            ahead = resolved - datetime.now(timezone.utc)
            assert timedelta(days=1) < ahead < timedelta(days=3), ahead
            print("place_order expiry resolved to", expiring["expires_at"])

            listed = next(
                o for o in tool_list(await session.call_tool("list_orders", {"status": "open"}))
                if o["id"] == expiring["id"]
            )
            assert listed["expires_at"] == expiring["expires_at"], listed
            await session.call_tool("cancel_order", {"order_id": expiring["id"]})

            r = await session.call_tool("place_order", {
                "market": "BTC-EUR", "side": "buy", "order_type": "market",
                "amount_quote": "10", "time_in_force": "day", "validate_only": True,
            })
            assert r.isError and "resting orders only" in tool_text(r), tool_text(r)
            print("expiry on a market order rejected OK")

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
