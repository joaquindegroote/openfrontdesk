"""Plays a simulated customer against the engine until the conversation ends."""

from dataclasses import dataclass, field
from typing import Literal

from openfrontdesk.agent.context import ToolEvent
from openfrontdesk.service import ConversationService

from ofd_evals.customer import SimulatedCustomer, SimulatorError, Speaker

StopReason = Literal["customer_done", "handed_off", "max_turns", "simulator_error"]


@dataclass
class TurnRecord:
    customer: str
    replies: list[str]
    events: list[ToolEvent]
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ConversationRecord:
    conversation_id: str
    turns: list[TurnRecord] = field(default_factory=list)
    stop_reason: StopReason = "max_turns"
    final_status: str = "active"
    error: str | None = None

    @property
    def events(self) -> list[ToolEvent]:
        return [event for turn in self.turns for event in turn.events]

    @property
    def tokens(self) -> int:
        return sum(t.input_tokens + t.output_tokens for t in self.turns)

    @property
    def latency_ms(self) -> int:
        return sum(t.latency_ms for t in self.turns)


async def run_conversation(
    service: ConversationService, customer: SimulatedCustomer, *, max_turns: int = 8
) -> ConversationRecord:
    conversation = await service.start(
        channel="web", customer_phone=customer.persona.phone, customer_name=None
    )
    record = ConversationRecord(conversation_id=conversation.id)
    transcript: list[tuple[Speaker, str]] = []

    for _ in range(max_turns):
        try:
            turn = await customer.next_turn(transcript)
        except SimulatorError as exc:
            record.stop_reason, record.error = "simulator_error", str(exc)
            return record
        if turn.done:
            record.stop_reason = "customer_done"
            return record

        outcome = await service.handle_customer_message(conversation.id, turn.message)
        replies = [m.content for m in outcome.messages if m.role == "assistant" and m.content]
        transcript.append(("customer", turn.message))
        transcript.extend(("assistant", reply) for reply in replies)
        record.turns.append(
            TurnRecord(
                customer=turn.message,
                replies=replies,
                events=outcome.events,
                latency_ms=outcome.turn.latency_ms if outcome.turn else 0,
                input_tokens=outcome.turn.input_tokens if outcome.turn else 0,
                output_tokens=outcome.turn.output_tokens if outcome.turn else 0,
            )
        )
        record.final_status = outcome.conversation.status
        if outcome.conversation.status != "active":
            record.stop_reason = "handed_off"
            return record

    record.stop_reason = "max_turns"
    return record
