from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from openfrontdesk.agent.context import ToolEvent
from openfrontdesk.ids import new_id
from openfrontdesk.llm import ChatMessage, ToolCall
from openfrontdesk.storage.db import Database
from openfrontdesk.storage.models import ConversationRow, MessageRow, ToolEventRow, TurnRow


class ConversationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        *,
        business_id: str,
        channel: str,
        customer_phone: str | None,
        customer_name: str | None,
    ) -> ConversationRow:
        now = datetime.now(UTC)
        row = ConversationRow(
            id=new_id("conv"),
            business_id=business_id,
            channel=channel,
            status="active",
            customer_phone=customer_phone,
            customer_name=customer_name,
            created_at=now,
            updated_at=now,
        )
        async with self.db.sessions.begin() as session:
            session.add(row)
        return row

    async def get(self, conversation_id: str) -> ConversationRow | None:
        async with self.db.sessions() as session:
            return await session.get(ConversationRow, conversation_id)

    async def list_conversations(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[ConversationRow]:
        query = select(ConversationRow).order_by(ConversationRow.updated_at.desc()).limit(limit)
        if status is not None:
            query = query.where(ConversationRow.status == status)
        async with self.db.sessions() as session:
            return list((await session.scalars(query)).all())

    async def update(self, conversation_id: str, **fields: Any) -> ConversationRow:
        async with self.db.sessions.begin() as session:
            row = await session.get(ConversationRow, conversation_id)
            if row is None:
                raise KeyError(conversation_id)
            for name, value in fields.items():
                setattr(row, name, value)
            row.updated_at = datetime.now(UTC)
        return row

    async def messages(self, conversation_id: str) -> list[MessageRow]:
        query = (
            select(MessageRow)
            .where(MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.id)
        )
        async with self.db.sessions() as session:
            return list((await session.scalars(query)).all())

    async def add_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        data: dict[str, Any] | None = None,
    ) -> MessageRow:
        row = MessageRow(
            conversation_id=conversation_id,
            role=role,
            content=content,
            data=data or {},
            created_at=datetime.now(UTC),
        )
        async with self.db.sessions.begin() as session:
            session.add(row)
            conversation = await session.get(ConversationRow, conversation_id)
            if conversation is not None:
                conversation.updated_at = row.created_at
        return row

    async def events(self, conversation_id: str) -> list[ToolEvent]:
        query = (
            select(ToolEventRow)
            .where(ToolEventRow.conversation_id == conversation_id)
            .order_by(ToolEventRow.created_at)
        )
        async with self.db.sessions() as session:
            rows = (await session.scalars(query)).all()
        return [_event_from_row(row) for row in rows]

    async def turns(self, conversation_id: str) -> list[TurnRow]:
        query = (
            select(TurnRow)
            .where(TurnRow.conversation_id == conversation_id)
            .order_by(TurnRow.created_at)
        )
        async with self.db.sessions() as session:
            return list((await session.scalars(query)).all())

    async def save_turn(
        self,
        turn: TurnRow,
        messages: list[ChatMessage],
        events: list[ToolEvent],
        conversation_updates: dict[str, Any],
    ) -> list[MessageRow]:
        """Persist everything a turn produced in one transaction."""
        now = datetime.now(UTC)
        message_rows = [_message_to_row(turn.conversation_id, turn.id, m, now) for m in messages]
        async with self.db.sessions.begin() as session:
            session.add(turn)
            session.add_all(message_rows)
            session.add_all(_event_to_row(turn.conversation_id, turn.id, e) for e in events)
            conversation = await session.get(ConversationRow, turn.conversation_id)
            if conversation is not None:
                for name, value in conversation_updates.items():
                    setattr(conversation, name, value)
                conversation.updated_at = now
        return message_rows


def to_chat_message(row: MessageRow) -> ChatMessage:
    """Rebuild the model's view of a stored message. Staff messages read as the front desk."""
    if row.role == "staff":
        author = row.data.get("author", "Front desk")
        return ChatMessage.assistant(f"[{author}, front-desk staff] {row.content}")
    return ChatMessage(
        role=row.role,  # type: ignore[arg-type]
        content=row.content,
        tool_calls=[ToolCall.model_validate(c) for c in row.data.get("tool_calls", [])],
        tool_call_id=row.data.get("tool_call_id"),
        name=row.data.get("name"),
    )


def _message_to_row(
    conversation_id: str, turn_id: str, message: ChatMessage, created_at: datetime
) -> MessageRow:
    data: dict[str, Any] = {}
    if message.tool_calls:
        data["tool_calls"] = [c.model_dump(mode="json") for c in message.tool_calls]
    if message.tool_call_id:
        data["tool_call_id"] = message.tool_call_id
    if message.name:
        data["name"] = message.name
    return MessageRow(
        conversation_id=conversation_id,
        turn_id=turn_id,
        role=message.role,
        content=message.content,
        data=data,
        created_at=created_at,
    )


def _event_to_row(conversation_id: str, turn_id: str, event: ToolEvent) -> ToolEventRow:
    return ToolEventRow(
        id=event.id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        call_id=event.call_id,
        tool=event.tool,
        side_effect=event.side_effect,
        status=event.status,
        arguments=event.arguments,
        result=event.result,
        summary=event.summary,
        checks=[c.model_dump() for c in event.checks],
        duration_ms=event.duration_ms,
        created_at=event.created_at,
    )


def _event_from_row(row: ToolEventRow) -> ToolEvent:
    return ToolEvent.model_validate(
        {
            "id": row.id,
            "call_id": row.call_id,
            "tool": row.tool,
            "side_effect": row.side_effect,
            "arguments": row.arguments,
            "status": row.status,
            "result": row.result,
            "summary": row.summary,
            "checks": row.checks,
            "duration_ms": row.duration_ms,
            "turn_id": row.turn_id,
            "created_at": row.created_at,
        }
    )
