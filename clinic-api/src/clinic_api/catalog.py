"""Static configuration of the fictional clinic: services, staff, hours and policies."""

from datetime import time, timedelta
from zoneinfo import ZoneInfo

from clinic_api.models import Practitioner, Service

TIMEZONE = ZoneInfo("America/New_York")

SERVICES = [
    Service(
        id="checkup",
        name="Check-up & X-rays",
        description="Exam with a dentist, bitewing X-rays and a treatment plan if needed.",
        duration_minutes=30,
        price=95,
        deposit=0,
    ),
    Service(
        id="cleaning",
        name="Professional cleaning",
        description="Scaling and polishing with a hygienist.",
        duration_minutes=45,
        price=120,
        deposit=0,
    ),
    Service(
        id="filling",
        name="Tooth-colored filling",
        description="Composite filling for one tooth. Requires a check-up in the last 6 months.",
        duration_minutes=60,
        price=180,
        deposit=0,
    ),
    Service(
        id="whitening",
        name="In-office whitening",
        description="Single-session professional whitening.",
        duration_minutes=90,
        price=350,
        deposit=100,
    ),
    Service(
        id="emergency",
        name="Emergency visit",
        description="Same-day visit for tooth pain, swelling or a broken tooth.",
        duration_minutes=30,
        price=150,
        deposit=0,
    ),
]

PRACTITIONERS = [
    Practitioner(
        id="dr-chen",
        name="Dr. Maya Chen",
        title="General dentist",
        service_ids=["checkup", "filling", "emergency", "whitening"],
    ),
    Practitioner(
        id="dr-ortega",
        name="Dr. Luis Ortega",
        title="General & cosmetic dentist",
        service_ids=["checkup", "filling", "whitening", "emergency"],
    ),
    Practitioner(
        id="sam-patel",
        name="Sam Patel",
        title="Dental hygienist",
        service_ids=["cleaning"],
    ),
]

_WEEKDAY = [(time(9), time(13)), (time(14), time(18))]
# Monday is 0. Sunday is closed.
OPENING_HOURS: dict[int, list[tuple[time, time]]] = {
    0: _WEEKDAY,
    1: _WEEKDAY,
    2: _WEEKDAY,
    3: _WEEKDAY,
    4: _WEEKDAY,
    5: [(time(9), time(13))],
}

SLOT_STEP = timedelta(minutes=15)
MIN_NOTICE = timedelta(hours=2)
BOOKING_HORIZON = timedelta(days=60)
MAX_SEARCH_DAYS = 14
FREE_CANCELLATION_WINDOW = timedelta(hours=24)
LATE_CANCELLATION_FEE = 50
PAYMENT_URL_TEMPLATE = "https://pay.example.com/brightsmile/{appointment_id}"
