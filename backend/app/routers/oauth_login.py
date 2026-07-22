"""Login + consent page for the MCP OAuth flow.

The SDK's /authorize endpoint validates the OAuth request and redirects here
with a one-time transaction id. The user signs in with their normal BereBank
credentials and approves (or denies) the MCP client's access, after which we
redirect back to the client with an authorization code.
"""
from html import escape

from fastapi import APIRouter, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ..database import SessionLocal
from ..models import User
from ..oauth import complete_authorization, deny_authorization, get_pending
from ..security import verify_password

router = APIRouter(prefix="/oauth", tags=["oauth"])

_EXPIRED_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>de BereBank</title></head>
<body style="font-family:system-ui;background:#0f172a;color:#e2e8f0;display:flex;
justify-content:center;padding-top:4rem">
<p>This authorization request has expired. Please start again from your MCP client.</p>
</body></html>"""


def _login_page(txn: str, client_name: str, error: str | None = None) -> str:
    error_html = (
        f'<p style="color:#f87171;font-size:.875rem;margin:0 0 1rem">{escape(error)}</p>'
        if error else ""
    )
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>de BereBank — authorize access</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0;
         display: flex; justify-content: center; padding: 4rem 1rem; margin: 0; }}
  .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px;
           padding: 2rem; max-width: 24rem; width: 100%; }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .5rem; color: #fff; }}
  p {{ font-size: .875rem; color: #94a3b8; margin: 0 0 1.5rem; }}
  label {{ display: block; font-size: .875rem; margin-bottom: .25rem; color: #cbd5e1; }}
  input {{ width: 100%; box-sizing: border-box; background: #020617; color: #e2e8f0;
           border: 1px solid #334155; border-radius: 6px; padding: .5rem .75rem;
           font-size: .875rem; margin-bottom: 1rem; }}
  input:focus {{ outline: none; border-color: #f59e0b; }}
  .buttons {{ display: flex; gap: .75rem; }}
  button {{ flex: 1; border: none; border-radius: 6px; padding: .55rem;
            font-size: .875rem; font-weight: 600; cursor: pointer; }}
  .allow {{ background: #f59e0b; color: #0f172a; }}
  .allow:hover {{ background: #fbbf24; }}
  .deny {{ background: #334155; color: #e2e8f0; }}
  .deny:hover {{ background: #475569; }}
  .note {{ font-size: .75rem; color: #64748b; margin-top: 1rem; }}
</style>
</head>
<body>
<div class="card">
  <h1>de BereBank 🐻</h1>
  <p><strong style="color:#e2e8f0">{escape(client_name)}</strong> is asking for access
     to your BereBank account via MCP.</p>
  <form method="post" action="/oauth/login">
    <input type="hidden" name="txn" value="{escape(txn)}">
    {error_html}
    <label for="email">Email</label>
    <input id="email" name="email" type="email" required autofocus>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" required>
    <div class="buttons">
      <button class="allow" type="submit" name="action" value="allow">Allow access</button>
      <button class="deny" type="submit" name="action" value="deny">Deny</button>
    </div>
  </form>
  <p class="note">Market and portfolio information is always readable via MCP.
     Whether trading is allowed is controlled by the MCP setting in your profile.</p>
</div>
</body>
</html>"""


def _client_name(pending) -> str:
    return pending.client.client_name or pending.client.client_id


@router.get("/login", response_class=HTMLResponse)
def login_form(txn: str = Query(...)):
    pending = get_pending(txn)
    if pending is None:
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)
    return HTMLResponse(_login_page(txn, _client_name(pending)))


@router.post("/login")
def login_submit(
    txn: str = Form(...),
    email: str = Form(""),
    password: str = Form(""),
    action: str = Form("allow"),
):
    pending = get_pending(txn)
    if pending is None:
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    if action == "deny":
        return RedirectResponse(deny_authorization(txn), status_code=303)

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None or not verify_password(password, user.password_hash):
            return HTMLResponse(
                _login_page(txn, _client_name(pending), error="Invalid email or password"),
                status_code=401,
            )
        if not user.is_active:
            return HTMLResponse(
                _login_page(txn, _client_name(pending), error="Account is deactivated"),
                status_code=403,
            )
        redirect_url = complete_authorization(txn, user)
    finally:
        db.close()
    return RedirectResponse(redirect_url, status_code=303)
