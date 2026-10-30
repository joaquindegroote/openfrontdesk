from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Service(BaseModel):
    id: str
    name: str
    description: str
    duration_minutes: int
    price: int
    deposit: int


class Practitioner(BaseModel):
    id: str
    name: str
    title: str
    service_ids: list[str]


class Patient(BaseModel):
    id: str
    name: str
    phone: str
    email: str | None = None
    created_at: datetime


class AppointmentStatus(StrEnum):
    CONFIRMED = "confirmed"
    PENDING_DEPOSIT = "pending_deposit"
    CANCELLED = "cancelled"


class Appointment(BaseModel):
    id: str
    patient_id: str
    service_id: str
    practitioner_id: str
    start: datetime
    end: datetime
    status: AppointmentStatus
    price: int
    deposit_due: int
    payment_url: str | None = None
    created_at: datetime
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None
    cancellation_fee: int = 0


class Slot(BaseModel):
    start: datetime
    end: datetime
    practitioner_id: str
    practitioner_name: str


class CancellationQuote(BaseModel):
    appointment_id: str
    fee: int
    late: bool
    free_cancellation_until: datetime


class CancellationResult(BaseModel):
    appointment: Appointment
    fee_charged: int


class PatientCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str
    email: str | None = Field(default=None, max_length=254)


class VerificationRequest(BaseModel):
    phone: str


class VerificationCheck(BaseModel):
    phone: str
    code: str = Field(min_length=4, max_length=10)


class AppointmentCreate(BaseModel):
    patient_id: str
    service_id: str
    start: datetime
    practitioner_id: str | None = None


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class RescheduleRequest(BaseModel):
    start: datetime
    practitioner_id: str | None = None
