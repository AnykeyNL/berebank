"""Standalone verification of self-registration with BankManager approval.

Run: .venv\\Scripts\\python test_registration.py
"""
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.models import Account, RegistrationRequest, User
from app.routers import admin, auth
from app.security import hash_password

# StaticPool: TestClient serves sync endpoints from a thread pool, and every
# connection to a plain in-memory SQLite would otherwise see its own empty db.
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

app = FastAPI()
app.include_router(auth.router)
app.include_router(admin.router)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

passed = failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


def seed_user(email: str, role: str = "user") -> None:
    db = TestSession()
    user = User(
        email=email, password_hash=hash_password("secret123"), display_name="Seed", role=role
    )
    db.add(user)
    db.flush()
    db.add(Account(user_id=user.id, balance_eur=Decimal("0")))
    db.commit()
    db.close()


seed_user("manager@test.nl", role="bank_manager")
seed_user("trader@test.nl")

client = TestClient(app)


def token_for(email: str) -> dict:
    resp = client.post("/auth/login", json={"email": email, "password": "secret123"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


manager = token_for("manager@test.nl")
trader = token_for("trader@test.nl")

print("register: happy path")
resp = client.post(
    "/auth/register",
    json={
        "display_name": "New Trader",
        "email": "New@Example.com",
        "whatsapp_number": "+31 6 1234-5678",
        "password": "hunter22",
    },
)
check("returns 200", resp.status_code == 200, f"(got {resp.status_code}: {resp.text})")
db = TestSession()
request = db.scalar(select(RegistrationRequest).where(RegistrationRequest.email == "new@example.com"))
check("request stored with lowercased email", request is not None)
check("whatsapp normalized", request is not None and request.whatsapp_number == "+31612345678",
      f"(got {request.whatsapp_number if request else None})")
check("password stored hashed", request is not None and request.password_hash != "hunter22")
db.close()

print("register: rejections")
resp = client.post(
    "/auth/register",
    json={"display_name": "X", "email": "trader@test.nl",
          "whatsapp_number": "+31612345678", "password": "hunter22"},
)
check("existing user email -> 409", resp.status_code == 409, f"(got {resp.status_code})")
resp = client.post(
    "/auth/register",
    json={"display_name": "X", "email": "new@example.com",
          "whatsapp_number": "+31612345678", "password": "hunter22"},
)
check("pending email -> 409 awaiting approval",
      resp.status_code == 409 and "awaiting" in resp.json()["detail"],
      f"(got {resp.status_code}: {resp.text})")
resp = client.post(
    "/auth/register",
    json={"display_name": "X", "email": "bad@example.com",
          "whatsapp_number": "0612345678", "password": "hunter22"},
)
check("whatsapp without country code -> 422", resp.status_code == 422, f"(got {resp.status_code})")
resp = client.post(
    "/auth/register",
    json={"display_name": "X", "email": "bad@example.com",
          "whatsapp_number": "+31612345678", "password": "short"},
)
check("short password -> 422", resp.status_code == 422, f"(got {resp.status_code})")

print("login while pending")
resp = client.post("/auth/login", json={"email": "new@example.com", "password": "hunter22"})
check("pending login -> 403 awaiting approval",
      resp.status_code == 403 and "awaiting approval" in resp.json()["detail"],
      f"(got {resp.status_code}: {resp.text})")
resp = client.post("/auth/login", json={"email": "nobody@test.nl", "password": "hunter22"})
check("unknown email still generic 401", resp.status_code == 401, f"(got {resp.status_code})")

print("admin queue: access control")
resp = client.get("/admin/registration-requests", headers=trader)
check("regular user -> 403", resp.status_code == 403, f"(got {resp.status_code})")
resp = client.get("/admin/registration-requests", headers=manager)
check("manager sees the request", resp.status_code == 200 and len(resp.json()) == 1,
      f"(got {resp.status_code}: {resp.text})")
request_id = resp.json()[0]["id"]
check("queue row carries whatsapp", resp.json()[0]["whatsapp_number"] == "+31612345678")

print("approve")
resp = client.post(
    f"/admin/registration-requests/{request_id}/approve",
    headers=manager,
    json={"initial_balance_eur": "2500"},
)
check("approve returns the new user", resp.status_code == 200, f"(got {resp.status_code}: {resp.text})")
body = resp.json()
check("role is user", body.get("role") == "user")
check("whatsapp copied to user", body.get("whatsapp_number") == "+31612345678")
check("balance set from approval", Decimal(body.get("balance_eur", "0")) == Decimal("2500"))
db = TestSession()
check("request removed after approval",
      db.scalar(select(RegistrationRequest).where(RegistrationRequest.email == "new@example.com")) is None)
db.close()
resp = client.post("/auth/login", json={"email": "new@example.com", "password": "hunter22"})
check("approved user can log in with registered password", resp.status_code == 200,
      f"(got {resp.status_code}: {resp.text})")
resp = client.post(
    f"/admin/registration-requests/{request_id}/approve",
    headers=manager,
    json={"initial_balance_eur": "2500"},
)
check("approving again -> 404", resp.status_code == 404, f"(got {resp.status_code})")

print("reject")
client.post(
    "/auth/register",
    json={"display_name": "Second", "email": "second@example.com",
          "whatsapp_number": "+491701234567", "password": "hunter22"},
)
requests = client.get("/admin/registration-requests", headers=manager).json()
second_id = requests[0]["id"]
resp = client.delete(f"/admin/registration-requests/{second_id}", headers=manager)
check("reject -> 204", resp.status_code == 204, f"(got {resp.status_code})")
resp = client.post(
    "/auth/register",
    json={"display_name": "Second", "email": "second@example.com",
          "whatsapp_number": "+491701234567", "password": "hunter22"},
)
check("can register again after rejection", resp.status_code == 200, f"(got {resp.status_code})")

print("approve conflict: email became a user in the meantime")
seed_user("second@example.com")
requests = client.get("/admin/registration-requests", headers=manager).json()
conflict_id = requests[0]["id"]
resp = client.post(
    f"/admin/registration-requests/{conflict_id}/approve",
    headers=manager,
    json={"initial_balance_eur": "1000"},
)
check("approve of taken email -> 409", resp.status_code == 409, f"(got {resp.status_code})")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
