"""The contract between the agent and a business's booking system.

The engine never talks to a specific system directly: each integration (the mock clinic API,
Cal.com, Google Calendar, a practice-management system) implements BookingBackend.
"""

from datetime import date, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Service(_Model):
    id: str
    name: str
    description: str
    duration_minutes: int
    price: int
    deposit: int = 0


class Slot(_Model):
    start: datetime
    end: datetime
    practitioner_id: str
    practitioner_name: str


class Customer(_Model):
    id: str
    name: str
    phone: str
    email: str | None = None


class Appointment(_Model):
    id: str
    customer_id: str
    service_id: str
    practitioner_id: str
    start: datetime
    end: datetime
    status: str
    price: int
    deposit_due: int = 0
    payment_url: str | None = None
    cancellation_fee: int = 0


class CancellationQuote(_Model):
    appointment_id: str
    fee: int
    late: bool
    free_cancellation_until: datetime


class CancellationResult(_Model):
    appointment: Appointment
    fee_charged: int


class BackendError(Exception):
    """A failure reported by (or while reaching) the booking system."""

    def __init__(
        self, code: str, message: str, *, status: int | None = None, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable


class BookingBackend(Protocol):
    async def list_services(self) -> list[Service]: ...

    async def find_slots(
        self,
        service_id: str,
        date_from: date,
        date_to: date,
        practitioner_id: str | None = None,
    ) -> list[Slot]: ...

    async def find_customer(self, phone: str) -> Customer | None: ...

    async def create_customer(
        self, name: str, phone: str, email: str | None = None
    ) -> Customer: ...

    async def send_verification_code(self, phone: str) -> None:
        """Text a one-time code to the phone, to prove the customer holds it."""
        ...

    async def check_verification_code(self, phone: str, code: str) -> bool: ...

    async def list_appointments(self, customer_id: str) -> list[Appointment]: ...

    async def get_appointment(self, appointment_id: str) -> Appointment: ...

    async def book(
        self,
        customer_id: str,
        service_id: str,
        start: datetime,
        practitioner_id: str | None = None,
        *,
        idempotency_key: str,
    ) -> Appointment: ...

    async def cancellation_quote(self, appointment_id: str) -> CancellationQuote: ...

    async def cancel(
        self, appointment_id: str, reason: str | None, *, idempotency_key: str
    ) -> CancellationResult: ...

    async def reschedule(
        self,
        appointment_id: str,
        start: datetime,
        practitioner_id: str | None = None,
        *,
        idempotency_key: str,
    ) -> Appointment: ...

    async def aclose(self) -> None: ...
