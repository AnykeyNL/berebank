"""GitHub webhook receiver for automatic deployments.

GitHub POSTs to /api/webhook/github on every push (nginx strips the /api
prefix). After verifying the HMAC signature, the handler touches a flag file
that a root-level systemd path unit (berebank-update.path) watches; that unit
runs deploy/update.sh outside this process. The web process itself never
gains root and never restarts itself mid-request.

The endpoint is disabled (404) unless BEREBANK_GITHUB_WEBHOOK_SECRET is set.
"""

import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from ..config import GITHUB_WEBHOOK_BRANCH, GITHUB_WEBHOOK_SECRET, UPDATE_FLAG_FILE

router = APIRouter(prefix="/webhook", tags=["webhook"])
logger = logging.getLogger("berebank.webhook")


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str | None, Header()] = None,
):
    if not GITHUB_WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Webhook not configured")

    body = await request.body()
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    if not x_hub_signature_256 or not hmac.compare_digest(expected, x_hub_signature_256):
        logger.warning("Webhook rejected: bad or missing signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    if x_github_event == "ping":
        return {"status": "pong"}
    if x_github_event != "push":
        return {"status": "ignored", "reason": f"event '{x_github_event}' is not a push"}

    try:
        ref = json.loads(body).get("ref", "")
    except (json.JSONDecodeError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    if ref != f"refs/heads/{GITHUB_WEBHOOK_BRANCH}":
        return {"status": "ignored", "reason": f"push to '{ref}', not branch '{GITHUB_WEBHOOK_BRANCH}'"}

    flag = Path(UPDATE_FLAG_FILE)
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
    except OSError as exc:
        logger.error("Could not write update flag %s: %s", flag, exc)
        raise HTTPException(status_code=500, detail="Could not request update")

    logger.info("Push to %s received; update requested via %s", ref, flag)
    return {"status": "update requested", "branch": GITHUB_WEBHOOK_BRANCH}
