"""Local authorization-server endpoints used when DEMO_MODE=mock.

These exist so the entire demo — including real JWKS verification, real audience
isolation, and a real PKCE-protected step-up round trip — runs with no Okta org
configured. Phase 3 onwards points the same client code at a real org; nothing
in the agent's business logic knows the difference.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..config import AuthServer, settings
from ..tokens.agent_key import agent_key
from ..tokens.base import TokenExchangeError
from ..tokens.mock_as import MOCK_USER_ISSUER, keys, mint_user_id_token, read_user_id_token

# Imported rather than restated so the client and this server cannot drift apart
# on the wire constants — if they did, testing raw mode here would prove nothing.
from ..tokens.raw_flow import (
    CLIENT_ASSERTION_TYPE,
    ID_JAG_TYPE,
    ID_TOKEN_TYPE,
    JWT_BEARER_GRANT,
    TOKEN_EXCHANGE_GRANT,
)

router = APIRouter(prefix="/mock-as", tags=["mock-as"])

# Demo shoppers. Real Okta users replace these in Phase 3.
SHOPPERS: dict[str, dict[str, str]] = {
    "alex@oktane.demo": {
        "sub": "00u_alex_demo",
        "name": "Alex Rivera",
        "email": "alex@oktane.demo",
    },
    "sam@oktane.demo": {
        "sub": "00u_sam_demo",
        "name": "Sam Okonkwo",
        "email": "sam@oktane.demo",
    },
}

_codes: dict[str, dict[str, Any]] = {}


def _issuer_for(name: str) -> str:
    if name == "catalog":
        return settings.catalog.issuer
    if name == "orders":
        return settings.orders.issuer
    if name == "users":
        return MOCK_USER_ISSUER
    if name == "org":
        return settings.org_issuer
    raise HTTPException(404, f"unknown authorization server {name}")


@router.get("/{name}/v1/keys")
def jwks(name: str) -> JSONResponse:
    """The public keys the MCP server fetches to verify tokens for real."""
    return JSONResponse(keys.jwks(_issuer_for(name)))


@router.get("/{name}/.well-known/openid-configuration")
def discovery(name: str) -> JSONResponse:
    issuer = _issuer_for(name)
    return JSONResponse(
        {
            "issuer": issuer,
            "jwks_uri": f"{issuer}/v1/keys",
            "authorization_endpoint": f"{issuer}/v1/authorize",
            "token_endpoint": f"{issuer}/v1/token",
            "id_token_signing_alg_values_supported": ["RS256"],
        }
    )


@router.post("/users/v1/signin")
def signin(email: str = Form(...)) -> JSONResponse:
    """Demo sign-in: hand back the shopper's ID token. No password — mock mode only."""
    shopper = SHOPPERS.get(email.lower().strip())
    if shopper is None:
        raise HTTPException(404, f"unknown demo shopper {email}")
    id_token = mint_user_id_token(
        shopper["sub"], shopper["email"], shopper["name"], acr="urn:okta:loa:1fa:pwd"
    )
    return JSONResponse({"id_token": id_token, "profile": shopper})


