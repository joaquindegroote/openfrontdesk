import json
from collections.abc import Sequence
from typing import Any

from openai import AsyncOpenAI

from openfrontdesk.llm.base import INVALID_JSON_KEY
from openfrontdesk.llm.types import ChatMessage, Completion, ToolCall, ToolSpec, Usage


class OpenAIChatClient:
    """Chat Completions adapter. Works with OpenAI and any compatible endpoint (via base_url)."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: AsyncOpenAI | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.model = model
        self._client = client or AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=2
        )

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        json_mode: bool = False,
    ) -> Completion:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_openai(m) for m in messages],
        }
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        if json_mode:
            request["response_format"] = {"type": "json_object"}

        response = await self._client.chat.completions.create(**request)
        message = response.choices[0].message
        calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=_parse_arguments(call.function.arguments),
            )
            for call in message.tool_calls or []
            if getattr(call, "function", None) is not None
        ]
        usage = (
            Usage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )
            if response.usage
            else Usage()
        )
        return Completion(text=message.content, tool_calls=calls, usage=usage, model=response.model)


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {INVALID_JSON_KEY: raw}
    return parsed if isinstance(parsed, dict) else {INVALID_JSON_KEY: raw}


def _to_openai(message: ChatMessage) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content or "",
        }
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    return payload
