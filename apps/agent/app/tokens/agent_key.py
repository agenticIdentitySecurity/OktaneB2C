"""The agent's own credential — the private key behind ``private_key_jwt``.

Cross App Access authenticates the agent on both legs of the exchange with a
signed assertion rather than a shared secret, so this key *is* the agent's
identity. Only its public half is ever registered with Okta.
"""

from __future__ import annotations

import json
import time
import uuid
from functools import lru_cache
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from jwt.utils import to_base64url_uint

from ..config import settings

# A public JWK has kty/n/e but no "d"; requiring it catches the easy mistake of
# pasting the half that was meant for Terraform.
_REQUIRED_JWK_FIELDS = ("kty", "n", "e", "d")


class AgentKey:
    """An RSA signing key plus the ``kid`` the authorization server knows it by."""

    def __init__(self, private_key: Any, kid: str, *, ephemeral: bool) -> None:
        self.private_key = private_key
        self.kid = kid
        self.ephemeral = ephemeral

    def public_jwk(self) -> dict[str, Any]:
        numbers = self.private_key.public_key().public_numbers()
        return {
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "kid": self.kid,
            "n": to_base64url_uint(numbers.n).decode(),
            "e": to_base64url_uint(numbers.e).decode(),
        }

    def client_assertion(self, token_url: str) -> str:
        """Sign a 60-second assertion bound to one specific token endpoint.

        ``aud`` is the exact token URL, so an assertion intercepted on its way to
        the org authorization server cannot be replayed against a custom one.
        """
        now = int(time.time())
        return jwt.encode(
            {
                "iss": settings.agent_client_id,
                "sub": settings.agent_client_id,
                "aud": token_url,
                "iat": now,
                "exp": now + 60,
                "jti": uuid.uuid4().hex,
            },
            self.private_key,
            algorithm="RS256",
            headers={"kid": self.kid},
        )


def _load() -> AgentKey:
    raw = settings.agent_private_key_jwk
    if raw:
        try:
            jwk = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"OKTA_AGENT_PRIVATE_KEY_JWK is not valid JSON: {exc}"
            ) from exc

        missing = [f for f in _REQUIRED_JWK_FIELDS if not jwk.get(f)]
        if missing:
            raise ValueError(
                f"OKTA_AGENT_PRIVATE_KEY_JWK is missing {', '.join(missing)}. "
                "A key without 'd' is the public half — that one goes to Terraform."
            )

        kid = jwk.get("kid") or settings.agent_key_id
        if not kid:
            raise ValueError(
                "the agent JWK needs a 'kid', or set OKTA_AGENT_KEY_ID to the one "
                "registered with Okta — the authorization server selects the key by it"
            )
        if settings.agent_key_id and jwk.get("kid") and settings.agent_key_id != jwk["kid"]:
            raise ValueError(
                f"OKTA_AGENT_KEY_ID={settings.agent_key_id!r} does not match the JWK "
                f"kid={jwk['kid']!r}; Okta would fail to find the verification key"
            )
        return AgentKey(RSAAlgorithm.from_jwk(json.dumps(jwk)), str(kid), ephemeral=False)

    if not settings.mock:
        raise ValueError(
            f"OKTA_AGENT_PRIVATE_KEY_JWK is required when DEMO_MODE={settings.demo_mode}. "
            "Run `node scripts/gen-agent-key.mjs` and register the public half with Okta."
        )

    # Mock mode: the agent is also its own registrar, so an ephemeral key still
    # exercises private_key_jwt for real. Nothing outside this process trusts it.
    return AgentKey(
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        f"agent-{uuid.uuid4().hex[:12]}",
        ephemeral=True,
    )


@lru_cache(maxsize=1)
def agent_key() -> AgentKey:
    return _load()
