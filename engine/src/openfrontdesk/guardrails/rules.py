from datetime import datetime
from typing import Any, Literal

from openfrontdesk.agent.context import CheckResult, ToolContext, ToolEvent
from openfrontdesk.backends import BackendError
from openfrontdesk.guardrails.base import ProposedAction
from openfrontdesk.timefmt import short_label


class StartsInFuture:
    """The proposed start time must be later than now, in the business's timezone."""

    kind: Literal["rule"] = "rule"

    def __init__(self, field: str = "start") -> None:
        self.field = field
        self.name = "starts_in_future"

    async def check(self, ctx: ToolContext, action: ProposedAction) -> CheckResult:
        start: datetime = getattr(action.params, self.field)
        if start.tzinfo is None:
            start = start.replace(tzinfo=ctx.business.tz)
        if start > ctx.now:
            return CheckResult(name=self.name, passed=True, detail="Start time is in the future.")
        return CheckResult(
            name=self.name,
            passed=False,
            detail=f"{start.isoformat()} is in the past. Offer times from find_available_slots.",
        )


class CancellationFeeDisclosed:
    """Cancelling needs a quote from this conversation and, if there's a fee, its acceptance."""

    kind: Literal["rule"] = "rule"
    name = "cancellation_fee_disclosed"

    async def check(self, ctx: ToolContext, action: ProposedAction) -> CheckResult:
        arguments = action.arguments
        appointment_id = arguments["appointment_id"]
        quote = latest_quote(ctx.events, appointment_id)
        if quote is None:
            return CheckResult(
                name=self.name,
                passed=False,
                detail=(
                    f"No cancellation quote for {appointment_id} yet. Call "
                    "get_cancellation_quote and tell the customer whether a fee applies."
                ),
            )
        fee = quote["fee"]
        if fee > 0 and not arguments.get("fee_acknowledged", False):
            return CheckResult(
                name=self.name,
                passed=False,
                detail=(
                    f"A late-cancellation fee of ${fee} applies. Tell the customer and cancel "
                    "only if they accept it, with fee_acknowledged=true."
                ),
            )
        return CheckResult(
            name=self.name,
            passed=True,
            detail=f"Quote shown; fee ${fee}." if fee else "Quote shown; no fee.",
        )


class NoDuplicateBooking:
    """A booking for a service the customer already has coming up is usually a change of time
    that went wrong: the old appointment would stay. Move it instead, unless the customer said
    they want both."""

    kind: Literal["rule"] = "rule"
    name = "no_duplicate_booking"

    async def check(self, ctx: ToolContext, action: ProposedAction) -> CheckResult:
        arguments = action.arguments
        if arguments.get("keep_existing", False):
            return CheckResult(
                name=self.name, passed=True, detail="The customer keeps their other appointments."
            )
        start = datetime.fromisoformat(arguments["start"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=ctx.business.tz)
        try:
            appointments = await ctx.backend.list_appointments(arguments["customer_id"])
        except BackendError as exc:
            return CheckResult(
                name=self.name,
                passed=False,
                detail=(
                    f"Could not check the customer's appointments ({exc.message}). Try once "
                    "more; if it keeps failing, hand off."
                ),
            )
        # An appointment at the same start is this booking going through again after a retry:
        # the booking system answers it from the idempotency key.
        others = [
            a
            for a in appointments
            if a.service_id == arguments["service_id"]
            and a.status != "cancelled"
            and a.end > ctx.now
            and a.start != start
        ]
        if not others:
            return CheckResult(
                name=self.name, passed=True, detail="No other upcoming appointment for it."
            )
        existing = others[0]
        return CheckResult(
            name=self.name,
            passed=False,
            detail=(
                "The customer already has this service booked on "
                f"{short_label(existing.start, ctx.business.tz)} (appointment_id "
                f"{existing.id}). To change its time, use reschedule_appointment. Book another "
                "only if they said they want to keep both, with keep_existing=true."
            ),
        )


class IdentityVerified:
    """A customer's data is only for someone who proved they hold the customer's phone: through
    the chat's own channel (WhatsApp, SMS, a call) or a code texted to it and read back here.

    `field` names the argument that points at the customer: their phone, their customer_id, or
    one of their appointments.
    """

    kind: Literal["rule"] = "rule"
    name = "identity_verified"

    def __init__(self, field: Literal["phone", "customer_id", "appointment_id"]) -> None:
        self.field = field

    async def check(self, ctx: ToolContext, action: ProposedAction) -> CheckResult:
        phones = verified_phones(ctx)
        value = str(action.arguments[self.field])
        try:
            if self.field == "phone":
                allowed = phone_key(value) in phones
            else:
                customer_id = value
                if self.field == "appointment_id":
                    customer_id = (await ctx.backend.get_appointment(value)).customer_id
                allowed = customer_id in await _customer_ids(ctx, phones)
        except BackendError as exc:
            return CheckResult(
                name=self.name,
                passed=False,
                detail=(
                    f"Could not check who this customer is ({exc.message}). Try once more; if "
                    "it keeps failing, hand off."
                ),
            )
        if allowed:
            return CheckResult(name=self.name, passed=True, detail="Identity verified.")
        return CheckResult(
            name=self.name,
            passed=False,
            detail=(
                "Nobody in this chat has proved they hold this customer's phone. Text a code to "
                "the customer's own number with send_verification_code and check it with "
                "verify_phone. Never share or change another person's details."
            ),
        )


def verified_phones(ctx: ToolContext) -> set[str]:
    """Phones proven in this conversation: the channel's own, and any verified with a code."""
    phones = {
        phone_key(e.arguments["phone"])
        for e in ctx.events
        if e.tool == "verify_phone"
        and e.status == "ok"
        and isinstance(e.result, dict)
        and e.result.get("verified") is True
    }
    if ctx.phone_verified and ctx.customer_phone:
        phones.add(phone_key(ctx.customer_phone))
    return phones


def phone_key(phone: str) -> str:
    """Digits with the country code, so "+1 555-010-0102" and "5550100102" compare equal. Ten
    digits are read as a North American number, like the booking system does."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return "1" + digits if len(digits) == 10 else digits


async def _customer_ids(ctx: ToolContext, phones: set[str]) -> set[str]:
    found = [await ctx.backend.find_customer("+" + phone) for phone in sorted(phones)]
    return {customer.id for customer in found if customer is not None}


def latest_quote(events: list[ToolEvent], appointment_id: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if (
            event.tool == "get_cancellation_quote"
            and event.status == "ok"
            and event.arguments.get("appointment_id") == appointment_id
        ):
            return event.result["quote"]
    return None
