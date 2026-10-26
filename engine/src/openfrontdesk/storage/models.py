from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC on every backend (SQLite drops the offset)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UTCDateTime,
        dict[str, Any]: JSON,
        list[Any]: JSON,
    }


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(80))
    channel: Mapped[str] = mapped_column(String(20))
    # active: the assistant replies. handed_off: staff replies until they hand it back.
    status: Mapped[str] = mapped_column(String(20), index=True)
    customer_phone: Mapped[str | None] = mapped_column(String(32))
    customer_name: Mapped[str | None] = mapped_column(String(120))
    handoff_reason: Mapped[str | None] = mapped_column(String(40))
    handoff_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(index=True)


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(40))
    # user | assistant | tool | staff
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str | None] = mapped_column(Text)
    # tool_calls, tool_call_id and name for the model; author for staff messages.
    data: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]


class ToolEventRow(Base):
    __tablename__ = "tool_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    turn_id: Mapped[str] = mapped_column(String(40))
    call_id: Mapped[str] = mapped_column(String(80))
    tool: Mapped[str] = mapped_column(String(80))
    side_effect: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    arguments: Mapped[dict[str, Any]]
    result: Mapped[Any] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    checks: Mapped[list[Any]]
    duration_ms: Mapped[int]
    created_at: Mapped[datetime]


class TurnRow(Base):
    __tablename__ = "turns"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    model: Mapped[str | None] = mapped_column(String(80))
    steps: Mapped[int]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    latency_ms: Mapped[int]
    # replied | handed_off | failed
    outcome: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime]
