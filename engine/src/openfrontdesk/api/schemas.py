from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from openfrontdesk.agent.context import ToolEvent
from openfrontdesk.storage.models import MessageRow

Channel = Literal["web", "whatsapp", "sms", "voice"]


class ConversationCreate(BaseModel):
    channel: Channel = "web"
    customer_phone: str | None = Field(default=None, max_length=32)
    customer_name: str | None = Field(default=None, max_length=120)


class CustomerMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class StaffMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    author: str = Field(default="Front desk", max_length=80)


class TakeOverIn(BaseModel):
    reason: str = Field(default="staff_takeover", max_length=40)
    summary: str | None = Field(default=None, max_length=1000)


class BusinessOut(BaseModel):
    id: str
    name: str
    assistant_name: str
    timezone: str
    phone: str
    address: str


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel: str
    status: str
    customer_phone: str | None
    customer_name: str | None
    handoff_reason: str | None
    handoff_summary: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: int
    role: Literal["customer", "assistant", "staff"]
    text: str
    author: str | None = None
    turn_id: str | None = None
    created_at: datetime


class TurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    model: str | None
    steps: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    outcome: str
    created_at: datetime


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]
    events: list[ToolEvent]
    turns: list[TurnOut]


class CustomerMessageOut(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]
    events: list[ToolEvent]
    turn: TurnOut | None


_VISIBLE_ROLES = {"user": "customer", "assistant": "assistant", "staff": "staff"}


def visible_messages(rows: list[MessageRow]) -> list[MessageOut]:
    """What a person sees in the chat: no tool traffic, no tool-call-only assistant steps."""
    return [
        MessageOut(
            id=row.id,
            role=_VISIBLE_ROLES[row.role],  # type: ignore[arg-type]
            text=row.content,
            author=row.data.get("author"),
            turn_id=row.turn_id,
            created_at=row.created_at,
        )
        for row in rows
        if row.role in _VISIBLE_ROLES and row.content
    ]
