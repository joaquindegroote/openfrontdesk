from datetime import datetime
from typing import Any, Literal

from openfrontdesk.agent.context import CheckResult, ToolContext, ToolEvent
from openfrontdesk.guardrails.base import ProposedAction


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


def latest_quote(events: list[ToolEvent], appointment_id: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if (
            event.tool == "get_cancellation_quote"
            and event.status == "ok"
            and event.arguments.get("appointment_id") == appointment_id
        ):
            return event.result["quote"]
    return None
