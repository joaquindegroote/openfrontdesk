"""In-memory booking system with the rules a real practice-management system would enforce."""

import random
from collections.abc import Callable, Hashable
from datetime import date, datetime, timedelta
from itertools import count

from clinic_api import catalog
from clinic_api.clock import Clock
from clinic_api.errors import ClinicError, not_found
from clinic_api.models import (
    Appointment,
    AppointmentCreate,
    AppointmentStatus,
    CancellationQuote,
    CancellationResult,
    Patient,
    PatientCreate,
    Practitioner,
    RescheduleRequest,
    Service,
    Slot,
)

DEMO_PATIENTS = [
    ("Emily Johnson", "+15550100101", "emily.j@example.com"),
    ("Carlos Rivera", "+15550100102", None),
    ("Priya Shah", "+15550100103", "priya.shah@example.com"),
]
FILLER_NAMES = [
    "Olivia Brown",
    "Noah Davis",
    "Ava Wilson",
    "Liam Moore",
    "Mia Taylor",
    "Ethan Anderson",
    "Sofia Thomas",
    "Lucas Martin",
]


def normalize_phone(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not 8 <= len(digits) <= 15:
        raise ClinicError(422, "invalid_phone", f"'{raw}' is not a valid phone number.")
    if raw.strip().startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    return "+" + digits


class ClinicStore:
    def __init__(self, clock: Clock, *, seed: int = 42, filler: bool = True) -> None:
        self.clock = clock
        self.services = {s.id: s for s in catalog.SERVICES}
        self.practitioners = {p.id: p for p in catalog.PRACTITIONERS}
        self.patients: dict[str, Patient] = {}
        self.appointments: dict[str, Appointment] = {}
        self._patient_ids = count(1)
        self._appointment_ids = count(1)
        self._idempotency: dict[str, tuple[Hashable, object]] = {}
        self._seed(random.Random(seed), filler=filler)

    # Lookups

    def get_service(self, service_id: str) -> Service:
        if service_id not in self.services:
            raise not_found("service", service_id)
        return self.services[service_id]

    def get_practitioner(self, practitioner_id: str) -> Practitioner:
        if practitioner_id not in self.practitioners:
            raise not_found("practitioner", practitioner_id)
        return self.practitioners[practitioner_id]

    def get_patient(self, patient_id: str) -> Patient:
        if patient_id not in self.patients:
            raise not_found("patient", patient_id)
        return self.patients[patient_id]

    def get_appointment(self, appointment_id: str) -> Appointment:
        if appointment_id not in self.appointments:
            raise not_found("appointment", appointment_id)
        return self.appointments[appointment_id]

    # Patients

    def find_patients(self, phone: str) -> list[Patient]:
        normalized = normalize_phone(phone)
        return [p for p in self.patients.values() if p.phone == normalized]

    def create_patient(self, data: PatientCreate) -> Patient:
        if self.find_patients(data.phone):
            raise ClinicError(409, "patient_exists", "A patient with this phone already exists.")
        return self._add_patient(data.name.strip(), normalize_phone(data.phone), data.email)

    def patient_appointments(
        self, patient_id: str, *, include_past: bool = False
    ) -> list[Appointment]:
        self.get_patient(patient_id)
        now = self.clock.now()
        found = [
            a
            for a in self.appointments.values()
            if a.patient_id == patient_id and (include_past or a.end > now)
        ]
        return sorted(found, key=lambda a: a.start)

    # Availability

    def availability(
        self,
        service_id: str,
        date_from: date,
        date_to: date,
        practitioner_id: str | None = None,
        limit: int = 50,
    ) -> list[Slot]:
        service = self.get_service(service_id)
        if date_to < date_from:
            raise ClinicError(422, "invalid_range", "date_to must be on or after date_from.")
        if (date_to - date_from).days >= catalog.MAX_SEARCH_DAYS:
            raise ClinicError(
                422, "range_too_long", f"Search at most {catalog.MAX_SEARCH_DAYS} days at a time."
            )
        practitioners = self._practitioners_for(service, practitioner_id)
        busy = self._busy_by_practitioner()
        earliest = self.clock.now() + catalog.MIN_NOTICE
        duration = timedelta(minutes=service.duration_minutes)

        slots: list[Slot] = []
        day = date_from
        while day <= date_to:
            for window_start, window_end in self._windows(day):
                start = window_start
                while start + duration <= window_end:
                    end = start + duration
                    if start >= earliest:
                        for p in practitioners:
                            if not _overlaps(busy.get(p.id, []), start, end):
                                slots.append(
                                    Slot(
                                        start=start,
                                        end=end,
                                        practitioner_id=p.id,
                                        practitioner_name=p.name,
                                    )
                                )
                    start += catalog.SLOT_STEP
            day += timedelta(days=1)
        return slots[:limit]

    # Appointments

    def book(self, data: AppointmentCreate) -> Appointment:
        service = self.get_service(data.service_id)
        patient = self.get_patient(data.patient_id)
        start = _localize(data.start)
        end = start + timedelta(minutes=service.duration_minutes)
        self._check_bookable(start, end)
        self._check_patient_free(patient.id, start, end)
        practitioner = self._pick_practitioner(service, start, end, data.practitioner_id)
        return self._create_appointment(patient.id, service, practitioner.id, start)

    def cancellation_quote(self, appointment_id: str) -> CancellationQuote:
        appointment = self.get_appointment(appointment_id)
        if appointment.status == AppointmentStatus.CANCELLED:
            raise ClinicError(409, "already_cancelled", "This appointment is already cancelled.")
        now = self.clock.now()
        if appointment.start <= now:
            raise ClinicError(409, "appointment_started", "This appointment has already started.")
        free_until = appointment.start - catalog.FREE_CANCELLATION_WINDOW
        late = now > free_until
        return CancellationQuote(
            appointment_id=appointment.id,
            fee=catalog.LATE_CANCELLATION_FEE if late else 0,
            late=late,
            free_cancellation_until=free_until,
        )

    def cancel(self, appointment_id: str, reason: str | None) -> CancellationResult:
        quote = self.cancellation_quote(appointment_id)
        cancelled = self.appointments[appointment_id].model_copy(
            update={
                "status": AppointmentStatus.CANCELLED,
                "cancelled_at": self.clock.now(),
                "cancellation_reason": reason,
                "cancellation_fee": quote.fee,
            }
        )
        self.appointments[appointment_id] = cancelled
        return CancellationResult(appointment=cancelled, fee_charged=quote.fee)

    def reschedule(self, appointment_id: str, data: RescheduleRequest) -> Appointment:
        appointment = self.get_appointment(appointment_id)
        if appointment.status == AppointmentStatus.CANCELLED:
            raise ClinicError(409, "already_cancelled", "This appointment is already cancelled.")
        if self.clock.now() > appointment.start - catalog.FREE_CANCELLATION_WINDOW:
            raise ClinicError(
                409,
                "late_change_not_allowed",
                "Appointments can't be moved within 24 hours of the start time. "
                "Cancel instead (a late fee applies) or call the clinic.",
            )
        service = self.get_service(appointment.service_id)
        start = _localize(data.start)
        end = start + timedelta(minutes=service.duration_minutes)
        self._check_bookable(start, end)
        self._check_patient_free(appointment.patient_id, start, end, ignore_id=appointment.id)
        practitioner = self._pick_practitioner(
            service,
            start,
            end,
            data.practitioner_id,
            ignore_id=appointment.id,
            preferred=appointment.practitioner_id,
        )
        moved = appointment.model_copy(
            update={"start": start, "end": end, "practitioner_id": practitioner.id}
        )
        self.appointments[appointment_id] = moved
        return moved

    def record_deposit_payment(self, appointment_id: str) -> Appointment:
        appointment = self.get_appointment(appointment_id)
        if appointment.status != AppointmentStatus.PENDING_DEPOSIT:
            raise ClinicError(409, "no_deposit_due", "This appointment has no pending deposit.")
        paid = appointment.model_copy(update={"status": AppointmentStatus.CONFIRMED})
        self.appointments[appointment_id] = paid
        return paid

    # Idempotency

    def idempotent[T](
        self, key: str | None, fingerprint: Hashable, action: Callable[[], T]
    ) -> tuple[T, bool]:
        """Run `action` once per key. Returns (result, replayed)."""
        if key is None:
            return action(), False
        if key in self._idempotency:
            stored_fingerprint, result = self._idempotency[key]
            if stored_fingerprint != fingerprint:
                raise ClinicError(
                    422,
                    "idempotency_key_reused",
                    "This Idempotency-Key was already used for a different request.",
                )
            return result, True  # type: ignore[return-value]
        result = action()
        self._idempotency[key] = (fingerprint, result)
        return result, False

    # Internals

    def _add_patient(self, name: str, phone: str, email: str | None) -> Patient:
        patient = Patient(
            id=f"pat_{next(self._patient_ids):04d}",
            name=name,
            phone=phone,
            email=email,
            created_at=self.clock.now(),
        )
        self.patients[patient.id] = patient
        return patient

    def _create_appointment(
        self, patient_id: str, service: Service, practitioner_id: str, start: datetime
    ) -> Appointment:
        appointment_id = f"apt_{next(self._appointment_ids):05d}"
        needs_deposit = service.deposit > 0
        appointment = Appointment(
            id=appointment_id,
            patient_id=patient_id,
            service_id=service.id,
            practitioner_id=practitioner_id,
            start=start,
            end=start + timedelta(minutes=service.duration_minutes),
            status=(
                AppointmentStatus.PENDING_DEPOSIT if needs_deposit else AppointmentStatus.CONFIRMED
            ),
            price=service.price,
            deposit_due=service.deposit,
            payment_url=(
                catalog.PAYMENT_URL_TEMPLATE.format(appointment_id=appointment_id)
                if needs_deposit
                else None
            ),
            created_at=self.clock.now(),
        )
        self.appointments[appointment_id] = appointment
        return appointment

    def _windows(self, day: date) -> list[tuple[datetime, datetime]]:
        return [
            (
                datetime.combine(day, opens, tzinfo=catalog.TIMEZONE),
                datetime.combine(day, closes, tzinfo=catalog.TIMEZONE),
            )
            for opens, closes in catalog.OPENING_HOURS.get(day.weekday(), [])
        ]

    def _practitioners_for(
        self, service: Service, practitioner_id: str | None
    ) -> list[Practitioner]:
        if practitioner_id is not None:
            practitioner = self.get_practitioner(practitioner_id)
            if service.id not in practitioner.service_ids:
                raise ClinicError(
                    422,
                    "practitioner_does_not_offer_service",
                    f"{practitioner.name} does not offer {service.name}.",
                )
            return [practitioner]
        return [p for p in self.practitioners.values() if service.id in p.service_ids]

    def _busy_by_practitioner(
        self, ignore_id: str | None = None
    ) -> dict[str, list[tuple[datetime, datetime]]]:
        busy: dict[str, list[tuple[datetime, datetime]]] = {}
        for a in self.appointments.values():
            if a.status != AppointmentStatus.CANCELLED and a.id != ignore_id:
                busy.setdefault(a.practitioner_id, []).append((a.start, a.end))
        return busy

    def _check_bookable(self, start: datetime, end: datetime) -> None:
        now = self.clock.now()
        if start.minute % 15 or start.second or start.microsecond:
            raise ClinicError(422, "misaligned_start", "Appointments start on the quarter hour.")
        if start < now + catalog.MIN_NOTICE:
            raise ClinicError(409, "too_soon", "Appointments need at least 2 hours' notice.")
        if start > now + catalog.BOOKING_HORIZON:
            raise ClinicError(409, "too_far_ahead", "Appointments open 60 days in advance.")
        if not any(ws <= start and end <= we for ws, we in self._windows(start.date())):
            raise ClinicError(
                409, "outside_opening_hours", "That time is outside the clinic's opening hours."
            )

    def _check_patient_free(
        self, patient_id: str, start: datetime, end: datetime, *, ignore_id: str | None = None
    ) -> None:
        for a in self.appointments.values():
            if (
                a.patient_id == patient_id
                and a.id != ignore_id
                and a.status != AppointmentStatus.CANCELLED
                and a.start < end
                and start < a.end
            ):
                raise ClinicError(
                    409, "patient_double_booked", f"The patient already has {a.id} at that time."
                )

    def _pick_practitioner(
        self,
        service: Service,
        start: datetime,
        end: datetime,
        requested: str | None,
        *,
        ignore_id: str | None = None,
        preferred: str | None = None,
    ) -> Practitioner:
        candidates = self._practitioners_for(service, requested)
        if preferred is not None and requested is None:
            candidates.sort(key=lambda p: p.id != preferred)
        busy = self._busy_by_practitioner(ignore_id=ignore_id)
        for practitioner in candidates:
            if not _overlaps(busy.get(practitioner.id, []), start, end):
                return practitioner
        raise ClinicError(409, "slot_unavailable", "That time is no longer available.")

    def _seed(self, rng: random.Random, *, filler: bool) -> None:
        now = self.clock.now()
        emily, carlos, priya = (self._add_patient(*p) for p in DEMO_PATIENTS)

        # Emily has a cleaning soon (usually within 24h) to demo late-cancellation fees.
        self._seed_upcoming(emily, "cleaning", not_before=now + timedelta(hours=3))
        # Carlos has a check-up about five days out, so it can be moved or cancelled for free.
        self._seed_upcoming(carlos, "checkup", not_before=now + timedelta(days=5))
        # Priya is a returning patient with a past visit.
        past_visit = datetime.combine(
            (now - timedelta(days=95)).date(), datetime.min.time(), tzinfo=catalog.TIMEZONE
        ) + timedelta(hours=10)
        self._create_appointment(priya.id, self.services["checkup"], "dr-chen", past_visit)

        if filler:
            fillers = [
                self._add_patient(name, f"+1555019{i:04d}", None)
                for i, name in enumerate(FILLER_NAMES)
            ]
            self._seed_busy_calendar(rng, fillers, now.date())

    def _seed_upcoming(self, patient: Patient, service_id: str, not_before: datetime) -> None:
        first_day = not_before.date()
        last_day = first_day + timedelta(days=catalog.MAX_SEARCH_DAYS - 1)
        for slot in self.availability(service_id, first_day, last_day, limit=10_000):
            if slot.start >= not_before:
                self._create_appointment(
                    patient.id, self.services[service_id], slot.practitioner_id, slot.start
                )
                return

    def _seed_busy_calendar(self, rng: random.Random, fillers: list[Patient], today: date) -> None:
        """Fill roughly a third of the calendar so availability looks like a real practice."""
        for offset in range(21):
            day = today + timedelta(days=offset)
            for window_start, window_end in self._windows(day):
                for practitioner in self.practitioners.values():
                    start = window_start
                    while start < window_end:
                        service = self.services[rng.choice(practitioner.service_ids)]
                        end = start + timedelta(minutes=service.duration_minutes)
                        busy = self._busy_by_practitioner().get(practitioner.id, [])
                        if (
                            end <= window_end
                            and rng.random() < 0.3
                            and not _overlaps(busy, start, end)
                        ):
                            patient = rng.choice(fillers)
                            self._create_appointment(patient.id, service, practitioner.id, start)
                            start = end
                        else:
                            start += timedelta(minutes=30)


def _localize(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=catalog.TIMEZONE)
    return value.astimezone(catalog.TIMEZONE)


def _overlaps(intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> bool:
    return any(s < end and start < e for s, e in intervals)
