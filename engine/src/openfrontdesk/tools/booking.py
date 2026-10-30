"""The standard toolset for appointment-based businesses."""

from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from openfrontdesk.agent.context import HandoffRequest, ToolContext
from openfrontdesk.backends import Appointment, Slot
from openfrontdesk.guardrails import (
    CancellationFeeDisclosed,
    CustomerConfirmed,
    NoDuplicateBooking,
    StartsInFuture,
)
from openfrontdesk.guardrails.rules import latest_quote
from openfrontdesk.llm import LLMClient
from openfrontdesk.timefmt import short_label
from openfrontdesk.tools.registry import ToolRegistry

MAX_SLOTS_SHOWN = 8
MAX_SLOTS_PER_DAY = 3
MAX_SEARCH_DAYS = 14


class NoParams(BaseModel):
    pass


class FindSlotsParams(BaseModel):
    service_id: str = Field(description="Service id from list_services.")
    date_from: date = Field(description="First day to search, YYYY-MM-DD.")
    date_to: date | None = Field(
        default=None,
        description="Last day to search, at most 13 days after date_from. Default: a week.",
    )
    practitioner_id: str | None = Field(default=None, description="Only this practitioner.")
    part_of_day: Literal["any", "morning", "afternoon"] = "any"


class FindCustomerParams(BaseModel):
    phone: str = Field(description="Phone number, ideally in international format.")


class RegisterCustomerParams(BaseModel):
    name: str = Field(description="Full name.")
    phone: str
    email: str | None = None


class CustomerParams(BaseModel):
    customer_id: str


class BookParams(BaseModel):
    customer_id: str
    service_id: str
    start: datetime = Field(description="Exact slot start from find_available_slots (ISO 8601).")
    practitioner_id: str | None = Field(
        default=None, description="Practitioner of the chosen slot."
    )
    keep_existing: bool = Field(
        default=False,
        description=(
            "True only if the customer already has this service booked and said they want "
            "another one as well, not a new time for it."
        ),
    )


class AppointmentParams(BaseModel):
    appointment_id: str


class CancelParams(BaseModel):
    appointment_id: str
    reason: str | None = None
    fee_acknowledged: bool = Field(
        default=False,
        description="True only when a late fee applies and the customer explicitly accepted it.",
    )


class RescheduleParams(BaseModel):
    appointment_id: str
    new_start: datetime = Field(description="Exact slot start from find_available_slots.")
    practitioner_id: str | None = None


class HandoffParams(BaseModel):
    reason: Literal[
        "customer_request", "medical_urgency", "complaint", "billing", "out_of_scope", "other"
    ]
    summary: str = Field(
        description="2-3 sentences for the staff member: who the customer is and what they need."
    )


# Action summaries use the words the customer saw (service names, prices, practitioner names):
# the confirmation judge compares them with the conversation, where ids never appear.


async def _service_label(ctx: ToolContext, service_id: str) -> str:
    services = {s.id: s for s in await ctx.backend.list_services()}
    service = services.get(service_id)
    if service is None:
        return f"service '{service_id}'"
    deposit = f", ${service.deposit} deposit" if service.deposit else ", no deposit"
    return f"{service.name} ({service.duration_minutes} min, ${service.price}{deposit})"


def _practitioner_label(ctx: ToolContext, practitioner_id: str | None) -> str:
    if practitioner_id is None:
        return "any available practitioner"
    for event in reversed(ctx.events):
        if event.tool == "find_available_slots" and event.status == "ok":
            for slot in event.result.get("slots", []):
                if slot["practitioner_id"] == practitioner_id:
                    return slot["practitioner_name"]
    return practitioner_id


async def _describe_registration(ctx: ToolContext, params: RegisterCustomerParams) -> str:
    return f"Register new customer {params.name} ({params.phone})."


