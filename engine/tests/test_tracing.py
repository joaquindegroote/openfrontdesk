"""Traces: what a conversation records, and what OFD_TRACE_CONTENT=false leaves out."""

from collections.abc import Iterator
from typing import Any

import pytest
from openfrontdesk import observability
from openfrontdesk.llm.scripted import reply, tool_call
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from support import PRIYA, Harness

EXPORTER = InMemorySpanExporter()
# A process gets one global provider; the engine's tracer picks it up from here on.
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)

MESSAGE = "Hi, it's Priya Shah. Anything for a check-up this week?"


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    EXPORTER.clear()
    yield EXPORTER
    observability.configure_tracing(None)  # content back on for the other tests


def converse(harness: Harness) -> list[dict[str, Any]]:
    conversation = harness.start(PRIYA)
    harness.llm.extend([tool_call("find_customer", phone=PRIYA), reply("Hi Priya! Let me look.")])
    harness.say(conversation, MESSAGE)
    return [dict(span.attributes or {}) for span in EXPORTER.get_finished_spans()]


def test_spans_carry_the_conversation_by_default(harness: Harness, spans: Any) -> None:
    attributes = converse(harness)

    kinds = {a.get(observability.SPAN_KIND) for a in attributes}
    assert {"AGENT", "LLM", "TOOL", "GUARDRAIL"} <= kinds
    assert any(a.get(observability.INPUT) == MESSAGE for a in attributes)
    [lookup] = [a for a in attributes if a.get(observability.TOOL_NAME) == "find_customer"]
    assert PRIYA in lookup[observability.INPUT]


def test_content_can_be_left_out(harness: Harness, spans: Any) -> None:
    observability.configure_tracing(None, content=False)
    attributes = converse(harness)

    # Still a full trace: every kind of span, with statuses and guard verdicts.
    kinds = {a.get(observability.SPAN_KIND) for a in attributes}
    assert {"AGENT", "LLM", "TOOL", "GUARDRAIL"} <= kinds
    assert any(a.get("tool.status") == "ok" for a in attributes)
    assert any(a.get("guardrail.passed") is True for a in attributes)
    # And nothing the customer wrote, or the tools said about them.
    assert not any(observability.INPUT in a or observability.OUTPUT in a for a in attributes)
    recorded = " ".join(str(value) for a in attributes for value in a.values())
    assert "Priya" not in recorded
    assert PRIYA not in recorded
