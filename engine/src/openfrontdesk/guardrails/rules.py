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
        start = ctx.business.local(getattr(action.params, self.field))
        if start > ctx.now:
            return CheckResult(name=self.name, passed=True, detail="Start time is in the future.")
        return CheckResult(
            name=self.name,
            passed=False,
            detail=f"{start.isoformat()} is in the past. Offer times from find_available_slots.",
        )


class CancellationFeeDisclosed:
    """Cancelling needs a quote from this conversation, the fee it named must still be the fee,
    and if there is one, the customer must have accepted it.

    The booking system is asked again right before cancelling. A quote can be days old, and a
    free cancellation becomes a late one when its window closes: the customer who was told
    "no fee" on Monday would be charged on Friday without a word."""

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
        try:
            now = await ctx.backend.cancellation_quote(appointment_id)
        except BackendError as exc:
            return CheckResult(
                name=self.name,
                passed=False,
                detail=(
                    f"Could not check the cancellation fee ({exc.message}). Try once more; if it "
                    "keeps failing, hand off."
                ),
                error=True,
            )
        fee = quote["fee"]
        if now.fee != fee:
            return CheckResult(
                name=self.name,
                passed=False,
                detail=(
                    f"The fee changed since the customer was told: cancelling now costs "
                    f"${now.fee}, not ${fee}. Call get_cancellation_quote again, tell the "
                    "customer, and cancel only if they accept the new fee."
                ),
            )
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
        start = ctx.business.local(datetime.fromisoformat(arguments["start"]))
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
                error=True,
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
                allowed = ctx.business.phone_key(value) in phones
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
                error=True,
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
        ctx.business.phone_key(e.arguments["phone"])
        for e in ctx.events
        if e.tool == "verify_phone"
        and e.status == "ok"
        and isinstance(e.result, dict)
        and e.result.get("verified") is True
    }
    if ctx.phone_verified and ctx.customer_phone:
        phones.add(ctx.business.phone_key(ctx.customer_phone))
    return phones


async def _customer_ids(ctx: ToolContext, phones: set[str]) -> set[str]:
    found = [await ctx.backend.find_customer("+" + phone) for phone in sorted(phones)]
    return {customer.id for customer in found if customer is not None}


def pending_fee(events: list[ToolEvent]) -> dict[str, Any] | None:
    """The latest cancellation quote, if it has a fee and that appointment hasn't been
    cancelled since: a fee the customer has been told about and hasn't paid."""
    cancelled: set[Any] = set()
    for event in reversed(events):
        if event.status != "ok":
            continue
        if event.tool == "cancel_appointment":
            cancelled.add(event.arguments.get("appointment_id"))
        if event.tool == "get_cancellation_quote":
            quote: dict[str, Any] = event.result["quote"]
            unpaid = quote["fee"] > 0 and quote["appointment_id"] not in cancelled
            return quote if unpaid else None
    return None


def latest_quote(events: list[ToolEvent], appointment_id: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if (
            event.tool == "get_cancellation_quote"
            and event.status == "ok"
            and event.arguments.get("appointment_id") == appointment_id
        ):
            return event.result["quote"]
    return None
