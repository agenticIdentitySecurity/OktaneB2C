"""Demo control plane: the restock trigger and telemetry reads.

The restock is the only scripted event in the demo. Everything downstream of it
— matching intents, raising approvals, notifying, step-up, the order — runs the
same code paths a real system would.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import mcp_client, telemetry
from ..approvals.notifier import get_notifier
from ..approvals.store import ApprovalConflict, store
from ..config import settings
from ..tokens.base import TraceEvent

log = logging.getLogger("oktane.demo")
router = APIRouter(tags=["demo"])


class RestockRequest(BaseModel):
    sku: str = "CE-BB-GAME-7"
    stock: int = 12


@router.post("/demo/restock")
async def restock(body: RestockRequest) -> dict[str, Any]:
    """Beat 6: stock returns, standing intents wake up, approvals are raised."""
    result = await mcp_client.demo_restock(body.sku, body.stock)

    raised: list[dict[str, Any]] = []
    for intent in store.pending_for_sku(body.sku):
        try:
            approval, code = store.raise_approval(intent)
        except ApprovalConflict as exc:
            log.warning("could not raise approval for %s: %s", intent.intent_id, exc)
            continue

        telemetry.record(
            approval.approval_id,
            TraceEvent(
                kind="note",
                label="Restock matched a standing intent",
                detail=f"{body.sku} {result['before']} -> {result['after']}",
                claims={"intent": intent.intent_id, "approval": approval.approval_id},
            ),
        )

        resume_url = f"{settings.public_base}/auth/stepup/start?" + urlencode(
            {"approval_id": approval.approval_id, "code": code}
        )
        summary = (
            f"{intent.product_name} — {intent.variant_label} "
            f"for ${intent.max_total_cents / 100:.2f}"
        )
        get_notifier().notify(approval, resume_url, summary)

        try:
            store.transition(approval.approval_id, "REQUESTED", "NOTIFIED")
        except ApprovalConflict as exc:
            log.warning("notify transition failed: %s", exc)

        telemetry.record(
            approval.approval_id,
            TraceEvent(
                kind="note",
                label="Notification sent",
                detail=summary,
                claims={
                    "channel": get_notifier().name,
                    "ttl_seconds": settings.approval_ttl_seconds,
                    "resume_url": resume_url,
                    "note": "This link names an approval — step-up still gates the order.",
                },
            ),
        )

        raised.append(
            {
                "approval_id": approval.approval_id,
                "intent_id": intent.intent_id,
                "summary": summary,
                # The link is surfaced because there is no real inbox in a demo.
                # It is not an authorization: step-up still gates the order.
                "resume_url": resume_url,
            }
        )

    return {"restock": result, "approvals_raised": raised}


@router.get("/demo/catalog")
async def catalog() -> dict[str, Any]:
    """Public product listing, proxied so the storefront has one origin to talk to."""
    return await mcp_client.public_catalog()


@router.get("/demo/state")
def state(subject: str | None = None) -> dict[str, Any]:
    intents = store.intents_for(subject) if subject else []
    return {
        "demo_mode": settings.demo_mode,
        "token_exchange_impl": settings.token_exchange_impl,
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
        "agent_client_id": settings.agent_client_id,
        "intents": [i.public() for i in intents],
    }


@router.get("/telemetry/{approval_id}")
def approval_trace(approval_id: str) -> dict[str, Any]:
    if store.get(approval_id) is None:
        raise HTTPException(404, "unknown approval")
    return {"trace": telemetry.for_approval(approval_id)}


@router.get("/telemetry")
def recent_trace() -> dict[str, Any]:
    return {"trace": telemetry.recent()}


class ScopeProbe(BaseModel):
    id_token: str
    tool: str = "orders.create"


@router.post("/demo/scope-probe")
async def scope_probe(body: ScopeProbe) -> dict[str, Any]:
    """Try to spend money with tokens the agent is not entitled to use.

    Two separate protections, provoked one at a time, because they fail for
    different reasons and a viewer should see both:

    - **insufficient_scope** — a genuine token from the *right* authorization
      server, carrying ``orders:read``. Same audience, wrong permission.
    - **wrong_audience** — a genuine token from the *catalog* authorization
      server. Right shape, wrong issuer, so the orders resource server will not
      even recognise its signing key.

    Neither refusal involves asking the agent nicely. The resource server decides.
    """
    from ..routers.chat import _identity

    sub, _, _ = _identity(body.id_token)
    probes: list[dict[str, Any]] = []

    for label, scope, expected in (
        ("wrong scope, right audience", "orders:read", "insufficient_scope"),
        ("wrong audience entirely", "inventory:read", "wrong_audience"),
    ):
        trace: list[TraceEvent] = []
        try:
            await mcp_client.call_tool(
                body.tool,
                {"variant_sku": "CE-BB-GAME-7", "qty": 1},
                id_token=body.id_token,
                subject=sub,
                trace=trace,
                scope_override=scope,
            )
        except mcp_client.McpError as exc:
            reason = (exc.data or {}).get("reason") or exc.error
            probes.append(
                {
                    "label": label,
                    "presented_scope": scope,
                    "refused": True,
                    "reason": reason,
                    "as_expected": reason == expected,
                    "detail": (exc.data or {}).get("detail", exc.error),
                    "trace": [event.public() for event in trace],
                }
            )
            continue

        probes.append(
            {
                "label": label,
                "presented_scope": scope,
                "refused": False,
                "warning": "the call succeeded — scope enforcement is NOT active. "
                "Is MCP_REQUIRE_AUTH=true?",
                "trace": [event.public() for event in trace],
            }
        )

    return {
        "tool": body.tool,
        "all_refused": all(p["refused"] for p in probes),
        "probes": probes,
    }
