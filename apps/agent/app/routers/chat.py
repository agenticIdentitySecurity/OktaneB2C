"""Chat and demo sign-in."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agent.graph import run_turn
from ..agent.llm import available as llm_available
from ..approvals.store import store
from ..config import settings
from ..tokens.mock_as import read_user_id_token

log = logging.getLogger("oktane.chat")
router = APIRouter(tags=["chat"])


class SignInRequest(BaseModel):
    email: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    id_token: str


def _identity(id_token: str) -> tuple[str, str, str]:
    """Resolve the shopper from their ID token. The agent never trusts a client-sent sub."""
    if settings.mock:
        claims = read_user_id_token(id_token)
    else:
        from ..security.jwt import verify_id_token

        claims = verify_id_token(id_token)
    return (
        str(claims["sub"]),
        str(claims.get("email", "")),
        str(claims.get("name", "Shopper")),
    )


@router.post("/auth/demo-signin")
def demo_signin(body: SignInRequest) -> dict[str, object]:
    """Mock-mode sign-in. Phase 3 replaces this with a real Okta redirect."""
    if not settings.mock:
        raise HTTPException(400, "demo sign-in is only available in mock mode")

    from .mock_as import SHOPPERS

    shopper = SHOPPERS.get(body.email.lower().strip())
    if shopper is None:
        raise HTTPException(404, f"unknown demo shopper {body.email}")

    from ..tokens.mock_as import mint_user_id_token

    return {
        "id_token": mint_user_id_token(
            shopper["sub"], shopper["email"], shopper["name"], acr="urn:okta:loa:1fa:pwd"
        ),
        "profile": shopper,
    }


@router.get("/auth/shoppers")
def shoppers() -> dict[str, object]:
    if not settings.mock:
        return {"mock": False, "shoppers": []}
    from .mock_as import SHOPPERS

    return {"mock": True, "shoppers": list(SHOPPERS.values())}


@router.post("/agent/chat")
async def chat(body: ChatRequest) -> dict[str, object]:
    try:
        sub, email, name = _identity(body.id_token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(401, f"invalid id_token: {exc}") from exc

    state = await run_turn(body.message, body.id_token, sub, email)

    intents = [
        i.public()
        for i in store.intents_for(sub)
        if i.state not in {"COMPLETED", "DENIED", "EXPIRED"}
    ]

    return {
        "reply": state.get("reply", ""),
        "kind": state.get("kind", "general"),
        "intent": state.get("intent"),
        "orders": state.get("orders", []),
        "pending_intents": intents,
        "trace": [event.public() for event in state.get("trace", [])],
        "llm": "anthropic" if llm_available() else "deterministic",
        "profile": {"sub": sub, "email": email, "name": name},
    }
