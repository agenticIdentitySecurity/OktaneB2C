"""Step-up authentication for releasing an approval.

The resume link a shopper receives is deliberately *not* an authorization. It
names an approval and nothing more. Releasing the order requires all of:

1. a completed ``/authorize`` round trip with PKCE;
2. a ``state`` bound server-side to this approval, usable exactly once;
3. the ID token's ``sub`` equal to the approval's subject — a forwarded link
   signed in as someone else must fail;
4. a matching ``nonce``;
5. ``acr`` equal to the required value, **failing closed when absent** — Okta
   silently ignores unrecognised ``acr_values``, so a typo in the request would
   otherwise look like a success;
6. ``auth_time`` both later than the approval and within a freshness window —
   this is what ``max_age=0`` buys, and it is why ``acr`` alone is not enough;
7. the single-use resume code marked consumed.

Getting any one of these wrong turns the whole human-in-the-loop story into
theatre, so each is checked explicitly and reported by name.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from ..config import settings
from .store import Approval, ApprovalConflict, store


class StepUpError(RuntimeError):
    def __init__(self, code: str, detail: str, approval_id: str | None = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.approval_id = approval_id


@dataclass
class VerifiedStepUp:
    subject: str
    acr: str
    auth_time: int
    id_token: str
    claims: dict[str, Any]


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


def callback_url() -> str:
    return f"{settings.public_base}/auth/stepup/callback"


def start(approval_id: str, code: str) -> str:
    """Consume the resume code and return the URL to send the shopper to.

    The code is consumed here rather than at the callback so a leaked link
    cannot be used twice even if the first attempt is abandoned.
    """
    approval = store.get(approval_id)
    if approval is None:
        raise StepUpError("unknown_approval", approval_id)

    try:
        store.consume_code(approval_id, code)
        store.transition(approval_id, {"NOTIFIED", "STEPUP_FAILED"}, "STEPUP_STARTED")
    except ApprovalConflict as exc:
        raise StepUpError("invalid_resume_link", str(exc)) from exc

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    store.bind_state(state, approval_id, verifier, nonce)

    params = {
        "client_id": settings.agent_client_id,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": callback_url(),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Force a fresh second factor rather than accepting the existing session.
        "acr_values": settings.required_acr,
        "max_age": "0",
        "prompt": "login",
    }

    return f"{settings.user_authorize_url}?{urlencode(params)}"


async def _redeem(auth_code: str, verifier: str) -> str:
    """Trade the authorization code for an ID token.

    The agent has no client secret — it authenticates with the same
    ``private_key_jwt`` assertion it uses on both legs of the exchange, so this
    redemption proves possession of the agent's key as well as of the PKCE
    verifier.
    """
    from ..tokens.agent_key import agent_key

    token_url = settings.user_token_url
    body = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": callback_url(),
        "client_id": settings.agent_client_id,
        "code_verifier": verifier,
        "client_assertion_type": (
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ),
        "client_assertion": agent_key().client_assertion(token_url),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(token_url, data=body)
    payload = response.json()
    if "id_token" not in payload:
        raise StepUpError(
            payload.get("error", "token_error"),
            payload.get("error_description", str(payload)),
        )
    return str(payload["id_token"])


def _decode(id_token: str) -> dict[str, Any]:
    if settings.mock:
        from ..tokens.mock_as import read_user_id_token

        return read_user_id_token(id_token)
    # Real Okta: verified against the org JWKS in Phase 6.
    from ..security.jwt import verify_id_token

    return verify_id_token(id_token)


async def complete(
    state: str, auth_code: str | None, error: str | None, error_description: str | None
) -> tuple[Approval, VerifiedStepUp]:
    """Run every check above, then mark the approval step-up-verified."""
    bound = store.take_state(state)
    if bound is None:
        raise StepUpError("invalid_state", "state is unknown, expired, or already used")
    approval_id, verifier, nonce = bound

    approval = store.get(approval_id)
    if approval is None:
        raise StepUpError("unknown_approval", approval_id)

    def fail(code: str, detail: str) -> StepUpError:
        try:
            store.transition(
                approval_id, "STEPUP_STARTED", "STEPUP_FAILED", failure=f"{code}: {detail}"
            )
        except ApprovalConflict:
            pass
        return StepUpError(code, detail, approval_id)

    if error:
        # Okta returns this when the requested assurance cannot be met. It is a
        # retry, not a crash.
        raise fail(error, error_description or "authorization request was refused")
    if not auth_code:
        raise fail("missing_code", "no authorization code on the callback")

    try:
        id_token = await _redeem(auth_code, verifier)
        claims = _decode(id_token)
    except StepUpError as exc:
        raise fail(exc.code, exc.detail) from exc
    except jwt.PyJWTError as exc:
        raise fail("invalid_id_token", str(exc)) from exc

    if claims.get("nonce") != nonce:
        raise fail("nonce_mismatch", "the ID token was not minted for this request")

    if str(claims.get("sub")) != approval.subject:
        raise fail(
            "subject_mismatch",
            "this approval belongs to a different shopper — a forwarded link cannot approve it",
        )

    acr = claims.get("acr")
    if not acr:
        raise fail(
            "acr_absent",
            "no acr claim on the token; unrecognised acr_values are ignored silently, "
            "so an absent claim is treated as failure",
        )
    if acr != settings.required_acr:
        raise fail("acr_insufficient", f"acr={acr}, required {settings.required_acr}")

    auth_time = claims.get("auth_time")
    if not isinstance(auth_time, int):
        raise fail("auth_time_absent", "no auth_time claim to prove freshness")
    if auth_time < int(approval.created_at):
        raise fail(
            "auth_time_stale",
            "authentication predates the approval request, so it cannot be consent for it",
        )
    age = int(time.time()) - auth_time
    if age > settings.stepup_freshness_seconds:
        raise fail(
            "auth_time_expired",
            f"authentication is {age}s old, limit is {settings.stepup_freshness_seconds}s",
        )

    verified = VerifiedStepUp(
        subject=str(claims["sub"]),
        acr=str(acr),
        auth_time=auth_time,
        id_token=id_token,
        claims=claims,
    )
    store.transition(
        approval_id,
        "STEPUP_STARTED",
        "STEPUP_VERIFIED",
        verified_acr=verified.acr,
        verified_auth_time=verified.auth_time,
        failure=None,
    )
    return approval, verified
