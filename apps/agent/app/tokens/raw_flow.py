"""The real two-leg Cross App Access exchange, spoken over HTTP.

Ported from the reference implementation's ``examples/token_exchange.py`` and
adapted to this agent's async interface. The shape:

    shopper id_token
      -> leg 1: RFC 8693 token-exchange at the **org** authorization server
                -> ID-JAG, bound to one target authorization server
      -> leg 2: RFC 7523 jwt-bearer at that **custom** authorization server
                -> access token, one scope, one audience

Leg 1 deliberately does not go to the custom authorization server: only the org
AS can assert "this agent may act for this user against that resource". Leg 2
carries no ``scope`` parameter — the scope is already inside the assertion, which
is what makes the grant non-negotiable after the fact.

Both legs authenticate with ``private_key_jwt``. There is no client secret.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt

from ..config import AuthServer, settings
from .agent_key import agent_key
from .base import ExchangeResult, TokenExchangeError, TraceEvent

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
ID_JAG_TYPE = "urn:ietf:params:oauth:token-type:id-jag"

_TIMEOUT = httpx.Timeout(10.0)


def _claims(token: str) -> dict[str, Any]:
    """Read a token's claims for display and bookkeeping only.

    Signature verification is not this function's job. The MCP server verifies
    the access token against the issuer's JWKS before honouring it, and the
    authorization server verifies everything we send it — so nothing here is
    load-bearing for security.
    """
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return {}


def _scope_list(claims: dict[str, Any]) -> list[str]:
    raw = claims.get("scp", claims.get("scope", []))
    return raw.split() if isinstance(raw, str) else list(raw)


class RawIdJagExchanger:
    """Performs the exchange against whatever issuer configuration points at."""

    name = "raw"

    async def exchange(
        self, id_token: str, audience: str, scopes: tuple[str, ...]
    ) -> ExchangeResult:
        if len(scopes) != 1:
            # One scope per exchange is the whole point: a token that can read the
            # catalog must not also be able to spend money.
            raise TokenExchangeError(
                "request",
                "invalid_scope",
                f"request exactly one least-privilege scope per exchange, got {list(scopes)}",
            )
        scope = scopes[0]
        target = self._target_for(audience, scope)

        user = _claims(id_token)
        trace: list[TraceEvent] = [
            TraceEvent(
                kind="user_token",
                label="Shopper ID token",
                detail=f"sub={user.get('sub', '?')} iss={user.get('iss', '?')}",
                claims={
                    "sub": user.get("sub"),
                    "aud": user.get("aud"),
                    "iss": user.get("iss"),
                    "auth_time": user.get("auth_time"),
                    "acr": user.get("acr"),
                },
            )
        ]

        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            id_jag = await self._leg_one(http, id_token, target, scope)
            jag = _claims(id_jag)
            trace.append(
                TraceEvent(
                    kind="id_jag",
                    label=f"ID-JAG for {target.name}",
                    detail=f"aud={target.issuer} scope={scope}",
                    claims={
                        "iss": jag.get("iss"),
                        "sub": jag.get("sub"),
                        "aud": jag.get("aud"),
                        "scope": jag.get("scope", scope),
                        "token_type": jag.get("token_type", ID_JAG_TYPE),
                        "exp_in": int(jag["exp"] - time.time()) if jag.get("exp") else None,
                    },
                )
            )

            access_token, expires_in = await self._leg_two(http, id_jag, target)

        claims = _claims(access_token)
        granted = tuple(_scope_list(claims)) or scopes
        if scope not in granted:
            # The authorization server is entitled to narrow a grant. If it did,
            # the call we were about to make would fail at the MCP server anyway;
            # failing here names the real cause.
            raise TokenExchangeError(
                "access_token",
                "invalid_scope",
                f"asked {target.name} for {scope}, token carries {list(granted)}",
            )

        expires_at = float(claims.get("exp") or time.time() + expires_in)
        actor = str(claims.get("cid") or _actor(claims) or settings.agent_client_id)
        trace.append(
            TraceEvent(
                kind="access_token",
                label=f"Access token scp={' '.join(granted)}",
                detail=f"aud={target.audience}",
                claims={
                    "iss": claims.get("iss"),
                    "aud": claims.get("aud"),
                    "sub": claims.get("sub"),
                    "act.sub": actor,
                    "scp": list(granted),
                    "acr": claims.get("acr"),
                    "expires_in": int(expires_at - time.time()),
                },
            )
        )

        return ExchangeResult(
            access_token=access_token,
            audience=target.audience,
            scopes=granted,
            expires_at=expires_at,
            subject=str(claims.get("sub") or user.get("sub") or ""),
            actor=actor,
            issuer=str(claims.get("iss") or target.issuer),
            trace=trace,
        )

    @staticmethod
    def _target_for(audience: str, scope: str) -> AuthServer:
        for server in (settings.catalog, settings.orders):
            if server.audience == audience:
                if scope not in server.scopes:
                    raise TokenExchangeError(
                        "request", "invalid_scope", f"{server.name} does not grant {scope}"
                    )
                return server
        raise TokenExchangeError(
            "request", "invalid_target", f"no authorization server serves audience {audience}"
        )

    async def _leg_one(
        self, http: httpx.AsyncClient, id_token: str, target: AuthServer, scope: str
    ) -> str:
        url = settings.org_token_url
        response = await http.post(
            url,
            data={
                "grant_type": TOKEN_EXCHANGE_GRANT,
                "client_assertion_type": CLIENT_ASSERTION_TYPE,
                "client_assertion": agent_key().client_assertion(url),
                "subject_token": id_token,
                "subject_token_type": ID_TOKEN_TYPE,
                "requested_token_type": ID_JAG_TYPE,
                "scope": scope,
                "audience": target.issuer,
            },
        )
        body = self._ok(response, "id_jag")
        token = body.get("access_token")
        if not token:
            raise TokenExchangeError(
                "id_jag", "invalid_response", "the org authorization server returned no assertion"
            )
        return str(token)

    async def _leg_two(
        self, http: httpx.AsyncClient, id_jag: str, target: AuthServer
    ) -> tuple[str, int]:
        url = target.token_url
        response = await http.post(
            url,
            data={
                "grant_type": JWT_BEARER_GRANT,
                "client_assertion_type": CLIENT_ASSERTION_TYPE,
                "client_assertion": agent_key().client_assertion(url),
                "assertion": id_jag,
            },
        )
        body = self._ok(response, "access_token")
        token = body.get("access_token")
        if not token:
            raise TokenExchangeError(
                "access_token", "invalid_response", f"{target.name} returned no access token"
            )
        return str(token), int(body.get("expires_in") or 300)

    @staticmethod
    def _ok(response: httpx.Response, stage: str) -> dict[str, Any]:
        """Surface the authorization server's own words; they are the diagnosis."""
        if response.is_success:
            try:
                return dict(response.json())
            except ValueError as exc:
                raise TokenExchangeError(
                    stage, "invalid_response", f"non-JSON success body: {exc}"
                ) from exc
        try:
            body = response.json()
            error = str(body.get("error") or "exchange_failed")
            description = str(body.get("error_description") or "the request was denied")
        except ValueError:
            error = "exchange_failed"
            description = f"HTTP {response.status_code} with a non-JSON body"
        raise TokenExchangeError(stage, error, f"{description} (HTTP {response.status_code})")


def _actor(claims: dict[str, Any]) -> str | None:
    act = claims.get("act")
    return act.get("sub") if isinstance(act, dict) else None