_STEPUP_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Verify it's you</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
    font-family: Inter, system-ui, sans-serif; color:#fafafa;
    background: linear-gradient(to bottom, #0d0d14, #1a1a2e); }}
  .card {{ width:min(420px, 92vw); padding:32px; border-radius:16px;
    background:#16213e; border:1px solid #2a2a3e; box-shadow:0 24px 60px rgba(0,0,0,.45); }}
  .brand {{ display:flex; align-items:center; gap:10px; margin-bottom:24px; }}
  .dot {{ width:32px; height:32px; border-radius:9px; background:#007dc1;
    display:grid; place-items:center; font-weight:700; }}
  h1 {{ font-size:20px; margin:0 0 8px; }}
  p {{ color:#9ca3bb; font-size:14px; line-height:1.55; margin:0 0 20px; }}
  .req {{ font-family:'JetBrains Mono', monospace; font-size:12px; color:#a78bfa;
    background:#0d0d14; border:1px solid #2a2a3e; border-radius:8px;
    padding:10px 12px; margin-bottom:20px; word-break:break-all; }}
  button {{ width:100%; padding:13px; border:0; border-radius:10px; font-size:15px;
    font-weight:600; cursor:pointer; font-family:inherit; }}
  .primary {{ background:#ff6b35; color:#0d0d14; margin-bottom:10px; }}
  .ghost {{ background:transparent; color:#9ca3bb; border:1px solid #2a2a3e; }}
</style></head>
<body><div class="card">
  <div class="brand"><div class="dot">O</div><strong>Okta Verify</strong></div>
  <h1>Approve your purchase</h1>
  <p>Your shopping assistant is asking to place an order on your behalf.
     Confirm a second factor to release it.</p>
  <div class="req">acr_values={acr}<br>max_age=0</div>
  <form method="post" action="/mock-as/users/v1/authorize">
    {hidden}
    <button class="primary" name="decision" value="2fa" type="submit">
      Approve with second factor
    </button>
    <button class="ghost" name="decision" value="1fa" type="submit">
      Continue without second factor
    </button>
  </form>
</div></body></html>
"""


@router.get("/users/v1/authorize", response_class=HTMLResponse)
def authorize(request: Request) -> HTMLResponse:
    """Stand-in for Okta's hosted sign-in widget performing a step-up."""
    q = dict(request.query_params)
    for required in ("state", "nonce", "redirect_uri", "code_challenge"):
        if required not in q:
            raise HTTPException(400, f"missing {required}")

    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{_escape(v)}">' for k, v in q.items()
    )
    hidden += f'<input type="hidden" name="sub" value="{_escape(_only_shopper()["sub"])}">'
    return HTMLResponse(
        _STEPUP_PAGE.format(acr=_escape(q.get("acr_values", "(none)")), hidden=hidden)
    )


@router.post("/users/v1/authorize")
async def authorize_submit(request: Request) -> RedirectResponse:
    """Issue an authorization code carrying whatever assurance was actually met.

    The "continue without second factor" path is not a bug — it produces a token
    with a weaker ``acr``, which is exactly the negative case the verifier must
    reject. A demo that can only succeed proves nothing.
    """
    form = dict(await request.form())
    decision = str(form.get("decision", "2fa"))
    redirect_uri = str(form["redirect_uri"])
    state = str(form["state"])

    acr = settings.required_acr if decision == "2fa" else "urn:okta:loa:1fa:pwd"
    code = secrets.token_urlsafe(24)
    shopper = SHOPPERS.get(str(form.get("email", "")).lower()) or _only_shopper()

    _codes[code] = {
        "sub": shopper["sub"],
        "email": shopper["email"],
        "name": shopper["name"],
        "nonce": str(form["nonce"]),
        "code_challenge": str(form["code_challenge"]),
        "acr": acr,
        # max_age=0 was requested, so authentication happened just now.
        "auth_time": int(time.time()),
        "expires_at": time.time() + 120,
    }
    return RedirectResponse(
        f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", status_code=302
    )


@router.post("/users/v1/token")
async def token(request: Request) -> JSONResponse:
    """Redeem an authorization code, enforcing PKCE for real."""
    form = dict(await request.form())
    code = str(form.get("code", ""))
    verifier = str(form.get("code_verifier", ""))

    entry = _codes.pop(code, None)
    if entry is None or entry["expires_at"] < time.time():
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "code unknown or expired"},
            status_code=400,
        )

    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    if not secrets.compare_digest(challenge, entry["code_challenge"]):
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "PKCE verification failed"},
            status_code=400,
        )

    id_token = mint_user_id_token(
        entry["sub"],
        entry["email"],
        entry["name"],
        acr=entry["acr"],
        auth_time=entry["auth_time"],
        nonce=entry["nonce"],
    )
    return JSONResponse({"token_type": "Bearer", "expires_in": 3600, "id_token": id_token})


class _OAuthError(Exception):
    """An OAuth error body, so the client's verbatim-error path is exercised."""

    def __init__(self, error: str, description: str, status: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status

    def response(self) -> JSONResponse:
        return JSONResponse(
            {"error": self.error, "error_description": self.description},
            status_code=self.status,
        )


def _server_by(field: str, value: str) -> AuthServer:
    for server in (settings.catalog, settings.orders):
        if getattr(server, field) == value:
            return server
    raise _OAuthError("invalid_target", f"no authorization server with {field}={value!r}")


def _verify_client_assertion(form: dict[str, Any], token_url: str) -> None:
    """Authenticate the agent by signature. There is no client secret to leak.

    A real org looks the public key up from the agent's registration; here the
    agent and its registrar are the same process, so we read the public half
    directly. The check itself is identical either way.
    """
    if form.get("client_assertion_type") != CLIENT_ASSERTION_TYPE:
        raise _OAuthError(
            "invalid_client", "private_key_jwt is the only supported client authentication", 401
        )
    try:
        claims = jwt.decode(
            str(form.get("client_assertion", "")),
            agent_key().private_key.public_key(),
            algorithms=["RS256"],
            audience=token_url,
            options={"require": ["exp", "iss", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        # Includes the replay case: an assertion minted for the org token endpoint
        # fails the audience check here, and vice versa.
        raise _OAuthError("invalid_client", f"client assertion rejected: {exc}", 401) from exc

    if claims.get("iss") != claims.get("sub"):
        raise _OAuthError(
            "invalid_client", "client assertion iss and sub must both be the client id", 401
        )
    if claims.get("sub") != settings.agent_client_id:
        raise _OAuthError("invalid_client", f"unknown client {claims.get('sub')!r}", 401)


@router.post("/org/v1/token")
async def org_token(request: Request) -> JSONResponse:
    """Leg 1 — RFC 8693. The shopper's ID token buys an audience-bound ID-JAG.

    Only the org authorization server can issue this: it is the party that knows
    both the shopper and the agent, and its assertion is what lets a *different*
    authorization server trust the pairing.
    """
    form = dict(await request.form())
    try:
        _verify_client_assertion(form, f"{settings.org_issuer}/v1/token")

        if form.get("grant_type") != TOKEN_EXCHANGE_GRANT:
            raise _OAuthError(
                "unsupported_grant_type", f"expected {TOKEN_EXCHANGE_GRANT}"
            )
        if form.get("subject_token_type") != ID_TOKEN_TYPE:
            raise _OAuthError("invalid_request", f"subject_token_type must be {ID_TOKEN_TYPE}")
        if form.get("requested_token_type") != ID_JAG_TYPE:
            raise _OAuthError("invalid_request", f"requested_token_type must be {ID_JAG_TYPE}")

        try:
            user = read_user_id_token(str(form.get("subject_token", "")))
        except TokenExchangeError as exc:
            raise _OAuthError("invalid_grant", f"subject_token rejected: {exc.detail}") from exc

        target = _server_by("issuer", str(form.get("audience", "")))
        scope = str(form.get("scope", "")).strip()
        if len(scope.split()) != 1:
            raise _OAuthError("invalid_scope", "request exactly one scope per exchange")
        if scope not in target.scopes:
            raise _OAuthError("invalid_scope", f"{target.name} does not grant {scope}")
    except _OAuthError as exc:
        return exc.response()

    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": settings.org_issuer,
        "sub": user["sub"],
        "aud": target.issuer,
        "client_id": settings.agent_client_id,
        "scope": scope,
        "iat": now,
        "exp": now + 60,
        "jti": uuid.uuid4().hex,
        "token_type": ID_JAG_TYPE,
    }
    # The authentication context has to ride along: leg 2 never sees the ID token,
    # so without this the orders:write access token could not carry `acr` and the
    # step-up gate would have nothing to prove.
    if user.get("auth_time"):
        claims["auth_time"] = user["auth_time"]
    if user.get("acr"):
        claims["acr"] = user["acr"]

    return JSONResponse(
        {
            "access_token": keys.sign(settings.org_issuer, claims),
            "issued_token_type": ID_JAG_TYPE,
            "token_type": "N_A",
            "expires_in": 60,
            "scope": scope,
        }
    )


@router.post("/{name}/v1/token")
async def resource_token(name: str, request: Request) -> JSONResponse:
    """Leg 2 — RFC 7523. The ID-JAG buys one scoped access token.

    Declared after ``/users/v1/token`` on purpose: FastAPI matches in declaration
    order, so the PKCE code-redemption route above still wins for ``users``.
    """
    form = dict(await request.form())
    try:
        target = _server_by("name", f"oktane-{name}")
        _verify_client_assertion(form, target.token_url)

        if form.get("grant_type") != JWT_BEARER_GRANT:
            raise _OAuthError("unsupported_grant_type", f"expected {JWT_BEARER_GRANT}")

        try:
            jag = keys.verify(str(form.get("assertion", "")), settings.org_issuer, target.issuer)
        except jwt.PyJWTError as exc:
            # An assertion minted for the other authorization server dies here on
            # its audience — that is the isolation the two issuers buy.
            raise _OAuthError("invalid_grant", f"assertion rejected: {exc}") from exc

        if jag.get("token_type") != ID_JAG_TYPE:
            raise _OAuthError("invalid_grant", "assertion is not an ID-JAG")
        if jag.get("client_id") != settings.agent_client_id:
            raise _OAuthError("invalid_grant", "assertion was issued to a different client")

        scope = str(jag.get("scope", "")).strip()
        if scope not in target.scopes:
            raise _OAuthError("invalid_scope", f"{target.name} does not grant {scope}")
    except _OAuthError as exc:
        return exc.response()

    now = int(time.time())
    lifetime = 900
    access_token = keys.sign(
        target.issuer,
        {
            "iss": target.issuer,
            "aud": target.audience,
            "sub": jag["sub"],
            "cid": settings.agent_client_id,
            "act": {"sub": settings.agent_client_id},
            "scp": [scope],
            "iat": now,
            "exp": now + lifetime,
            "jti": uuid.uuid4().hex,
            **({"auth_time": jag["auth_time"]} if jag.get("auth_time") else {}),
            **({"acr": jag["acr"]} if jag.get("acr") else {}),
        },
    )
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": lifetime,
            "scope": scope,
        }
    )


def _only_shopper() -> dict[str, str]:
    return SHOPPERS["alex@oktane.demo"]


def _escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
