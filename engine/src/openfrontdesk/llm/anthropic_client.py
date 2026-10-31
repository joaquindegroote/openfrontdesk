"""Messages API adapter for Claude models.

Thinking blocks are replayed verbatim within a turn, as the API expects alongside tool results,
but never across turns. The system prompt carries the time and the customer's appointments, so
it changes every turn, and a changed prefix invalidates earlier blocks. They travel only on the
in-memory messages of the turn that produced them (`ChatMessage.provider_content`), which is
never stored, so every later turn starts from a history the API accepts.
"""

import json
from collections.abc import Sequence
from typing import Any

from anthropic import AsyncAnthropic

from openfrontdesk.llm.base import INVALID_JSON_KEY, ModelRefusedError
from openfrontdesk.llm.types import ChatMessage, Completion, ToolCall, ToolSpec, Usage

# On a refusal, the API re-runs the request on a fallback model it picks by refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
# Shared effort names to Claude's: there is no "minimal", "low" is the lightest.
EFFORT = {"minimal": "low", "low": "low", "medium": "medium", "high": "high"}
# Response blocks sent back on the next step of the same turn.
REPLAYED_BLOCKS = {"thinking", "redacted_thinking", "text", "tool_use"}
_JSON = json.JSONDecoder()


class AnthropicChatClient:
    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        client: AsyncAnthropic | None = None,
        timeout_s: float = 60.0,
        effort: str | None = None,
        max_tokens: int = 16000,
    ) -> None:
        self.model = model
        self.effort = EFFORT.get(effort, effort) if effort else None
        self.max_tokens = max_tokens
        self._client = client or AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=2)

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        json_mode: bool = False,
    ) -> Completion:
        system, conversation = _split_system(messages)
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": _to_anthropic(conversation),
            # Tools, system prompt and earlier steps repeat on every step of a turn.
            "cache_control": {"type": "ephemeral"},
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        if self.effort:
            request["output_config"] = {"effort": self.effort}

        if self.model.startswith(FALLBACK_MODELS):
            response = await self._client.beta.messages.create(
                **request, betas=[FALLBACK_BETA], fallbacks="default"
            )
        else:
            response = await self._client.messages.create(**request)
        if response.stop_reason == "refusal":
            raise ModelRefusedError(f"{response.model} declined to answer")

        text = "".join(b.text for b in response.content if b.type == "text") or None
        if json_mode and text:
            text = _json_object(text)
        calls = [
            ToolCall(
                id=b.id,
                name=b.name,
                arguments=b.input if isinstance(b.input, dict) else {INVALID_JSON_KEY: b.input},
            )
            for b in response.content
            if b.type == "tool_use"
        ]
        usage = response.usage
        cached = (usage.cache_read_input_tokens or 0) + (usage.cache_creation_input_tokens or 0)
        return Completion(
            text=text,
            tool_calls=calls,
            # Counted like OpenAI's prompt tokens: cached input included.
            usage=Usage(
                input_tokens=usage.input_tokens + cached, output_tokens=usage.output_tokens
            ),
            model=response.model,
            provider_content=[
                b.model_dump(mode="json", exclude_none=True)
                for b in response.content
                if b.type in REPLAYED_BLOCKS
            ],
        )


def _split_system(messages: Sequence[ChatMessage]) -> tuple[str, list[ChatMessage]]:
    """Leading system messages become the top-level system prompt."""
    leading = 0
    while leading < len(messages) and messages[leading].role == "system":
        leading += 1
    system = "\n\n".join(m.content or "" for m in messages[:leading])
    return system, list(messages[leading:])


def _to_anthropic(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            result = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content or "",
            }
            # Every result of one step goes back in a single user message.
            previous = converted[-1] if converted else None
            if previous and previous["role"] == "user" and _is_tool_results(previous["content"]):
                previous["content"].append(result)
            else:
                converted.append({"role": "user", "content": [result]})
        elif message.role == "assistant":
            converted.append({"role": "assistant", "content": _assistant_blocks(message)})
        else:
            converted.append({"role": message.role, "content": message.content or ""})
    return converted


def _assistant_blocks(message: ChatMessage) -> list[dict[str, Any]]:
    if message.provider_content:
        return message.provider_content
    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    blocks += [
        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        for call in message.tool_calls
    ]
    return blocks


def _is_tool_results(content: Any) -> bool:
    return isinstance(content, list) and bool(content) and content[0]["type"] == "tool_result"


def _json_object(text: str) -> str:
    """The last JSON object in a reply, or the reply as it came if there is none.

    json_mode callers ask for JSON only, but Claude may still fence it, or reason in prose
    before or after it. A reply without an object reaches the caller unchanged, so its parse
    fails there and the caller's own failure handling applies.
    """
    found = None
    start = text.find("{")
    while start != -1:
        try:
            _, end = _JSON.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        found = text[start:end]
        start = text.find("{", end)
    return found if found is not None else text
