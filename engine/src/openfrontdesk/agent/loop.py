"""The agent loop for one customer turn: think, call tools, repeat, reply.

Bounded on purpose: a step budget, automatic handoff when the booking system keeps failing,
and a final reply even when the model or the tools misbehave.
"""

import json
from dataclasses import dataclass, field

from openfrontdesk.agent.context import HandoffRequest, ToolContext, ToolEvent
from openfrontdesk.agent.executor import ToolExecutor
from openfrontdesk.llm import ChatMessage, Completion, LLMClient, ToolSpec, Usage
from openfrontdesk.observability import (
    COMPLETION_TOKENS,
    INPUT,
    MODEL,
    OUTPUT,
    PROMPT_TOKENS,
    SESSION_ID,
    SPAN_KIND,
    tracer,
)
from openfrontdesk.tools import ToolRegistry

HANDOFF_REPLY = (
    "I've passed this to our front-desk team and someone will reply here shortly. "
    "Thanks for your patience."
)
OUTAGE_NOTE = (
    "The booking system is not responding. A staff member will take over this conversation. "
    "Apologize briefly and tell the customer someone will follow up here soon. No tools."
)
# Consecutive steps in which every tool call failed with a temporary error.
MAX_FAILING_STEPS = 2


@dataclass
class TurnResult:
    reply: str
    new_messages: list[ChatMessage]
    events: list[ToolEvent]
    handoff: HandoffRequest | None
    usage: Usage
    steps: int
    model: str | None = None
    errors: list[str] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        *,
        executor: ToolExecutor | None = None,
        max_steps: int = 8,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.executor = executor or ToolExecutor(registry)
        self.max_steps = max_steps

    async def run_turn(
        self,
        *,
        system_prompt: str,
        history: list[ChatMessage],
        user_message: ChatMessage,
        ctx: ToolContext,
    ) -> TurnResult:
        messages = [ChatMessage.system(system_prompt), *history, user_message]
        ctx.transcript = [*history, user_message]
        turn = TurnResult(
            reply="", new_messages=[user_message], events=[], handoff=None, usage=Usage(), steps=0
        )
        specs = self.registry.specs()
        failing_steps = 0

        with tracer.start_as_current_span("agent turn") as span:
            span.set_attribute(SPAN_KIND, "AGENT")
            span.set_attribute(SESSION_ID, ctx.conversation_id)
            span.set_attribute(INPUT, user_message.content or "")

            for step in range(1, self.max_steps + 1):
                turn.steps = step
                # After a handoff the model only writes the closing message, no more tools.
                completion = await self._complete(
                    messages, [] if turn.handoff else specs, ctx.conversation_id
                )
                turn.usage += completion.usage
                turn.model = completion.model or self.llm.model
                assistant = ChatMessage(
                    role="assistant", content=completion.text, tool_calls=completion.tool_calls
                )
                if turn.handoff or not completion.tool_calls:
                    assistant.tool_calls = []
                    assistant.content = (completion.text or "").strip() or (
                        HANDOFF_REPLY if turn.handoff else "Sorry, could you say that again?"
                    )
                    self._append(assistant, messages, turn, ctx)
                    turn.reply = assistant.content
                    span.set_attribute(OUTPUT, turn.reply)
                    return turn

                self._append(assistant, messages, turn, ctx)
                temporary_failures = 0
                for call in completion.tool_calls:
                    event, handoff = await self.executor.execute(call, ctx)
                    ctx.events.append(event)
                    turn.events.append(event)
                    self._append(
                        ChatMessage(
                            role="tool",
                            tool_call_id=call.id,
                            name=call.name,
                            content=json.dumps(event.result, default=str),
                        ),
                        messages,
                        turn,
                        ctx,
                    )
                    if event.status == "error" and event.result.get("retryable"):
                        temporary_failures += 1
                    turn.handoff = turn.handoff or handoff

                all_failed = temporary_failures == len(completion.tool_calls)
                failing_steps = failing_steps + 1 if all_failed else 0
                if failing_steps >= MAX_FAILING_STEPS and turn.handoff is None:
                    turn.handoff = HandoffRequest(
                        reason="system_unavailable",
                        summary="The booking system kept failing while helping this customer.",
                        automatic=True,
                    )
                    # Steers the closing message; not persisted.
                    messages.append(ChatMessage.system(OUTAGE_NOTE))

            # Out of steps: stop here rather than loop, and let a person take it from here.
            turn.handoff = turn.handoff or HandoffRequest(
                reason="step_limit",
                summary="The assistant could not finish this request within its step budget.",
                automatic=True,
            )
            closing = ChatMessage.assistant(HANDOFF_REPLY)
            self._append(closing, messages, turn, ctx)
            turn.reply = HANDOFF_REPLY
            span.set_attribute(OUTPUT, turn.reply)
            return turn

    async def _complete(
        self, messages: list[ChatMessage], tools: list[ToolSpec], conversation_id: str
    ) -> Completion:
        with tracer.start_as_current_span("llm chat") as span:
            span.set_attribute(SPAN_KIND, "LLM")
            span.set_attribute(SESSION_ID, conversation_id)
            span.set_attribute(MODEL, self.llm.model)
            span.set_attribute(INPUT, messages[-1].content or "")
            completion = await self.llm.complete(messages, tools=tools)
            span.set_attribute(PROMPT_TOKENS, completion.usage.input_tokens)
            span.set_attribute(COMPLETION_TOKENS, completion.usage.output_tokens)
            span.set_attribute(
                OUTPUT, completion.text or ", ".join(c.name for c in completion.tool_calls)
            )
            return completion

    @staticmethod
    def _append(
        message: ChatMessage, messages: list[ChatMessage], turn: TurnResult, ctx: ToolContext
    ) -> None:
        messages.append(message)
        turn.new_messages.append(message)
        ctx.transcript.append(message)
