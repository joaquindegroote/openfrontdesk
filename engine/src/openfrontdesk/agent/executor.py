"""Runs one tool call: validate, guard, execute with a timeout, and record what happened.

Nothing a tool does can crash the conversation. Every outcome, including a blocked action or
a booking-system outage, becomes a structured result the model can react to.
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from openfrontdesk.agent.context import (
    CheckResult,
    EventStatus,
    HandoffRequest,
    ToolContext,
    ToolEvent,
)
from openfrontdesk.backends import BackendError
from openfrontdesk.guardrails import ProposedAction, evaluate
from openfrontdesk.ids import new_id
from openfrontdesk.llm import ToolCall
from openfrontdesk.llm.base import INVALID_JSON_KEY
from openfrontdesk.observability import INPUT, OUTPUT, SESSION_ID, SPAN_KIND, TOOL_NAME, tracer
from openfrontdesk.tools import Tool, ToolRegistry

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, *, timeout_s: float = 20.0) -> None:
        self.registry = registry
        self.timeout_s = timeout_s

    async def execute(
        self, call: ToolCall, ctx: ToolContext
    ) -> tuple[ToolEvent, HandoffRequest | None]:
        started = time.perf_counter()
        with tracer.start_as_current_span(f"tool {call.name}") as span:
            span.set_attribute(SPAN_KIND, "TOOL")
            span.set_attribute(TOOL_NAME, call.name)
            span.set_attribute(SESSION_ID, ctx.conversation_id)
            span.set_attribute(INPUT, json.dumps(call.arguments, default=str))
            event, handoff = await self._execute(call, ctx)
            event.duration_ms = round((time.perf_counter() - started) * 1000)
            span.set_attribute("tool.status", event.status)
            span.set_attribute(OUTPUT, json.dumps(event.result, default=str))
        return event, handoff

    async def _execute(
        self, call: ToolCall, ctx: ToolContext
    ) -> tuple[ToolEvent, HandoffRequest | None]:
        tool = self.registry.get(call.name)
        if tool is None:
            return _event(call, "read", "invalid", _error("unknown_tool", "No such tool.")), None
        if INVALID_JSON_KEY in call.arguments:
            result = _error("invalid_arguments", "Arguments were not a valid JSON object.")
            return _event(call, tool.side_effect, "invalid", result), None
        try:
            params = tool.params.model_validate(call.arguments)
        except ValidationError as exc:
            details = [
                {"field": ".".join(str(p) for p in err["loc"]), "problem": err["msg"]}
                for err in exc.errors()
            ]
            result = {**_error("invalid_arguments", "Fix the arguments."), "details": details}
            return _event(call, tool.side_effect, "invalid", result), None

        # From here on, events record the validated arguments (defaults filled, types normalized).
        call = call.model_copy(update={"arguments": params.model_dump(mode="json")})
        summary = await _summarize(tool, ctx, params)
        checks: list[CheckResult] = []
        if tool.guards:
            action = ProposedAction(tool=tool.name, params=params, summary=summary or tool.name)
            checks = await evaluate(tool.guards, ctx, action)
            failed = next((c for c in checks if not c.passed), None)
            if failed is not None:
                result = {
                    "status": "blocked",
                    "check": failed.name,
                    "reason": failed.detail,
                    "instructions": "Do not retry this action until the reason is resolved.",
                }
                return _event(call, tool.side_effect, "blocked", result, summary, checks), None

        if tool.side_effect == "write":
            ctx = replace(ctx, idempotency_key=idempotency_key(ctx, tool, params))
        try:
            output = await asyncio.wait_for(tool.handler(ctx, params), self.timeout_s)
        except BackendError as exc:
            result = {**_error(exc.code, exc.message), "retryable": exc.retryable}
            return _event(call, tool.side_effect, "error", result, summary, checks), None
        except TimeoutError:
            result = {**_error("timeout", "The action took too long."), "retryable": True}
            return _event(call, tool.side_effect, "error", result, summary, checks), None
        except Exception:
            logger.exception("tool raised", extra={"tool": call.name})
            result = {
                **_error("internal_error", "The tool failed unexpectedly."),
                "retryable": False,
            }
            return _event(call, tool.side_effect, "error", result, summary, checks), None

        if isinstance(output, HandoffRequest):
            result = {
                "status": "handed_off",
                "instructions": (
                    "A member of the front-desk team will reply in this same conversation. Tell "
                    "the customer that in one sentence. Don't promise calls, emails or wait "
                    "times. Do not call more tools."
                ),
            }
            return _event(call, tool.side_effect, "ok", result, summary, checks), output
        return _event(call, tool.side_effect, "ok", output, summary, checks), None


async def _summarize(tool: Tool, ctx: ToolContext, params: BaseModel) -> str | None:
    if tool.describe is None:
        return None
    try:
        return await tool.describe(ctx, params)
    except Exception:
        # Describing needs the booking system; if it's down, fall back to the raw arguments.
        logger.warning("could not describe action", exc_info=True, extra={"tool": tool.name})
        return f"{tool.name} {json.dumps(params.model_dump(mode='json'))}"


def idempotency_key(ctx: ToolContext, tool: Tool, params: BaseModel) -> str:
    """Same conversation + same action + no successful run yet => same key.

    Retries after a timeout or a dropped connection reuse the key, so the booking system can
    return the original result instead of booking twice. Once an action has succeeded, an
    identical request later in the conversation is a new, deliberate action and gets a new key.
    """
    arguments = params.model_dump(mode="json")
    successes = sum(
        1
        for e in ctx.events
        if e.tool == tool.name and e.status == "ok" and e.arguments == arguments
    )
    canonical = json.dumps(arguments, sort_keys=True)
    raw = f"{ctx.conversation_id}:{tool.name}:{canonical}:{successes}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": code, "message": message}


def _event(
    call: ToolCall,
    side_effect: Any,
    status: EventStatus,
    result: Any,
    summary: str | None = None,
    checks: list[CheckResult] | None = None,
) -> ToolEvent:
    return ToolEvent(
        id=new_id("evt"),
        call_id=call.id,
        tool=call.name,
        side_effect=side_effect,
        arguments=call.arguments,
        status=status,
        result=result,
        summary=summary,
        checks=checks or [],
        created_at=datetime.now(UTC),
    )
