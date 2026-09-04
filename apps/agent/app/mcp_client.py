"""JSON-RPC client for the MCP server.

The agent has no database credentials and no direct data access. Every read and
write goes through here, and every call carries an access token whose audience
and scope the MCP server verifies independently. If the wrong token is
presented, the call is refused — and the refusal is surfaced, not swallowed,
because watching it fail is the point.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx

from .config import settings
from .tokens.base import TraceEvent
from .tokens.factory import token_for

log = logging.getLogger("oktane.mcp_client")

# Render's free tier is unfriendly in two ways: containers suspend after idle
# (cold start observed at ~21 s, so budget at least 30 s here), and the front
# proxy imposes a burst rate limit that returns 429 when multiple tool calls
# fire in quick succession. Retrying absorbs both. Total worst case:
# _MAX_RETRIES * _BACKOFF_SECONDS = 15 * 2s = 30 s, comfortably over the cold
# start ceiling.
_TRANSIENT_STATUSES = frozenset({429, 502, 503, 504})
_MAX_RETRIES = 15
_BACKOFF_SECONDS = 2.0


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """Retry once or twice on Render's cold-start 5xx, then propagate.

    Only the transient statuses are retried. Any other error — a 400, a 500
    from the app itself, a network refusal — is passed through immediately so
    real failures are still surfaced.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.request(method, url, **kwargs)
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            last_exc = exc
            if attempt == _MAX_RETRIES - 1:
                raise
            log.warning(
                "mcp %s %s attempt %d/%d: %s — retrying in %.1fs",
                method, url, attempt + 1, _MAX_RETRIES, exc, _BACKOFF_SECONDS,
            )
            await asyncio.sleep(_BACKOFF_SECONDS)
            continue

        if response.status_code in _TRANSIENT_STATUSES and attempt < _MAX_RETRIES - 1:
            log.warning(
                "mcp %s %s attempt %d/%d: status %d — retrying in %.1fs",
                method, url, attempt + 1, _MAX_RETRIES, response.status_code, _BACKOFF_SECONDS,
            )
            await asyncio.sleep(_BACKOFF_SECONDS)
            continue

        return response

    # Loop only exits by returning; this is unreachable but keeps mypy happy.
    assert last_exc is not None
    raise last_exc

# Which scope each tool demands. This mirrors the MCP server's own map; the
# server is authoritative and re-checks everything.
TOOL_SCOPES: dict[str, str] = {
    "catalog.search": "catalog:read",
    "catalog.sizing_guide": "catalog:read",
    "inventory.check": "inventory:read",
    "orders.list": "orders:read",
    "orders.create": "orders:write",
}


class McpError(RuntimeError):
    def __init__(self, tool: str, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"{tool} failed: {message}")
        self.tool = tool
        self.code = code
        self.error = message
        self.data = data


async def call_tool(
    tool: str,
    params: dict[str, Any],
    *,
    id_token: str,
    subject: str,
    trace: list[TraceEvent],
    scope_override: str | None = None,
) -> dict[str, Any]:
    """Invoke one MCP tool, obtaining the right scoped token first.

    ``scope_override`` exists so the demo can deliberately present the wrong
    token and show the MCP server refusing the call.
    """
    scope = scope_override or TOOL_SCOPES.get(tool)
    if scope is None:
        raise McpError(tool, -32601, f"unknown tool {tool}")

    server = settings.server_for_scope(scope)
    exchange = await token_for(id_token, subject, server.audience, (scope,))
    # The shopper's ID token is the root of the whole chain and appears once per
    # turn, not once per exchange.
    already_rooted = any(event.kind == "user_token" for event in trace)
    trace.extend(
        event
        for event in exchange.trace
        if not (already_rooted and event.kind == "user_token")
    )

    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex[:8],
        "method": tool,
        "params": params,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await _request_with_retry(
            client,
            "POST",
            f"{settings.mcp_url}/mcp",
            json=payload,
            headers={"authorization": f"Bearer {exchange.access_token}"},
        )

    # After retries: if MCP is still returning a non-JSON error page (cold-start
    # 5xx, gateway 429), surface it as a structured McpError instead of blowing
    # up on response.json(). The retry helper already absorbs transient statuses
    # up to _MAX_RETRIES; anything left here is a real failure the caller needs
    # to see.
    if response.status_code >= 400:
        preview = response.text[:200].replace("\n", " ")
        log.warning(
            "MCP %s returned %d after retries: %s", tool, response.status_code, preview
        )
        raise McpError(
            tool,
            -32000,
            f"MCP unreachable (HTTP {response.status_code})",
            {"reason": "mcp_unreachable", "detail": preview, "status": response.status_code},
        )

    try:
        body = response.json()
    except ValueError as exc:
        preview = response.text[:200].replace("\n", " ")
        raise McpError(
            tool,
            -32603,
            "MCP returned non-JSON body",
            {"reason": "invalid_response", "detail": preview},
        ) from exc

    if "error" in body:
        err = body["error"]
        detail = err.get("data") or {}
        reason = detail.get("reason") or err.get("message")
        trace.append(
            TraceEvent(
                kind="mcp_denied",
                label=f"MCP {tool} refused",
                detail=f"{reason}: {detail.get('detail', '')}".strip(": "),
                ok=False,
                claims={
                    "required_scope": (detail.get("required") or {}).get("scope"),
                    "presented_scopes": detail.get("presented_scopes", []),
                },
            )
        )
        raise McpError(tool, int(err.get("code", -32603)), str(err.get("message")), detail)

    trace.append(
        TraceEvent(
            kind="mcp_call",
            label=f"MCP {tool} 200",
            detail=f"scope={scope} aud={server.audience}",
            claims={"tool": tool, "params": _redact(params)},
        )
    )
    return body.get("result", {})


def _redact(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if k != "idempotency_key"}


async def public_catalog() -> dict[str, Any]:
    """Unauthenticated product listing, used to render storefront tiles."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await _request_with_retry(client, "GET", f"{settings.mcp_url}/public/catalog")
        response.raise_for_status()
        return response.json()


async def demo_restock(sku: str, stock: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await _request_with_retry(
            client,
            "POST",
            f"{settings.mcp_url}/demo/restock",
            json={"sku": sku, "stock": stock},
        )
        response.raise_for_status()
        return response.json()


async def warm() -> bool:
    """Best-effort wake-up ping to the MCP server.

    Fired by the storefront when it loads so the MCP container is already
    warm by the time the shopper clicks Simulate restock. Returns True on
    success, False on any failure. Never raises.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await _request_with_retry(
                client, "GET", f"{settings.mcp_url}/healthz"
            )
            return response.status_code == 200
    except Exception as exc:  # noqa: BLE001 — this is a fire-and-forget helper
        log.info("mcp warm ping failed (non-fatal): %s", exc)
        return False
