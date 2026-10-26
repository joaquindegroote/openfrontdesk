from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from openfrontdesk.backends import BookingBackend
from openfrontdesk.business import BusinessProfile
from openfrontdesk.llm import ChatMessage

SideEffect = Literal["read", "write", "control"]
EventStatus = Literal["ok", "error", "blocked", "invalid"]


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str


class ToolEvent(BaseModel):
    """One tool call as it happened: what the model asked for, what the guards said, the result."""

    id: str
    call_id: str
    tool: str
    side_effect: SideEffect
    arguments: dict[str, Any]
    status: EventStatus
    result: Any
    summary: str | None = None
    checks: list[CheckResult] = []
    duration_ms: int = 0
    turn_id: str | None = None
    created_at: datetime


class HandoffRequest(BaseModel):
    reason: str
    summary: str
    automatic: bool = False


@dataclass
class ToolContext:
    """Everything a tool or guard may look at while handling one customer turn."""

    conversation_id: str
    business: BusinessProfile
    backend: BookingBackend
    now: datetime
    customer_phone: str | None = None
    # Conversation so far, including this turn (no system prompt). Guards read it.
    transcript: list[ChatMessage] = field(default_factory=list)
    # Every tool event in the conversation, including earlier turns.
    events: list[ToolEvent] = field(default_factory=list)
    # Set by the executor for write tools; passed to the booking system for dedupe.
    idempotency_key: str | None = None
