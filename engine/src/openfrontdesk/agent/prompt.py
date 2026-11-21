from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple

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


class SystemPrompt(NamedTuple):
    """The system prompt in two parts, in the order they are sent.

    `business` is the same for every customer and every turn, until the business changes
    something: the rules, the business's information, its services. The model's provider keeps
    a prompt it has just seen and charges a fraction to read it again, but only up to the
    first character that differs. So everything that varies is in `turn`, after it: the time,
    who is writing, their appointments.
    """

    business: str
    turn: str


def render_system_prompt(
    business: BusinessProfile,
    *,
    now: datetime,
    customer_phone: str | None,
    phone_verified: bool = False,
    snapshot: BookingSnapshot | None = None,
) -> SystemPrompt:
    snapshot = snapshot or BookingSnapshot()
    return SystemPrompt(
        # It is given nothing else to render with, so nothing about the turn can get into it.
        business=_templates.get_template("system_prompt.md.j2").render(
            business=business, services=snapshot.services
        ),
        turn=_templates.get_template("this_conversation.md.j2").render(
            business=business,
            now=long_label(now, business.tz),
            today=now.astimezone(business.tz).date().isoformat(),
            customer_phone=customer_phone,
            phone_verified=phone_verified,
            customer=snapshot.customer,
            customer_not_found=snapshot.customer_not_found,
            appointments=_appointment_lines(snapshot, business),
        ),
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
