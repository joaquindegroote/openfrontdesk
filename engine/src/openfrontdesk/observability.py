"""OpenTelemetry tracing with OpenInference attributes, so Arize Phoenix renders agent,
LLM, tool and guardrail spans natively. Without an endpoint, spans are no-ops."""

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SPAN_KIND = "openinference.span.kind"
INPUT = "input.value"
OUTPUT = "output.value"
MODEL = "llm.model_name"
PROMPT_TOKENS = "llm.token_count.prompt"
COMPLETION_TOKENS = "llm.token_count.completion"
TOOL_NAME = "tool.name"
SESSION_ID = "session.id"

tracer = trace.get_tracer("openfrontdesk")
_configured = False


def configure_tracing(
    otlp_endpoint: str | None, service_name: str = "openfrontdesk-engine"
) -> None:
    global _configured
    if _configured or not otlp_endpoint:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": service_name, "openinference.project.name": "openfrontdesk"}
        )
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True
