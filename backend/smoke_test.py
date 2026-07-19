"""End-to-end smoke test against a running backend on localhost:8000."""
import httpx

BASE = "http://127.0.0.1:8000"

c = httpx.Client(base_url=BASE, timeout=15)

# 1. Login as bank manager
r = c.post("/auth/login", json={"email": "manager@berebank.nl", "password": "manager123"})
r.raise_for_status()
mgr = {"Authorization": f"Bearer {r.json()['access_token']}"}
print("manager login OK")

# 2. Create a user with initial balance
r = c.post("/admin/users", headers=mgr, json={
    "email": "alice@example.com", "password": "alice123",
    "display_name": "Alice", "initial_balance_eur": 10000,
})
assert r.status_code in (201, 409), r.text
print("create user:", r.status_code)

# 3. Settings endpoint
r = c.get("/admin/settings", headers=mgr)
r.raise_for_status()
print("settings:", r.json()["connection"])

# 4. Login as alice
r = c.post("/auth/login", json={"email": "alice@example.com", "password": "alice123"})
r.raise_for_status()
alice = {"Authorization": f"Bearer {r.json()['access_token']}"}

# 5. Markets
r = c.get("/markets", headers=alice)
r.raise_for_status()
btc = next(m for m in r.json() if m["market"] == "BTC-EUR")
print("BTC-EUR:", btc["last"], "bid", btc["bid"], "ask", btc["ask"])

# 6. Market buy for EUR 1000
r = c.post("/orders", headers=alice, json={
    "market": "BTC-EUR", "side": "buy", "order_type": "market", "amount_quote": 1000,
})
print("market buy:", r.status_code, r.json())
assert r.status_code == 201

# 7. Portfolio
r = c.get("/portfolio", headers=alice)
r.raise_for_status()
p = r.json()
print("portfolio: cash", p["balance_eur"], "holdings", p["holdings"], "total", p["total_value_eur"])

# 8. Market sell half the BTC
btc_amount = float(p["holdings"][0]["amount"])
r = c.post("/orders", headers=alice, json={
    "market": "BTC-EUR", "side": "sell", "order_type": "market", "amount": round(btc_amount / 2, 8),
})
print("market sell:", r.status_code, r.json())
assert r.status_code == 201

# 9. Limit buy far below market (stays open), then cancel
low_price = round(float(btc["last"]) * 0.5, 0)
r = c.post("/orders", headers=alice, json={
    "market": "BTC-EUR", "side": "buy", "order_type": "limit", "amount": 0.001, "limit_price": low_price,
})
print("limit buy:", r.status_code, r.json())
assert r.status_code == 201
order_id = r.json()["id"]
assert r.json()["status"] == "open"

r = c.get("/portfolio", headers=alice)
print("reserved while limit open:", r.json()["reserved_eur"])

r = c.delete(f"/orders/{order_id}", headers=alice)
print("cancel:", r.status_code, r.json()["status"])
assert r.json()["status"] == "cancelled"

# 10. Limit buy above market (should fill immediately as it crosses)
high_price = round(float(btc["ask"]) * 1.05, 0)
r = c.post("/orders", headers=alice, json={
    "market": "BTC-EUR", "side": "buy", "order_type": "limit", "amount": 0.001, "limit_price": high_price,
})
print("crossing limit buy:", r.status_code, r.json()["status"], "fee", r.json()["fee_paid"])
assert r.json()["status"] == "filled"

# 11. Trades and fee tier
r = c.get("/trades", headers=alice)
print("trades:", len(r.json()))
r = c.get("/portfolio", headers=alice)
p = r.json()
print("final: cash", p["balance_eur"], "reserved", p["reserved_eur"], "fee tier", p["fee_tier"])

# 12. Insufficient balance is rejected
r = c.post("/orders", headers=alice, json={
    "market": "BTC-EUR", "side": "buy", "order_type": "market", "amount_quote": 10_000_000,
})
assert r.status_code == 400, r.text
print("insufficient balance rejected:", r.json()["detail"])

# 13. Non-manager cannot access admin
r = c.get("/admin/users", headers=alice)
assert r.status_code == 403
print("role control OK")

print("\nALL SMOKE TESTS PASSED")
