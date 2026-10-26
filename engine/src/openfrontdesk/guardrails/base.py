"""Propose, verify, execute: every write the model proposes is checked before it reaches the
booking system. Deterministic rules run first; the LLM judge only sees actions that pass them."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from openfrontdesk.agent.context import CheckResult, ToolContext
from openfrontdesk.observability import INPUT, OUTPUT, SPAN_KIND, tracer


@dataclass(frozen=True)
class ProposedAction:
    tool: str
    params: BaseModel
    summary: str

    @property
    def arguments(self) -> dict[str, Any]:
        return self.params.model_dump(mode="json")


class Guard(Protocol):
    name: str
    kind: Literal["rule", "judge"]

    async def check(self, ctx: ToolContext, action: ProposedAction) -> CheckResult: ...


async def evaluate(
    guards: Sequence[Guard], ctx: ToolContext, action: ProposedAction
) -> list[CheckResult]:
    """Run guards in order (rules before judges), stopping at the first failure."""
    results: list[CheckResult] = []
    for guard in sorted(guards, key=lambda g: g.kind != "rule"):
        with tracer.start_as_current_span(f"guard {guard.name}") as span:
            span.set_attribute(SPAN_KIND, "GUARDRAIL")
            span.set_attribute(INPUT, action.summary)
            result = await guard.check(ctx, action)
            span.set_attribute(OUTPUT, f"{'pass' if result.passed else 'fail'}: {result.detail}")
        results.append(result)
        if not result.passed:
            break
    return results
