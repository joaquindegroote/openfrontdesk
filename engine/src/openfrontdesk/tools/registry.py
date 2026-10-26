"""Typed tools: a Pydantic model defines the arguments, its JSON schema is what the model sees,
and validation errors go back to the model instead of reaching the booking system."""

import inspect
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import BaseModel

from openfrontdesk.agent.context import SideEffect, ToolContext
from openfrontdesk.guardrails.base import Guard
from openfrontdesk.llm import ToolSpec

Handler = Callable[[ToolContext, Any], Awaitable[Any]]
Describe = Callable[[ToolContext, Any], str]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    handler: Handler
    side_effect: SideEffect = "read"
    guards: tuple[Guard, ...] = ()
    # One-line summary of a proposed write, shown to the judge and kept in the audit trail.
    describe: Describe | None = None

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name, description=self.description, parameters=_schema(self.params)
        )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def tool(
        self,
        *,
        side_effect: SideEffect = "read",
        guards: Sequence[Guard] = (),
        describe: Describe | None = None,
        name: str | None = None,
    ) -> Callable[[Handler], Handler]:
        """Register `async def handler(ctx, params: Model)`; the docstring is the description."""

        def decorator(handler: Handler) -> Handler:
            params = get_type_hints(handler).get("params")
            if not (inspect.isclass(params) and issubclass(params, BaseModel)):
                raise TypeError(f"{handler.__name__}: annotate `params` with a Pydantic model")
            self.add(
                Tool(
                    name=name or handler.__name__,
                    description=inspect.getdoc(handler) or "",
                    params=params,
                    handler=handler,
                    side_effect=side_effect,
                    guards=tuple(guards),
                    describe=describe,
                )
            )
            return handler

        return decorator

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools.values()]

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    _strip_titles(schema)
    return schema


def _strip_titles(node: Any) -> None:
    """Drop Pydantic's auto-generated titles: they cost tokens and add nothing for the model."""
    if isinstance(node, dict):
        if isinstance(node.get("title"), str):
            del node["title"]
        for value in node.values():
            _strip_titles(value)
    elif isinstance(node, list):
        for item in node:
            _strip_titles(item)
