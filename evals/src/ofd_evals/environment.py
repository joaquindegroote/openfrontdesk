"""An isolated world per eval run: a fresh clinic and a fresh engine, wired in process."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

import httpx
from clinic_api.catalog import TIMEZONE
from clinic_api.chaos import ChaosConfig
from clinic_api.clock import FixedClock, SystemClock
from clinic_api.main import create_app as create_clinic_app
from clinic_api.store import ClinicStore
from fastapi import FastAPI
from openfrontdesk.backends.clinic_http import ClinicHttpBackend
from openfrontdesk.llm import LLMClient
from openfrontdesk.service import ConversationService
from openfrontdesk.settings import Settings
from openfrontdesk.wiring import open_conversation_service


@dataclass
class EvalEnvironment:
    service: ConversationService
    clinic: FastAPI

    @property
    def store(self) -> ClinicStore:
        """The clinic's state, for checking what actually happened."""
        return self.clinic.state.store


@asynccontextmanager
async def eval_environment(
    settings: Settings,
    *,
    llm: LLMClient | None = None,
    judge_llm: LLMClient | None = None,
    now: datetime | None = None,
    chaos: ChaosConfig | None = None,
) -> AsyncIterator[EvalEnvironment]:
    """`now` freezes the clock for both the clinic and the engine, for reproducible runs."""
    clinic_clock = FixedClock(now) if now else SystemClock(TIMEZONE)
    clinic = create_clinic_app(clinic_clock, chaos=chaos or ChaosConfig())
    backend = ClinicHttpBackend(
        httpx.AsyncClient(transport=httpx.ASGITransport(app=clinic), base_url="http://clinic")
    )
    isolated = settings.model_copy(update={"database_url": "sqlite+aiosqlite:///:memory:"})
    try:
        async with open_conversation_service(
            isolated,
            llm=llm,
            judge_llm=judge_llm,
            backend=backend,
            clock=(lambda: now) if now else None,
        ) as service:
            yield EvalEnvironment(service=service, clinic=clinic)
    finally:
        await backend.aclose()
