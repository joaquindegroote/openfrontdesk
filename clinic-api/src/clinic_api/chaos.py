"""Fault injection: develop the agent against a backend that misbehaves like real ones do."""

import asyncio
import random
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

EXEMPT_PATHS = {"/healthz", "/docs", "/openapi.json"}


@dataclass(frozen=True)
class ChaosConfig:
    error_rate: float = 0.0
    latency_ms: int = 0
    seed: int | None = None


class ChaosMiddleware(BaseHTTPMiddleware):
    """Adds latency and random 503s. Send `X-Chaos-Fail: 1` to force a failure."""

    def __init__(self, app: ASGIApp, config: ChaosConfig) -> None:
        super().__init__(app)
        self.config = config
        self._rng = random.Random(config.seed)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        if self.config.latency_ms:
            await asyncio.sleep(self.config.latency_ms / 1000)
        forced = request.headers.get("x-chaos-fail") == "1"
        if forced or self._rng.random() < self.config.error_rate:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "service_unavailable",
                        "message": "The booking system is temporarily unavailable.",
                    }
                },
            )
        return await call_next(request)
