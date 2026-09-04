"""Initial user sign-in — the OIDC bootstrap the XAA flow depends on.

Complements ``routers/approvals.py`` (step-up mid-flow) and ``routers/mock_as.py``
(mock authorization server). Everything the shopper does after this — chat,
intents, restock, step-up, order placement — needs an ID token in a cookie.
This router is where that ID token comes from.

The exchange is `authorization_code` + PKCE, and the token endpoint is
authenticated with the agent's ``private_key_jwt``. It's the same wire flow
step-up uses; we reuse ``stepup._pkce`` and ``stepup._decode`` directly and
just skip the approval state machine. In mock mode the same code drives the
mock authorize page from ``routers/mock_as.py``. In okta mode it drives
Okta's hosted sign-in.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..approvals.stepup import _decode, _pkce
from ..config import settings

log = logging.getLogger("oktane.auth")
router = APIRouter(tags=["auth"])

# state -> (created_at, verifier, nonce, return_to)
_STATE_TTL = 600
_pending: dict[str, tuple[float, str, str, str]] = {}


def _reap() -> None:
    """Best-effort cleanup so a long-running process doesn't leak state."""
    now = time.time()
    for k in [k for k, entry in _pending.items() if now - entry[0] > _STATE_TTL]:
        _pending.pop(k, None)


def _callback_url() -> str:
    """Where Okta redirects after the shopper signs in.

    The URL is on the *storefront* origin so the resulting session cookie lands
    where the storefront can read it back on subsequent requests.
    """
    return f"{settings.web_base.rstrip('/')}/auth/callback"


@router.get("/auth/signin-url")
def signin_url(return_to: str = "/") -> dict[str, str]:
    """Build a fresh /authorize URL and stash its PKCE + nonce server-side.

    Returned to the storefront so it can 302 the shopper's browser. Nothing
    on the client ever sees the verifier — that's the whole point of PKCE.
    """
    _reap()
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    _pending[state] = (time.time(), verifier, nonce, return_to or "/")

    params = {
        "client_id": settings.agent_client_id,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": _callback_url(),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {"authorize_url": f"{settings.user_authorize_url}?{urlencode(params)}"}


class CompleteBody(BaseModel):
    code: str
    state: str


@router.post("/auth/complete-signin")
async def complete_signin(body: CompleteBody) -> dict[str, Any]:
    """Trade Okta's authorization code for an ID token and return it to the storefront.

    The storefront then sets the ``oktane_idt`` HttpOnly cookie itself, so the
    cookie is bound to the storefront origin rather than this one.
    """
    from ..tokens.agent_key import agent_key

    entry = _pending.pop(body.state, None)
    if entry is None:
        raise HTTPException(400, "invalid or unknown state (already used or expired)")
    created, verifier, nonce, return_to = entry
    if time.time() - created > _STATE_TTL:
        raise HTTPException(400, "state expired — try signing in again")

    token_url = settings.user_token_url
    request_body = {
        "grant_type": "authorization_code",
        "code": body.code,
        "redirect_uri": _callback_url(),
        "client_id": settings.agent_client_id,
        "code_verifier": verifier,
        "client_assertion_type": (
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ),
        "client_assertion": agent_key().client_assertion(token_url),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(token_url, data=request_body)

    payload: dict[str, Any] = {}
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 — Okta returns HTML on some errors
        pass

    if response.status_code != 200 or "id_token" not in payload:
        detail = payload.get("error_description") or payload.get("error") or response.text[:200]
        log.warning("code exchange failed at %s: %s", token_url, detail)
        raise HTTPException(400, f"token exchange failed: {detail}")

    id_token = str(payload["id_token"])
    try:
        claims = _decode(id_token)
    except Exception as exc:  # noqa: BLE001 — verifier surfaces its own reason
        raise HTTPException(401, f"invalid id token: {exc}") from exc

    if claims.get("nonce") != nonce:
        raise HTTPException(400, "nonce mismatch — this callback does not belong to this sign-in")

    return {
        "id_token": id_token,
        "profile": {
            "sub": str(claims.get("sub", "")),
            "email": str(claims.get("email", "")),
            "name": str(claims.get("name") or claims.get("given_name") or "Shopper"),
        },
        "return_to": return_to,
    }
