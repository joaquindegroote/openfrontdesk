"""Conversation lifecycle: who replies (assistant or staff), and what gets persisted."""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from openfrontdesk.agent.context import HandoffRequest, ToolContext, ToolEvent
from openfrontdesk.agent.loop import HANDOFF_REPLY, Agent, TurnResult
from openfrontdesk.agent.prompt import BookingSnapshot, render_system_prompt
from openfrontdesk.backends import Appointment, BookingBackend, Customer, Service
from openfrontdesk.business import BusinessProfile
from openfrontdesk.ids import new_id
from openfrontdesk.llm import ChatMessage, Usage
from openfrontdesk.storage import ConversationRepository
from openfrontdesk.storage.models import ConversationRow, MessageRow, TurnRow
from openfrontdesk.storage.repository import to_chat_message

logger = logging.getLogger(__name__)

ACTIVE = "active"
HANDED_OFF = "handed_off"
SERVICES_TTL_S = 300
# Result stored for a tool call a failed turn never finished, so the history stays valid.
INTERRUPTED = {
    "error": "interrupted",
    "message": "The turn ended before this tool finished, so its outcome is unknown.",
}


class ConversationNotFoundError(Exception):
    pass


class AssistantNotConfiguredError(Exception):
    pass


@dataclass
class TurnOutcome:
    conversation: ConversationRow
    messages: list[MessageRow]
    events: list[ToolEvent] = field(default_factory=list)
    turn: TurnRow | None = None


