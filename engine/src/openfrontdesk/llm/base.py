from collections.abc import Sequence
from typing import Protocol

from openfrontdesk.llm.types import ChatMessage, Completion, ToolSpec

# Key in ToolCall.arguments when the model produced arguments that aren't a JSON object.
INVALID_JSON_KEY = "__invalid_json__"


class ModelRefusedError(Exception):
    """The model declined to answer. Raised rather than returning an empty reply, so the turn
    fails into a handoff instead of a blank message."""


class LLMClient(Protocol):
    model: str

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        json_mode: bool = False,
    ) -> Completion: ...
