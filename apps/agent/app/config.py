"""Configuration for the Oktane B2C agent service.

`DEMO_MODE` is the switch that makes this repo demoable with no Okta org at all:

- ``mock`` — the agent runs a local authorization server, mints real RS256
  tokens, and serves a JWKS the MCP server verifies against. The full
  token-exchange shape and every scope check is exercised for real; only the
  issuer is local.
- ``okta`` — the same code paths point at a real Okta org.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

if sys.version_info < (3, 10):
    raise RuntimeError(
        f"The agent requires Python 3.10+ (okta-client and modern typing); "
        f"found {sys.version.split()[0]}. Recreate the venv with python3.13."
    )

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parent.parent.parent
load_dotenv(_HERE.parent / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name)
    return raw.lower() in {"1", "true", "yes", "on"} if raw else default


@dataclass(frozen=True)
class AuthServer:
    """One custom authorization server: an issuer, an audience, and its scopes."""

    name: str
    issuer: str
    audience: str
    scopes: tuple[str, ...]

    @property
    def token_url(self) -> str:
        """Leg 2 of the exchange posts here — the *custom* AS, not the org one."""
        return f"{self.issuer}/v1/token"

    @property
    def keys_url(self) -> str:
        return f"{self.issuer}/v1/keys"


@dataclass(frozen=True)
class Settings:
    demo_mode: str = field(default_factory=lambda: _env("DEMO_MODE", "mock").lower())
    host: str = field(default_factory=lambda: _env("AGENT_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("AGENT_PORT", "8788")))
    public_base: str = field(
        default_factory=lambda: _env("AGENT_PUBLIC_BASE", "http://localhost:8788")
    )
    web_base: str = field(default_factory=lambda: _env("WEB_BASE", "http://localhost:3000"))
    mcp_url: str = field(default_factory=lambda: _env("MCP_URL", "http://localhost:8787"))

    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(
        default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-opus-4-7")
    )

    okta_domain: str = field(default_factory=lambda: _env("OKTA_DOMAIN"))
    agent_client_id: str = field(
        default_factory=lambda: _env("OKTA_AGENT_CLIENT_ID", "wlp-oktane-demo-agent")
    )
    agent_private_key_jwk: str = field(
        default_factory=lambda: _env("OKTA_AGENT_PRIVATE_KEY_JWK")
    )
    agent_key_id: str = field(default_factory=lambda: _env("OKTA_AGENT_KEY_ID"))
    token_exchange_impl: str = field(
        default_factory=lambda: _env("TOKEN_EXCHANGE_IMPL", "mock").lower()
    )

    catalog_audience: str = field(
        default_factory=lambda: _env("OKTA_CATALOG_AUDIENCE", "api://oktane-catalog")
    )
    orders_audience: str = field(
        default_factory=lambda: _env("OKTA_ORDERS_AUDIENCE", "api://oktane-orders")
    )
    catalog_issuer_okta: str = field(default_factory=lambda: _env("OKTA_CATALOG_ISSUER"))
    orders_issuer_okta: str = field(default_factory=lambda: _env("OKTA_ORDERS_ISSUER"))

    approval_ttl_seconds: int = field(
        default_factory=lambda: int(_env("APPROVAL_TTL_SECONDS", "900"))
    )
    stepup_freshness_seconds: int = field(
        default_factory=lambda: int(_env("STEPUP_FRESHNESS_SECONDS", "120"))
    )
    required_acr: str = field(
        default_factory=lambda: _env("REQUIRED_ACR", "urn:okta:loa:2fa:any")
    )

    @property
    def mock(self) -> bool:
        return self.demo_mode == "mock"

    @property
    def org_issuer(self) -> str:
        """The **org** authorization server, which mints ID-JAGs.

        Leg 1 of Cross App Access goes here, not to the custom AS — the org AS is
        the only party that can assert "this agent may act for this user against
        that resource". Leg 2 then goes to the custom AS named in the assertion.
        """
        if self.mock:
            return f"{self.public_base}/mock-as/org"
        if not self.okta_domain.startswith("https://"):
            raise ValueError(
                f"OKTA_DOMAIN must be an https:// org URL when DEMO_MODE={self.demo_mode}; "
                f"got {self.okta_domain!r}"
            )
        return f"{self.okta_domain.rstrip('/')}/oauth2"

    @property
    def org_token_url(self) -> str:
        return f"{self.org_issuer}/v1/token"

    @property
    def catalog(self) -> AuthServer:
        return AuthServer(
            name="oktane-catalog",
            issuer=self.catalog_issuer_okta
            if not self.mock
            else f"{self.public_base}/mock-as/catalog",
            audience=self.catalog_audience,
            scopes=("catalog:read", "inventory:read"),
        )

    @property
    def orders(self) -> AuthServer:
        return AuthServer(
            name="oktane-orders",
            issuer=self.orders_issuer_okta
            if not self.mock
            else f"{self.public_base}/mock-as/orders",
            audience=self.orders_audience,
            scopes=("orders:read", "orders:write"),
        )

    def server_for_scope(self, scope: str) -> AuthServer:
        if scope in self.orders.scopes:
            return self.orders
        if scope in self.catalog.scopes:
            return self.catalog
        raise ValueError(f"no authorization server owns scope {scope!r}")


settings = Settings()
