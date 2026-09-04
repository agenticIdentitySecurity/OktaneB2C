"""A local stand-in for Okta's authorization servers.

This is *not* a fake. It generates a real RSA keypair, mints real RS256 JWTs,
and publishes a real JWKS that the MCP server fetches and verifies. Every
audience check and scope check in the demo is genuinely enforced — the only
thing that is local is the issuer. That means all eight demo beats, including
the "wrong scope is rejected" money shot, run with zero Okta dependency.

The two-leg shape mirrors ID-JAG exactly: leg 1 turns the user's ID token into
an assertion bound to a target audience, leg 2 trades that assertion for an
access token. When ``TOKEN_EXCHANGE_IMPL=raw`` these same two legs are performed
against a real Okta org instead.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.utils import to_base64url_uint

from ..config import settings
from .base import ExchangeResult, TokenExchangeError, TraceEvent

_ID_JAG_TYPE = "urn:ietf:params:oauth:token-type:id-jag"

# The shopper's ID token is minted for the agent's own client id, matching what a
# real org enforces: leg 1 accepts only a subject token issued to the requesting
# client. Using a separate storefront audience here would let mock mode pass a
# chain that the real org rejects.
MOCK_USER_ISSUER = f"{settings.public_base}/mock-as/users"
MOCK_USER_AUDIENCE = settings.agent_client_id


class MockKeys:
    """One keypair per issuer, generated at startup and never persisted."""

    def __init__(self) -> None:
        self._by_issuer: dict[str, tuple[rsa.RSAPrivateKey, str]] = {}

    def _for(self, issuer: str) -> tuple[rsa.RSAPrivateKey, str]:
        if issuer not in self._by_issuer:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            kid = f"mock-{uuid.uuid4().hex[:12]}"
            self._by_issuer[issuer] = (key, kid)
        return self._by_issuer[issuer]

    def private(self, issuer: str) -> rsa.RSAPrivateKey:
        return self._for(issuer)[0]

    def kid(self, issuer: str) -> str:
        return self._for(issuer)[1]

    def jwks(self, issuer: str) -> dict[str, Any]:
        key, kid = self._for(issuer)
        numbers = key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "alg": "RS256",
                    "use": "sig",
                    "kid": kid,
                    "n": to_base64url_uint(numbers.n).decode(),
                    "e": to_base64url_uint(numbers.e).decode(),
                }
            ]
        }

    def sign(self, issuer: str, claims: dict[str, Any]) -> str:
        return jwt.encode(
            claims,
            self.private(issuer),
            algorithm="RS256",
            headers={"kid": self.kid(issuer)},
        )

    def verify(self, token: str, issuer: str, audience: str) -> dict[str, Any]:
        return jwt.decode(
            token,
            self.private(issuer).public_key(),
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            leeway=5,
        )


keys = MockKeys()


def mint_user_id_token(
    sub: str,
    email: str,
    name: str,
    *,
    acr: str | None = None,
    auth_time: int | None = None,
    nonce: str | None = None,
    lifetime: int = 3600,
) -> str:
    """Mint the shopper's ID token — the credential the whole chain hangs off."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": MOCK_USER_ISSUER,
        "aud": MOCK_USER_AUDIENCE,
        "sub": sub,
        "email": email,
        "name": name,
        "iat": now,
        "exp": now + lifetime,
        "auth_time": auth_time if auth_time is not None else now,
        "jti": uuid.uuid4().hex,
    }
    if acr:
        claims["acr"] = acr
    if nonce:
        claims["nonce"] = nonce
    return keys.sign(MOCK_USER_ISSUER, claims)


def read_user_id_token(id_token: str) -> dict[str, Any]:
    try:
        return keys.verify(id_token, MOCK_USER_ISSUER, MOCK_USER_AUDIENCE)
    except jwt.PyJWTError as exc:
        raise TokenExchangeError("verify_id_token", "invalid_grant", str(exc)) from exc


class MockTokenExchanger:
    """Performs the ID-JAG two-leg exchange against the local authorization servers."""

    name = "mock"

    async def exchange(
        self, id_token: str, audience: str, scopes: tuple[str, ...]
    ) -> ExchangeResult:
        user = read_user_id_token(id_token)
        target = (
            settings.orders if audience == settings.orders_audience else settings.catalog
        )
        for scope in scopes:
            if scope not in target.scopes:
                raise TokenExchangeError(
                    "id_jag",
                    "invalid_scope",
                    f"{target.name} does not grant {scope}",
                )

        now = int(time.time())
        trace: list[TraceEvent] = [
            TraceEvent(
                kind="user_token",
                label="Shopper ID token",
                detail=f"sub={user['sub']} iss={user['iss']}",
                claims={
                    "sub": user["sub"],
                    "aud": user["aud"],
                    "iss": user["iss"],
                    "iat": user.get("iat"),
                    "exp": user.get("exp"),
                    "auth_time": user.get("auth_time"),
                    "acr": user.get("acr"),
                },
            )
        ]

        # Leg 1 — RFC 8693 token exchange: ID token in, ID-JAG assertion out,
        # bound to exactly one target authorization server. The *org* server
        # issues it, which is why `iss` is not the agent.
        id_jag = keys.sign(
            settings.org_issuer,
            {
                "iss": settings.org_issuer,
                "sub": user["sub"],
                "aud": target.issuer,
                "client_id": settings.agent_client_id,
                "scope": " ".join(scopes),
                "iat": now,
                "exp": now + 60,
                "jti": uuid.uuid4().hex,
                "token_type": _ID_JAG_TYPE,
                **({"auth_time": user["auth_time"]} if user.get("auth_time") else {}),
                **({"acr": user["acr"]} if user.get("acr") else {}),
            },
        )
        trace.append(
            TraceEvent(
                kind="id_jag",
                label=f"ID-JAG for {target.name}",
                detail=f"aud={target.issuer} scope={' '.join(scopes)}",
                claims={
                    "iss": settings.org_issuer,
                    "sub": user["sub"],
                    "aud": target.issuer,
                    "scope": " ".join(scopes),
                    "token_type": _ID_JAG_TYPE,
                    "iat": now,
                    "exp": now + 60,
                },
            )
        )

        # Leg 2 — RFC 7523 jwt-bearer: the assertion buys a scoped access token.
        # `sub` stays the human; `act`/`cid` record the agent. That contrast is
        # the entire "on behalf of" story in one token.
        expires_at = now + 900
        access_token = keys.sign(
            target.issuer,
            {
                "iss": target.issuer,
                "aud": target.audience,
                "sub": user["sub"],
                "cid": settings.agent_client_id,
                "act": {"sub": settings.agent_client_id},
                "scp": list(scopes),
                "iat": now,
                "exp": expires_at,
                "jti": uuid.uuid4().hex,
                "auth_time": user.get("auth_time"),
                **({"acr": user["acr"]} if user.get("acr") else {}),
            },
        )
        trace.append(
            TraceEvent(
                kind="access_token",
                label=f"Access token scp={' '.join(scopes)}",
                detail=f"aud={target.audience}",
                claims={
                    "iss": target.issuer,
                    "aud": target.audience,
                    "sub": user["sub"],
                    "act.sub": settings.agent_client_id,
                    "scp": list(scopes),
                    "acr": user.get("acr"),
                    "iat": now,
                    "exp": expires_at,
                },
            )
        )

        return ExchangeResult(
            access_token=access_token,
            audience=target.audience,
            scopes=scopes,
            expires_at=expires_at,
            subject=str(user["sub"]),
            actor=settings.agent_client_id,
            issuer=target.issuer,
            trace=trace,
        )