class ConversationService:
    def __init__(
        self,
        *,
        repo: ConversationRepository,
        agent: Agent | None,
        business: BusinessProfile,
        backend: BookingBackend,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = repo
        self.agent = agent
        self.business = business
        self.backend = backend
        self.clock = clock or (lambda: datetime.now(business.tz))
        # Serializes turns per conversation within this process.
        self._locks: dict[str, asyncio.Lock] = {}
        self._services: tuple[float, list[Service]] | None = None

    async def start(
        self, *, channel: str, customer_phone: str | None, customer_name: str | None
    ) -> ConversationRow:
        return await self.repo.create(
            business_id=self.business.id,
            channel=channel,
            customer_phone=customer_phone,
            customer_name=customer_name,
        )

    async def get(self, conversation_id: str) -> ConversationRow:
        conversation = await self.repo.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        return conversation

    async def handle_customer_message(self, conversation_id: str, text: str) -> TurnOutcome:
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            conversation = await self.get(conversation_id)
            if conversation.status != ACTIVE:
                # Staff own the conversation: store the message for them, the assistant stays out.
                stored = await self.repo.add_message(conversation_id, role="user", content=text)
                return TurnOutcome(conversation=await self.get(conversation_id), messages=[stored])
            if self.agent is None:
                raise AssistantNotConfiguredError(
                    "No LLM configured. Set OPENAI_API_KEY and restart the engine."
                )
            return await self._run_assistant(conversation, text)

    async def staff_reply(self, conversation_id: str, text: str, author: str) -> MessageRow:
        conversation = await self.get(conversation_id)
        if conversation.status != HANDED_OFF:
            await self.take_over(conversation_id, reason="staff_takeover", summary=None)
        return await self.repo.add_message(
            conversation_id, role="staff", content=text, data={"author": author}
        )

    async def take_over(
        self, conversation_id: str, *, reason: str, summary: str | None
    ) -> ConversationRow:
        await self.get(conversation_id)
        return await self.repo.update(
            conversation_id, status=HANDED_OFF, handoff_reason=reason, handoff_summary=summary
        )

    async def resume_assistant(self, conversation_id: str) -> ConversationRow:
        await self.get(conversation_id)
        return await self.repo.update(
            conversation_id, status=ACTIVE, handoff_reason=None, handoff_summary=None
        )

    async def _run_assistant(self, conversation: ConversationRow, text: str) -> TurnOutcome:
        assert self.agent is not None
        history = [to_chat_message(m) for m in await self.repo.messages(conversation.id)]
        now = self.clock()
        ctx = ToolContext(
            conversation_id=conversation.id,
            business=self.business,
            backend=self.backend,
            now=now,
            customer_phone=conversation.customer_phone,
            events=await self.repo.events(conversation.id),
        )
        prior_events = len(ctx.events)
        started = time.perf_counter()
        try:
            result = await self.agent.run_turn(
                system_prompt=render_system_prompt(
                    self.business,
                    now=now,
                    customer_phone=conversation.customer_phone,
                    snapshot=await self._snapshot(conversation.customer_phone),
                ),
                history=history,
                user_message=ChatMessage.user(text),
                ctx=ctx,
            )
            outcome = "handed_off" if result.handoff else "replied"
        except Exception:
            # The customer must never be left without an answer: hand off instead.
            logger.exception("assistant turn failed", extra={"conversation_id": conversation.id})
            result = _failed_turn(ctx, text, len(history), prior_events)
            outcome = "failed"

        turn = TurnRow(
            id=new_id("turn"),
            conversation_id=conversation.id,
            model=result.model,
            steps=result.steps,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000),
            outcome=outcome,
            created_at=datetime.now(UTC),
        )
        for event in result.events:
            event.turn_id = turn.id
        updates = (
            {
                "status": HANDED_OFF,
                "handoff_reason": result.handoff.reason,
                "handoff_summary": result.handoff.summary,
            }
            if result.handoff
            else {}
        )
        messages = await self.repo.save_turn(turn, result.new_messages, result.events, updates)
        return TurnOutcome(
            conversation=await self.get(conversation.id),
            messages=messages,
            events=result.events,
            turn=turn,
        )

    async def _snapshot(self, customer_phone: str | None) -> BookingSnapshot:
        """Prefetch what nearly every turn needs. Optional: failures leave it to the tools."""
        services, (customer, looked_up, appointments) = await asyncio.gather(
            self._cached_services(), self._lookup_customer(customer_phone)
        )
        return BookingSnapshot(
            services=services,
            customer=customer,
            customer_not_found=looked_up and customer is None,
            appointments=appointments,
        )

    async def _cached_services(self) -> list[Service]:
        now = time.monotonic()
        if self._services is not None and now - self._services[0] < SERVICES_TTL_S:
            return self._services[1]
        try:
            services = await self.backend.list_services()
        except Exception:
            logger.warning("could not prefetch services", exc_info=True)
            return []
        self._services = (now, services)
        return services

    async def _lookup_customer(
        self, phone: str | None
    ) -> tuple[Customer | None, bool, list[Appointment] | None]:
        """The customer behind the phone, whether the lookup worked, and their appointments."""
        if not phone:
            return None, False, None
        try:
            customer = await self.backend.find_customer(phone)
        except Exception:
            logger.warning("could not prefetch customer", exc_info=True)
            return None, False, None
        if customer is None:
            return None, True, None
        try:
            return customer, True, await self.backend.list_appointments(customer.id)
        except Exception:
            logger.warning("could not prefetch appointments", exc_info=True)
            return customer, True, None


def _failed_turn(ctx: ToolContext, text: str, history_length: int, prior_events: int) -> TurnResult:
    """Keep whatever the turn did before failing (tools may have run), then hand off."""
    done = _answer_open_tool_calls(ctx.transcript[history_length:]) or [ChatMessage.user(text)]
    return TurnResult(
        reply=HANDOFF_REPLY,
        new_messages=[*done, ChatMessage.assistant(HANDOFF_REPLY)],
        events=ctx.events[prior_events:],
        handoff=HandoffRequest(
            reason="assistant_error",
            summary="The assistant failed while handling this message.",
            automatic=True,
        ),
        usage=Usage(),
        steps=0,
    )


def _answer_open_tool_calls(messages: list[ChatMessage]) -> list[ChatMessage]:
    """A tool call without a result makes the stored history invalid for the model API, and
    every later turn would fail on it. Calls a failed turn left open get a placeholder."""
    answered = {m.tool_call_id for m in messages if m.role == "tool"}
    complete: list[ChatMessage] = []
    for message in messages:
        complete.append(message)
        for call in message.tool_calls if message.role == "assistant" else []:
            if call.id not in answered:
                complete.append(
                    ChatMessage(
                        role="tool",
                        tool_call_id=call.id,
                        name=call.name,
                        content=json.dumps(INTERRUPTED),
                    )
                )
    return complete
