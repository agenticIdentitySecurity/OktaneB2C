"""ID token verification against a real Okta org.

This is the non-mock branch used by ``approvals/stepup.py`` when the demo is
pointed at a real authorization server. In mock mode this module is never
imported — the import lives inside a function body, so an un-configured Okta
domain cannot break startup.

The step-up gate reads its MFA proof (``acr``, ``auth_time``) out of an ID token,
so this signature check is the only thing standing between "the shopper completed
MFA" and "someone posted a JSON blob to the callback".

What is verified here:

- signature, against the org's published JWKS
- ``iss``, exactly. Okta's *org* authorization server serves its endpoints under
  ``/oauth2/v1/*`` but stamps tokens with the bare org URL, which is why the
  expected issuer comes from ``settings.user_token_issuer`` rather than being
  derived from the endpoint base.
- ``aud`` must equal the **agent's** client id. Okta refuses ID_TOKEN delegation
  links outright and only accepts a subject token minted for the requesting
  client, so the agent — not the storefront — is the relying party.
- ``exp``/``iat``/``nbf`` with a small clock-skew tolerance

The higher-level checks for ``acr``, ``auth_time`` freshness, ``nonce``, and
``sub`` binding live in ``approvals/stepup.py`` and run on the claims returned
here.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from ..config import settings

# PyJWKClient caches JWKS internally and re-fetches on an unknown kid, which is
# what makes Okta's key rotation a non-event. Reinstantiating it per call would
# defeat that cache, so keep one per JWKS URL.
_lock = threading.Lock()
_jwks_clients: dict[str, PyJWKClient] = {}
_cache_ttl_seconds = 10 * 60


class JwtVerificationError(RuntimeError):
    """An ID token did not check out — signature, iss, aud, exp or nbf."""


def _jwks_client(jwks_uri: str) -> PyJWKClient:
    with _lock:
        client = _jwks_clients.get(jwks_uri)
        if client is None:
            client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=_cache_ttl_seconds)
            _jwks_clients[jwks_uri] = client
        return client


def verify_id_token(token: str) -> dict[str, Any]:
    """Verify an ID token minted by the configured Okta org and return its claims.

    Raises ``JwtVerificationError`` with a short reason on any failure.
    """
    if not settings.okta_domain:
        raise JwtVerificationError(
            "OKTA_DOMAIN is not configured — cannot verify against a real Okta org "
            "outside mock mode. Set OKTA_DOMAIN on the agent service."
        )
    if not settings.agent_client_id:
        raise JwtVerificationError(
            "the agent client id is not configured, so no audience can be enforced"
        )

    try:
        signing_key = _jwks_client(settings.user_keys_url).get_signing_key_from_jwt(token)
    except (httpx.HTTPError, jwt.PyJWKClientError) as exc:
        raise JwtVerificationError(f"could not fetch signing key: {exc}") from exc

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.agent_client_id,
            issuer=settings.user_token_issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=5,
        )
    except jwt.PyJWTError as exc:
        raise JwtVerificationError(f"invalid id token: {exc}") from exc

    # PyJWT checks exp with leeway but not nbf; belt and braces for clocks that
    # skew forwards.
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and time.time() + 5 < nbf:
        raise JwtVerificationError("token is not yet valid (nbf in the future)")

    return dict(claims)
