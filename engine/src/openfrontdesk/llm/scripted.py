"""A fake LLM that replays a script. Makes agent behavior testable without network or cost."""

from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import count
from typing import Any

from openfrontdesk.llm.types import ChatMessage, Completion, ToolCall, ToolSpec

Step = Completion | Callable[[Sequence[ChatMessage]], Completion]

_call_ids = count(1)


@dataclass(frozen=True)
class ScriptedCall:
    messages: list[ChatMessage]
    tools: list[ToolSpec]
    json_mode: bool


class ScriptedLLM:
    def __init__(self, script: Iterable[Step] = (), *, model: str = "scripted") -> None:
        self.model = model
        self._script: deque[Step] = deque(script)
        self.calls: list[ScriptedCall] = []

    def extend(self, steps: Iterable[Step]) -> None:
        self._script.extend(steps)

    @property
    def remaining(self) -> int:
        return len(self._script)

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        json_mode: bool = False,
    ) -> Completion:
        self.calls.append(ScriptedCall(list(messages), list(tools), json_mode))
        if not self._script:
            raise AssertionError("ScriptedLLM was called more times than scripted")
        step = self._script.popleft()
        return step(messages) if callable(step) else step


def reply(text: str) -> Completion:
    return Completion(text=text, model="scripted")


def tool_call(name: str, **arguments: Any) -> Completion:
    return tool_calls((name, arguments))


def tool_calls(*calls: tuple[str, dict[str, Any]]) -> Completion:
    return Completion(
        tool_calls=[
            ToolCall(id=f"call_{next(_call_ids)}", name=name, arguments=arguments)
            for name, arguments in calls
        ],
        model="scripted",
    )
