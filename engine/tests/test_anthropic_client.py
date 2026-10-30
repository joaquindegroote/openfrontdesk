"""The Claude adapter against a fake SDK client: the requests it builds and how it reads replies."""

from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types import Message
from openfrontdesk.llm import ChatMessage, Completion, ToolCall, ToolSpec
from openfrontdesk.llm.anthropic_client import FALLBACK_BETA, AnthropicChatClient
from openfrontdesk.llm.base import ModelRefusedError
from openfrontdesk.llm.scripted import reply
from support import PRIYA, Harness

THINKING = {"type": "thinking", "thinking": "", "signature": "sig-1"}
TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "list_services", "input": {}}
SERVICES = ToolSpec(
    name="list_services",
    description="List the services offered.",
    parameters={"type": "object", "properties": {}},
)


class FakeMessages:
    def __init__(self, responses: list[Message]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def create(self, **request: Any) -> Message:
        self.requests.append(request)
        return self.responses.pop(0)


def fake(*responses: Message) -> tuple[Any, FakeMessages]:
    messages = FakeMessages(list(responses))
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages)), messages


def message(
    *content: dict[str, Any], stop_reason: str = "end_turn", cache_read: int = 0
) -> Message:
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": list(content),
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": 0,
            },
        }
    )


async def test_requests_use_the_messages_api_shape() -> None:
    client, sdk = fake(message({"type": "text", "text": "Hi!"}))
    llm = AnthropicChatClient("claude-haiku-5-5", client=client, effort="minimal")
    await llm.complete(
        [
            ChatMessage.system("You are Ava."),
            ChatMessage.user("Book a cleaning"),
            ChatMessage(
                role="assistant",
                content="Let me check.",
                tool_calls=[
                    ToolCall(id="call_a", name="list_services"),
                    ToolCall(id="call_b", name="find_customer", arguments={"phone": "+1"}),
                ],
            ),
            ChatMessage(role="tool", tool_call_id="call_a", content='{"services": []}'),
            ChatMessage(role="tool", tool_call_id="call_b", content='{"found": false}'),
            ChatMessage.assistant("[Dana, front-desk staff] Hi, I can help."),
            ChatMessage.user("Thanks"),
        ],
        tools=[SERVICES],
    )

    [request] = sdk.requests
    assert request["system"] == "You are Ava."
    assert request["output_config"] == {"effort": "low"}  # Claude has no "minimal"
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["tools"] == [
        {
            "name": "list_services",
            "description": "List the services offered.",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    assert "betas" not in request
    assert request["messages"] == [
        {"role": "user", "content": "Book a cleaning"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "call_a", "name": "list_services", "input": {}},
                {
                    "type": "tool_use",
                    "id": "call_b",
                    "name": "find_customer",
                    "input": {"phone": "+1"},
                },
            ],
        },
        # Both results of the step go back in one user message.
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_a", "content": '{"services": []}'},
                {"type": "tool_result", "tool_use_id": "call_b", "content": '{"found": false}'},
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "[Dana, front-desk staff] Hi, I can help."}],
        },
        {"role": "user", "content": "Thanks"},
    ]


async def test_thinking_blocks_go_back_unchanged_with_the_tool_results() -> None:
    client, sdk = fake(message(THINKING, TOOL_USE), message({"type": "text", "text": "Done."}))
    llm = AnthropicChatClient("claude-haiku-5-5", client=client)
    first = await llm.complete([ChatMessage.user("What do you offer?")], tools=[SERVICES])

    assert first.tool_calls == [ToolCall(id="toolu_1", name="list_services", arguments={})]
    assert first.provider_content == [THINKING, TOOL_USE]
    step = ChatMessage(
        role="assistant", tool_calls=first.tool_calls, provider_content=first.provider_content
    )
    result = ChatMessage(role="tool", tool_call_id="toolu_1", content="{}")
    await llm.complete([ChatMessage.user("What do you offer?"), step, result], tools=[SERVICES])

    assert sdk.requests[1]["messages"][1] == {"role": "assistant", "content": [THINKING, TOOL_USE]}


async def test_replies_are_read_by_block_type_with_cached_tokens_counted() -> None:
    client, _ = fake(message(THINKING, {"type": "text", "text": "Hello"}, cache_read=900))
    completion = await AnthropicChatClient("claude-haiku-5-5", client=client).complete(
        [ChatMessage.user("Hi")]
    )

    assert completion.text == "Hello"
    assert completion.tool_calls == []
    assert (completion.usage.input_tokens, completion.usage.output_tokens) == (1000, 20)
    assert completion.model == "claude-haiku-5-5"


async def test_json_mode_unwraps_a_fenced_reply() -> None:
    fenced = '```json\n{"confirmed": true, "evidence": "yes"}\n```'
    client, _ = fake(message({"type": "text", "text": fenced}))
    completion = await AnthropicChatClient("claude-opus-5-5", client=client).complete(
        [ChatMessage.system("Reply with JSON only."), ChatMessage.user("...")], json_mode=True
    )
    assert completion.text == '{"confirmed": true, "evidence": "yes"}'


async def test_a_refusal_raises_instead_of_returning_an_empty_reply() -> None:
    client, _ = fake(message(stop_reason="refusal"))
    with pytest.raises(ModelRefusedError):
        await AnthropicChatClient("claude-haiku-5-5", client=client).complete(
            [ChatMessage.user("...")]
        )


async def test_sonnet_and_opus_requests_opt_into_server_side_fallback() -> None:
    client, sdk = fake(message({"type": "text", "text": "Hi"}))
    await AnthropicChatClient("claude-sonnet-5-5", client=client).complete([ChatMessage.user("Hi")])
    assert sdk.requests[0]["betas"] == [FALLBACK_BETA]
    assert sdk.requests[0]["fallbacks"] == "default"


def test_provider_content_lives_only_within_its_turn(harness: Harness) -> None:
    """Replayed on the next step of the same turn, never stored for the next turn."""
    conversation = harness.start(PRIYA)
    harness.llm.extend(
        [
            Completion(
                tool_calls=[ToolCall(id="toolu_1", name="list_services")],
                provider_content=[THINKING, TOOL_USE],
            ),
            reply("We offer check-ups, cleanings, fillings and whitening."),
        ]
    )
    harness.say(conversation, "What do you offer?")
    same_turn = [m.provider_content for m in harness.llm.calls[1].messages if m.role == "assistant"]
    assert same_turn == [[THINKING, TOOL_USE]]

    harness.llm.extend([reply("Anything else I can help with?")])
    harness.say(conversation, "Thanks")
    assert all(m.provider_content is None for m in harness.llm.calls[2].messages)
