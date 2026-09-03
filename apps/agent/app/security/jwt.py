"""Verify shopper ID tokens against the org's published keys.

The step-up gate reads its ``acr`` and ``auth_time`` proof out of an ID token, so
that token's signature is the only thing standing between "the shopper completed
MFA" and "someone handed us a JSON blob". Decoding without verifying would make
the whole human-in-the-loop story forgeable by anyone who can reach the callback.
"""

from __future__ import annotations

from typing import Any

import jwt
from jwt import PyJWKClient

from ..config import settings

# One client per JWKS URL. PyJWKClient caches the fetched keys and re-fetches on
# an unknown kid, which is what makes Okta's key rotation a non-event.
_clients: dict[str, PyJWKClient] = {}


def _client(keys_url: str) -> PyJWKClient:
    if keys_url not in _clients:
        _clients[keys_url] = PyJWKClient(keys_url, cache_keys=True)
    return _clients[keys_url]


def verify_id_token(id_token: str) -> dict[str, Any]:
    """Return the claims of a shopper ID token, or raise ``jwt.PyJWTError``.

    Audience is the storefront, not the agent: the shopper signs into the store,
    and a token minted for some other client must not be accepted here.
    """
    audience = settings.storefront_client_id
    if not audience:
        raise jwt.InvalidAudienceError(
            "OKTA_STOREFRONT_CLIENT_ID is unset, so no audience can be enforced"
        )
    signing_key = _client(settings.user_keys_url).get_signing_key_from_jwt(id_token)
    return jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=settings.user_token_issuer,
        options={"require": ["exp", "iat", "sub", "aud", "iss"]},
    )
