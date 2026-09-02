"""Demo control plane: the restock trigger and telemetry reads.

The restock is the only scripted event in the demo. Everything downstream of it
— matching intents, raising approvals, notifying, step-up, the order — runs the
same code paths a real system would.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import mcp_client, telemetry
from ..approvals.notifier import get_notifier
from ..approvals.store import ApprovalConflict, store
from ..config import settings
from ..tokens import raw_flow
from ..tokens.agent_key import agent_key
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
                label="Shopper notified out of band",
                detail="Approval is pending. The agent cannot proceed alone.",
                claims={"channel": get_notifier().name, "ttl_seconds": settings.approval_ttl_seconds},
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


class ForgetTokens(BaseModel):
    id_token: str


@router.post("/demo/forget-tokens")
def forget_tokens(body: ForgetTokens) -> dict[str, Any]:
    """Drop the shopper's cached access tokens so the next turn exchanges for real.

    The cache in ``tokens.factory`` exists to keep demo latency watchable. That
    makes it invisible whether a turn performed an exchange or reused one, which
    is exactly what the walkthrough needs to assert. This is the only way to
    observe a cold start without restarting the process.
    """
    from ..routers.chat import _identity
    from ..tokens import factory

    sub, _, _ = _identity(body.id_token)
    factory.invalidate(sub)
    return {"subject": sub, "cache": "cleared"}


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


class ExchangeProbe(BaseModel):
    id_token: str


@router.post("/demo/exchange-probe")
async def exchange_probe(body: ExchangeProbe) -> dict[str, Any]:
    """Attack the *authorization server* rather than the resource server.

    ``/demo/scope-probe`` proves the MCP server refuses a token it should not
    honour. This proves the tier above it: that the agent cannot talk an
    authorization server into minting a token it should never have issued.

    Four attacks, each defeated by a different mechanism:

    - **no client authentication** — there is no anonymous path to a token.
    - **client assertion replayed at another endpoint** — assertions are bound to
      one exact token URL, so capturing one buys nothing elsewhere.
    - **a scope the server does not own** — the catalog server cannot grant
      ``orders:write`` even if asked politely.
    - **an ID-JAG crossed between servers** — a genuine catalog assertion is
      refused by the orders server on its audience. This is the payoff for
      running two issuers instead of one.
    """
    if not settings.mock:
        raise HTTPException(
            400,
            "exchange-probe refuses to run against a real org: a burst of invalid "
            "client assertions can trip Okta's client lockout. Run it in DEMO_MODE=mock, "
            "where the wire protocol is identical.",
        )

    key = agent_key()
    org_url = settings.org_token_url
    catalog, orders = settings.catalog, settings.orders
    probes: list[dict[str, Any]] = []

    def record(label: str, expected: str, response: httpx.Response) -> None:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error = str(payload.get("error", ""))
        probes.append(
            {
                "label": label,
                "refused": not response.is_success,
                "status": response.status_code,
                "reason": error or ("minted a token" if response.is_success else "unknown"),
                "as_expected": error == expected,
                "detail": str(payload.get("error_description", ""))[:300],
            }
        )

    def leg_one(**overrides: str) -> dict[str, str]:
        form = {
            "grant_type": raw_flow.TOKEN_EXCHANGE_GRANT,
            "client_assertion_type": raw_flow.CLIENT_ASSERTION_TYPE,
            "client_assertion": key.client_assertion(org_url),
            "subject_token": body.id_token,
            "subject_token_type": raw_flow.ID_TOKEN_TYPE,
            "requested_token_type": raw_flow.ID_JAG_TYPE,
            "scope": "catalog:read",
            "audience": catalog.issuer,
        }
        form.update(overrides)
        return form

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http:
        unauthenticated = leg_one()
        unauthenticated.pop("client_assertion")
        record(
            "no client authentication",
            "invalid_client",
            await http.post(org_url, data=unauthenticated),
        )

        record(
            "client assertion minted for another endpoint",
            "invalid_client",
            # Correctly signed, wrong audience: this one is valid at the catalog
            # server's token endpoint, and only there.
            await http.post(
                org_url, data=leg_one(client_assertion=key.client_assertion(catalog.token_url))
            ),
        )

        record(
            "scope the catalog server does not own",
            "invalid_scope",
            await http.post(org_url, data=leg_one(scope="orders:write")),
        )

        # A genuine, fully valid catalog assertion — then presented to the orders
        # server, which is the one thing the two-issuer split exists to stop.
        crossing = "ID-JAG crossed to the other authorization server"
        genuine = await http.post(org_url, data=leg_one())
        if not genuine.is_success:
            probes.append(
                {
                    "label": crossing,
                    "refused": False,
                    "status": genuine.status_code,
                    "reason": "could not obtain a genuine assertion to misuse",
                    "as_expected": False,
                    "detail": genuine.text[:300],
                }
            )
        else:
            record(
                crossing,
                "invalid_grant",
                await http.post(
                    orders.token_url,
                    data={
                        "grant_type": raw_flow.JWT_BEARER_GRANT,
                        "client_assertion_type": raw_flow.CLIENT_ASSERTION_TYPE,
                        "client_assertion": key.client_assertion(orders.token_url),
                        "assertion": genuine.json()["access_token"],
                    },
                ),
            )

    return {
        "issuer": settings.org_issuer,
        "all_refused": all(p["refused"] for p in probes),
        "as_expected": all(p["as_expected"] for p in probes),
        "probes": probes,
    }