async def _describe_booking(ctx: ToolContext, params: BookParams) -> str:
    # keep_existing is in the summary so the judge checks the customer asked for both.
    keep = " The customer also keeps their existing one." if params.keep_existing else ""
    return (
        f"Book {await _service_label(ctx, params.service_id)} on "
        f"{short_label(params.start, ctx.business.tz)} with "
        f"{_practitioner_label(ctx, params.practitioner_id)}.{keep}"
    )


async def _describe_cancellation(ctx: ToolContext, params: CancelParams) -> str:
    appointment = await ctx.backend.get_appointment(params.appointment_id)
    quote = latest_quote(ctx.events, params.appointment_id)
    fee = f"late-cancellation fee ${quote['fee']}" if quote and quote["fee"] else "no fee"
    return (
        f"Cancel {await _service_label(ctx, appointment.service_id)} on "
        f"{short_label(appointment.start, ctx.business.tz)} ({fee})."
    )


async def _describe_reschedule(ctx: ToolContext, params: RescheduleParams) -> str:
    appointment = await ctx.backend.get_appointment(params.appointment_id)
    tz = ctx.business.tz
    return (
        f"Move {await _service_label(ctx, appointment.service_id)} from "
        f"{short_label(appointment.start, tz)} to {short_label(params.new_start, tz)}."
    )


def build_booking_tools(judge: LLMClient) -> ToolRegistry:
    registry = ToolRegistry()
    confirmed = CustomerConfirmed(judge)

    @registry.tool()
    async def list_services(ctx: ToolContext, params: NoParams) -> dict[str, Any]:
        """List the services offered, with duration, price and any deposit required."""
        services = await ctx.backend.list_services()
        return {"services": [s.model_dump(mode="json") for s in services]}

    @registry.tool()
    async def find_available_slots(ctx: ToolContext, params: FindSlotsParams) -> dict[str, Any]:
        """Find open appointment times for a service. Returns a spread of options, not all."""
        today = ctx.now.date()
        date_from = max(params.date_from, today)
        date_to = params.date_to or params.date_from + timedelta(days=6)
        date_to = min(date_to, date_from + timedelta(days=MAX_SEARCH_DAYS - 1))
        if date_to < date_from:
            return {"slots": [], "note": "That date range is in the past."}

        slots = await ctx.backend.find_slots(
            params.service_id, date_from, date_to, params.practitioner_id
        )
        tz = ctx.business.tz
        if params.part_of_day == "morning":
            slots = [s for s in slots if s.start.astimezone(tz).hour < 12]
        elif params.part_of_day == "afternoon":
            slots = [s for s in slots if s.start.astimezone(tz).hour >= 12]
        return {
            "searched": f"{date_from.isoformat()} to {date_to.isoformat()}",
            "total_open": len(slots),
            "slots": [_slot_view(s, ctx) for s in _spread(slots, ctx)],
        }

    @registry.tool()
    async def find_customer(ctx: ToolContext, params: FindCustomerParams) -> dict[str, Any]:
        """Look up a customer by phone number."""
        customer = await ctx.backend.find_customer(params.phone)
        if customer is None:
            return {"found": False}
        return {"found": True, "customer": customer.model_dump(mode="json")}

    @registry.tool(side_effect="write", describe=_describe_registration)
    async def register_customer(ctx: ToolContext, params: RegisterCustomerParams) -> dict[str, Any]:
        """Register a new customer. Only after find_customer found no match."""
        customer = await ctx.backend.create_customer(params.name, params.phone, params.email)
        return {"customer": customer.model_dump(mode="json")}

    @registry.tool()
    async def list_customer_appointments(
        ctx: ToolContext, params: CustomerParams
    ) -> dict[str, Any]:
        """List a customer's upcoming appointments."""
        appointments = await ctx.backend.list_appointments(params.customer_id)
        return {"appointments": [_appointment_view(a, ctx) for a in appointments]}

    @registry.tool(
        side_effect="write",
        guards=[StartsInFuture("start"), NoDuplicateBooking(), confirmed],
        describe=_describe_booking,
    )
    async def book_appointment(ctx: ToolContext, params: BookParams) -> dict[str, Any]:
        """Book an appointment. Only after the customer explicitly confirmed the exact details."""
        appointment = await ctx.backend.book(
            params.customer_id,
            params.service_id,
            params.start,
            params.practitioner_id,
            idempotency_key=_key(ctx),
        )
        result: dict[str, Any] = {
            "booked": True,
            "appointment": _appointment_view(appointment, ctx),
        }
        if appointment.payment_url:
            result["next_step"] = (
                f"Send the customer this link to pay the ${appointment.deposit_due} deposit: "
                f"{appointment.payment_url}. The slot is held until it is paid."
            )
        return result

    @registry.tool()
    async def get_cancellation_quote(ctx: ToolContext, params: AppointmentParams) -> dict[str, Any]:
        """Check whether cancelling an appointment now has a fee. Required before cancelling."""
        quote = await ctx.backend.cancellation_quote(params.appointment_id)
        view = quote.model_dump(mode="json")
        view["free_cancellation_until_label"] = short_label(
            quote.free_cancellation_until, ctx.business.tz
        )
        return {"quote": view}

    @registry.tool(
        side_effect="write",
        guards=[CancellationFeeDisclosed(), confirmed],
        describe=_describe_cancellation,
    )
    async def cancel_appointment(ctx: ToolContext, params: CancelParams) -> dict[str, Any]:
        """Cancel an appointment. Needs a quote first and the customer's explicit confirmation."""
        result = await ctx.backend.cancel(
            params.appointment_id, params.reason, idempotency_key=_key(ctx)
        )
        return {
            "cancelled": True,
            "fee_charged": result.fee_charged,
            "appointment": _appointment_view(result.appointment, ctx),
        }

    @registry.tool(
        side_effect="write",
        guards=[StartsInFuture("new_start"), confirmed],
        describe=_describe_reschedule,
    )
    async def reschedule_appointment(ctx: ToolContext, params: RescheduleParams) -> dict[str, Any]:
        """Move an appointment to a new time. Only after the customer confirmed the new time."""
        appointment = await ctx.backend.reschedule(
            params.appointment_id,
            params.new_start,
            params.practitioner_id,
            idempotency_key=_key(ctx),
        )
        return {"rescheduled": True, "appointment": _appointment_view(appointment, ctx)}

    @registry.tool(side_effect="control")
    async def handoff_to_human(ctx: ToolContext, params: HandoffParams) -> HandoffRequest:
        """Hand the conversation to the front-desk staff. Use when the instructions say so."""
        return HandoffRequest(reason=params.reason, summary=params.summary)

    return registry


