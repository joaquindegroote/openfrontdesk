from dataclasses import dataclass, field
from datetime import datetime

from jinja2 import Environment, PackageLoader, StrictUndefined

from openfrontdesk.backends import Appointment, Customer, Service
from openfrontdesk.business import BusinessProfile
from openfrontdesk.timefmt import long_label, short_label

_templates = Environment(
    loader=PackageLoader("openfrontdesk.agent", "templates"),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


@dataclass(frozen=True)
class BookingSnapshot:
    """Booking-system data fetched before the turn, so the model doesn't spend steps on it.

    Everything is optional: if the booking system is slow or down, the prompt simply omits it
    and the model falls back to its tools.
    """

    services: list[Service] = field(default_factory=list)
    customer: Customer | None = None
    # The phone was looked up and nobody is registered with it.
    customer_not_found: bool = False
    # The known customer's upcoming appointments; None if they couldn't be fetched.
    appointments: list[Appointment] | None = None


def render_system_prompt(
    business: BusinessProfile,
    *,
    now: datetime,
    customer_phone: str | None,
    snapshot: BookingSnapshot | None = None,
) -> str:
    snapshot = snapshot or BookingSnapshot()
    return _templates.get_template("system_prompt.md.j2").render(
        business=business,
        now=long_label(now, business.tz),
        today=now.astimezone(business.tz).date().isoformat(),
        customer_phone=customer_phone,
        services=snapshot.services,
        customer=snapshot.customer,
        customer_not_found=snapshot.customer_not_found,
        appointments=_appointment_lines(snapshot, business),
    )


def _appointment_lines(snapshot: BookingSnapshot, business: BusinessProfile) -> list[str] | None:
    if snapshot.appointments is None:
        return None
    names = {s.id: s.name for s in snapshot.services}
    lines = []
    for appointment in snapshot.appointments:
        if appointment.status == "cancelled":
            continue
        pending = ", waiting for the deposit" if appointment.status == "pending_deposit" else ""
        lines.append(
            f"{names.get(appointment.service_id, appointment.service_id)} on "
            f"{short_label(appointment.start, business.tz)}{pending} "
            f"(appointment_id {appointment.id})"
        )
    return lines
