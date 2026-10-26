"""HTTP API of the mock clinic.

Run with: uvicorn clinic_api.main:create_app --factory --port 8001
"""

import os
from datetime import date

from fastapi import FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from clinic_api import catalog
from clinic_api.chaos import ChaosConfig, ChaosMiddleware
from clinic_api.clock import Clock, SystemClock
from clinic_api.errors import ClinicError
from clinic_api.models import (
    Appointment,
    AppointmentCreate,
    CancellationQuote,
    CancellationResult,
    CancelRequest,
    Patient,
    PatientCreate,
    Practitioner,
    RescheduleRequest,
    Service,
    Slot,
)
from clinic_api.store import ClinicStore

REPLAY_HEADER = "Idempotent-Replayed"


def chaos_from_env() -> ChaosConfig:
    return ChaosConfig(
        error_rate=float(os.environ.get("CLINIC_CHAOS_ERROR_RATE", "0")),
        latency_ms=int(os.environ.get("CLINIC_CHAOS_LATENCY_MS", "0")),
    )


def create_app(
    clock: Clock | None = None,
    *,
    chaos: ChaosConfig | None = None,
    seed: int = 42,
    filler: bool = True,
) -> FastAPI:
    store = ClinicStore(clock or SystemClock(catalog.TIMEZONE), seed=seed, filler=filler)
    app = FastAPI(
        title="BrightSmile Dental API (mock)",
        version="0.1.0",
        description="Fictional practice-management system used as the agent's business backend.",
    )
    app.state.store = store
    app.add_middleware(ChaosMiddleware, config=chaos if chaos is not None else chaos_from_env())

    @app.exception_handler(ClinicError)
    async def clinic_error_handler(_: Request, exc: ClinicError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/services")
    def list_services() -> list[Service]:
        return list(store.services.values())

    @app.get("/practitioners")
    def list_practitioners() -> list[Practitioner]:
        return list(store.practitioners.values())

    @app.get("/availability")
    def availability(
        service_id: str,
        date_from: date,
        date_to: date,
        practitioner_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[Slot]:
        return store.availability(service_id, date_from, date_to, practitioner_id, limit)

    @app.get("/patients")
    def find_patients(phone: str) -> list[Patient]:
        return store.find_patients(phone)

    @app.post("/patients", status_code=201)
    def create_patient(body: PatientCreate) -> Patient:
        return store.create_patient(body)

    @app.get("/patients/{patient_id}")
    def get_patient(patient_id: str) -> Patient:
        return store.get_patient(patient_id)

    @app.get("/patients/{patient_id}/appointments")
    def patient_appointments(patient_id: str, include_past: bool = False) -> list[Appointment]:
        return store.patient_appointments(patient_id, include_past=include_past)

    @app.post("/appointments", status_code=201)
    def book(
        body: AppointmentCreate,
        response: Response,
        idempotency_key: str | None = Header(default=None),
    ) -> Appointment:
        appointment, replayed = store.idempotent(
            idempotency_key, ("book", body.model_dump_json()), lambda: store.book(body)
        )
        response.headers[REPLAY_HEADER] = str(replayed).lower()
        return appointment

    @app.get("/appointments/{appointment_id}")
    def get_appointment(appointment_id: str) -> Appointment:
        return store.get_appointment(appointment_id)

    @app.get("/appointments/{appointment_id}/cancellation-quote")
    def cancellation_quote(appointment_id: str) -> CancellationQuote:
        return store.cancellation_quote(appointment_id)

    @app.post("/appointments/{appointment_id}/cancel")
    def cancel(
        appointment_id: str,
        body: CancelRequest,
        response: Response,
        idempotency_key: str | None = Header(default=None),
    ) -> CancellationResult:
        result, replayed = store.idempotent(
            idempotency_key,
            ("cancel", appointment_id, body.model_dump_json()),
            lambda: store.cancel(appointment_id, body.reason),
        )
        response.headers[REPLAY_HEADER] = str(replayed).lower()
        return result

    @app.post("/appointments/{appointment_id}/reschedule")
    def reschedule(
        appointment_id: str,
        body: RescheduleRequest,
        response: Response,
        idempotency_key: str | None = Header(default=None),
    ) -> Appointment:
        appointment, replayed = store.idempotent(
            idempotency_key,
            ("reschedule", appointment_id, body.model_dump_json()),
            lambda: store.reschedule(appointment_id, body),
        )
        response.headers[REPLAY_HEADER] = str(replayed).lower()
        return appointment

    @app.post("/appointments/{appointment_id}/deposit-payment")
    def record_deposit_payment(appointment_id: str) -> Appointment:
        """Simulates the payment provider's webhook after the patient pays the deposit."""
        return store.record_deposit_payment(appointment_id)

    return app
