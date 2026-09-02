"""Implementation selection and token caching.

Two exchanges plus an MCP hop per turn is enough latency to be visible in a
live demo, so tokens are cached per ``(subject, audience, scopes)``. The cache
is keyed on the subject, never shared across shoppers.
"""

from __future__ import annotations

import dataclasses
import time

from ..config import settings
from .base import ExchangeResult, TokenExchanger, TraceEvent
from .mock_as import MockTokenExchanger

_cache: dict[tuple[str, str, tuple[str, ...]], ExchangeResult] = {}


def get_exchanger() -> TokenExchanger:
    """Pick an implementation.

    ``DEMO_MODE`` and ``TOKEN_EXCHANGE_IMPL`` are orthogonal: the first decides
    *which issuer* (local or a real org), the second decides *how* the exchange is
    performed (in-process, over HTTP, or through the SDK). ``mock`` + ``raw`` is a
    deliberately supported pairing — it runs the real wire protocol, including
    ``private_key_jwt``, against the local authorization servers.
    """
    impl = settings.token_exchange_impl or ("mock" if settings.mock else "raw")
    if impl == "mock":
        return MockTokenExchanger()
    if impl == "raw":
        from .raw_flow import RawIdJagExchanger

        return RawIdJagExchanger()
    if impl == "sdk":
        from .sdk_flow import SdkExchanger

        return SdkExchanger()
    raise ValueError(f"unknown TOKEN_EXCHANGE_IMPL={impl!r}")


async def token_for(
    id_token: str, subject: str, audience: str, scopes: tuple[str, ...]
) -> ExchangeResult:
    key = (subject, audience, tuple(sorted(scopes)))
    cached = _cache.get(key)
    if cached and not cached.expired:
        # Report the reuse honestly as one event. Replaying the original chain
        # would make the drawer show exchanges that did not happen.
        return dataclasses.replace(
            cached,
            trace=[
                TraceEvent(
                    kind="access_token",
                    label=f"Access token reused scp={' '.join(scopes)}",
                    detail=f"aud={audience} (cached, {int(cached.expires_at - time.time())}s left)",
                    claims={
                        "aud": audience,
                        "sub": cached.subject,
                        "act.sub": cached.actor,
                        "scp": list(scopes),
                        "cached": True,
                    },
                )
            ],
        )

    result = await get_exchanger().exchange(id_token, audience, scopes)
    _cache[key] = result
    return result


def invalidate(subject: str) -> None:
    for key in [k for k in _cache if k[0] == subject]:
        _cache.pop(key, None)