def _key(ctx: ToolContext) -> str:
    if ctx.idempotency_key is None:
        raise RuntimeError("write tools must run through ToolExecutor, which sets the key")
    return ctx.idempotency_key


def _spread(slots: list[Slot], ctx: ToolContext) -> list[Slot]:
    """Pick varied options: at most one per hour and a few per day, earliest first."""
    picked: list[Slot] = []
    seen_hours: set[tuple[date, int]] = set()
    per_day: dict[date, int] = {}
    for slot in slots:
        local = slot.start.astimezone(ctx.business.tz)
        hour = (local.date(), local.hour)
        if hour in seen_hours or per_day.get(local.date(), 0) >= MAX_SLOTS_PER_DAY:
            continue
        seen_hours.add(hour)
        per_day[local.date()] = per_day.get(local.date(), 0) + 1
        picked.append(slot)
        if len(picked) == MAX_SLOTS_SHOWN:
            break
    return picked


def _slot_view(slot: Slot, ctx: ToolContext) -> dict[str, Any]:
    return {
        "start": slot.start.isoformat(),
        "label": short_label(slot.start, ctx.business.tz),
        "practitioner_id": slot.practitioner_id,
        "practitioner_name": slot.practitioner_name,
    }


def _appointment_view(appointment: Appointment, ctx: ToolContext) -> dict[str, Any]:
    view = appointment.model_dump(mode="json", exclude_none=True)
    view["label"] = short_label(appointment.start, ctx.business.tz)
    return view
