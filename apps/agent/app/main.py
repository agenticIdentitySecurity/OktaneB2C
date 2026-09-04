"""Oktane B2C agent service.

Owns the shopper's identity, the token exchanges, the intent/approval state
machine, and step-up verification. It deliberately owns no data access: every
read and write goes through the MCP server with a scoped token.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import mcp_client
from .config import settings
from .routers import approvals, auth, chat, demo, mock_as

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("oktane.agent")

app = FastAPI(title="Oktane B2C Agent", version="1.0.0")

# The storefront talks to this service through its own server-side route
# handlers, so the browser origin only needs to be allowed for the approve page.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_base],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(auth.router)
app.include_router(approvals.router)
app.include_router(demo.router)
if settings.mock:
    app.include_router(mock_as.router)


@app.get("/healthz")
def healthz() -> dict[str, object]:
    return {
        "ok": True,
        "demo_mode": settings.demo_mode,
        "token_exchange_impl": "mock" if settings.mock else settings.token_exchange_impl,
        "mcp_url": settings.mcp_url,
        "catalog_issuer": settings.catalog.issuer,
        "orders_issuer": settings.orders.issuer,
        "required_acr": settings.required_acr,
    }


@app.get("/warm")
async def warm() -> dict[str, object]:
    """Wake this service and the MCP server.

    Called by the storefront on load so the free-tier containers are hot by
    the time the shopper clicks anything. Always returns 200 — the whole
    point is that a slow underlying service should not surface as an error.
    """
    mcp_ok = await mcp_client.warm()
    return {"agent": True, "mcp": mcp_ok}


@app.get("/agent/whoami")
def whoami() -> dict[str, object]:
    """Static identity of this agent — surfaced in the security trace header.

    Everything here is public: the workload principal id doubles as the OIDC
    client id, and the two custom authorization servers' issuers are advertised
    on the MCP server too. No secrets, no tokens.
    """
    return {
        "agent_client_id": settings.agent_client_id,
        "workload_principal_id": settings.agent_client_id,
        "demo_mode": settings.demo_mode,
        "token_exchange_impl": "mock" if settings.mock else settings.token_exchange_impl,
        "catalog": {
            "issuer": settings.catalog.issuer,
            "audience": settings.catalog.audience,
            "scopes": list(settings.catalog.scopes),
        },
        "orders": {
            "issuer": settings.orders.issuer,
            "audience": settings.orders.audience,
            "scopes": list(settings.orders.scopes),
        },
    }


@app.on_event("startup")
def announce() -> None:
    log.info("agent up  DEMO_MODE=%s  mcp=%s", settings.demo_mode, settings.mcp_url)
    if settings.mock:
        log.info("mock authorization servers:")
        log.info("  catalog %s", settings.catalog.issuer)
        log.info("  orders  %s", settings.orders.issuer)
        log.info("set MCP_REQUIRE_AUTH=true on the MCP server to enforce these for real")
