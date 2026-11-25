"""An isolated world per eval run: a fresh clinic and a fresh engine, wired in process."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from clinic_api.catalog import TIMEZONE
from clinic_api.chaos import ChaosConfig
from clinic_api.clock import FixedClock, SystemClock
from clinic_api.main import create_app as create_clinic_app
from clinic_api.models import AppointmentCreate, PatientCreate
from clinic_api.store import ClinicStore
from fastapi import FastAPI
from openfrontdesk.agent.executor import GuardMode
from openfrontdesk.backends.clinic_http import ClinicHttpBackend
from openfrontdesk.llm import LLMClient
from openfrontdesk.service import ConversationService
from openfrontdesk.settings import Settings
from openfrontdesk.wiring import open_conversation_service

# Every code the eval clinic "texts". Personas that verify a phone know it as a fact.
VERIFICATION_CODE = "246810"


@dataclass
class EvalEnvironment:
    service: ConversationService
    clinic: FastAPI
    # Set when the run has its own time, which the clinic and the engine both read.
    clock: FixedClock | None = None

    def time_passes(self, until: datetime | None = None) -> None:
        """Between a reply and the customer's next message: a minute, or up to `until`. With
        time standing still, every turn's prompt says the same minute, and the suite reads
        from the model's prompt cache what a real conversation can't."""
        if self.clock:
            self.clock.at = until or self.clock.at + timedelta(minutes=1)

    def someone_books(self, service_id: str, starting_from: datetime, until: datetime) -> int:
        """Other patients book every open time of a service that starts in [starting_from,
        until), with any practitioner. Returns how many."""
        store = self.store
        day = starting_from.astimezone(TIMEZONE).date()
        booked = 0
        while True:
            open_times = [
                slot
                for slot in store.availability(service_id, day, day, limit=10_000)
                if starting_from <= slot.start < until
            ]
            if not open_times:
                return booked
            booked += 1
            # One patient each: nobody can be in two chairs at once.
            other = store.create_patient(
                PatientCreate(name=f"Other Patient {booked}", phone=f"+1555018{booked:04d}")
            )
            store.book(
                AppointmentCreate(
                    patient_id=other.id,
                    service_id=service_id,
                    start=open_times[0].start,
                    practitioner_id=open_times[0].practitioner_id,
                )
            )

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
    guards: GuardMode = "full",
) -> AsyncIterator[EvalEnvironment]:
    """`now` sets the time for both the clinic and the engine, for reproducible runs. It only
    moves when the run says so."""
    clock = FixedClock(now) if now else None
    clinic = create_clinic_app(
        clock or SystemClock(TIMEZONE),
        chaos=chaos or ChaosConfig(),
        verification_code=VERIFICATION_CODE,
    )
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
            clock=clock.now if clock else None,
            guards=guards,
        ) as service:
            yield EvalEnvironment(service=service, clinic=clinic, clock=clock)
    finally:
        await backend.aclose()
