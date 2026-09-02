"""ID token verification against a real Okta org.

This is the ``else settings.mock`` branch used by ``approvals/stepup.py`` when
the demo is pointed at a real authorization server. In mock mode this module
is not imported at all — the import lives inside a function body, so an
un-configured Okta domain never breaks startup.

What is verified:

- signature, against the org's JWKS at ``https://{okta_domain}/oauth2/v1/keys``
- ``iss`` must start with ``https://{okta_domain}`` (Okta issues under
  ``/oauth2/default`` or a custom authorization-server path — a prefix match
  covers both without hard-coding the AS)
- ``aud`` must equal the agent's client id (the step-up flow requests tokens
  minted for this agent as the audience)
- ``exp`` and ``nbf`` with 5-second clock skew tolerance

The higher-level check for ``acr``, ``auth_time``, ``nonce``, and ``sub``
identity lives in ``approvals/stepup.py`` and runs on top of the claims this
function returns.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from ..config import settings

# jwt.PyJWKClient caches JWKS internally (default 1 hour) but reinstantiating
# it every call would defeat that cache. Keep one per issuer.
_lock = threading.Lock()
_jwks_clients: dict[str, PyJWKClient] = {}
_cache_ttl_seconds = 10 * 60


class JwtVerificationError(RuntimeError):
    """Something about the ID token did not check out — signature, iss, aud, exp, nbf."""


def _jwks_client(jwks_uri: str) -> PyJWKClient:
    with _lock:
        client = _jwks_clients.get(jwks_uri)
        if client is None:
            client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=_cache_ttl_seconds)
            _jwks_clients[jwks_uri] = client
        return client


def verify_id_token(token: str) -> dict[str, Any]:
    """Verify an ID token minted by the configured Okta org and return its claims.

    Raises JwtVerificationError with a short reason on any failure.
    """
    domain = settings.okta_domain
    if not domain:
        raise JwtVerificationError(
            "OKTA_DOMAIN is not configured — cannot verify against a real Okta org "
            "while DEMO_MODE=okta. Set OKTA_DOMAIN on the agent service."
        )

    jwks_uri = f"https://{domain}/oauth2/v1/keys"
    issuer_prefix = f"https://{domain}"

    try:
        signing_key = _jwks_client(jwks_uri).get_signing_key_from_jwt(token)
    except (httpx.HTTPError, jwt.PyJWKClientError) as exc:
        raise JwtVerificationError(f"could not fetch signing key: {exc}") from exc

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.agent_client_id,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=5,
        )
    except jwt.PyJWTError as exc:
        raise JwtVerificationError(f"invalid id token: {exc}") from exc

    iss = str(claims.get("iss", ""))
    if not iss.startswith(issuer_prefix):
        raise JwtVerificationError(
            f"issuer {iss!r} does not belong to configured Okta domain {domain!r}"
        )

    # PyJWT already checked exp with leeway; belt-and-suspenders on nbf for
    # clocks that skew forwards.
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and time.time() + 5 < nbf:
        raise JwtVerificationError("token is not yet valid (nbf in the future)")

    return dict(claims)
