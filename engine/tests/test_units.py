"""Focused tests for the pieces the conversation tests rely on."""

from datetime import UTC, datetime

import httpx
import pytest
from clinic_api.chaos import ChaosConfig
from clinic_api.clock import FixedClock
from clinic_api.main import create_app as create_clinic_app
from openfrontdesk.agent.context import ToolContext, ToolEvent
from openfrontdesk.agent.executor import idempotency_key
from openfrontdesk.agent.prompt import render_system_prompt
from openfrontdesk.backends import BackendError
from openfrontdesk.backends.clinic_http import ClinicHttpBackend
from openfrontdesk.business import BusinessProfile
from openfrontdesk.llm.scripted import ScriptedLLM
from openfrontdesk.tools.booking import BookParams, build_booking_tools
from support import NOW, PROFILE

BUSINESS = BusinessProfile.load(PROFILE)


def test_tool_schemas_are_compact_and_typed() -> None:
    specs = {s.name: s for s in build_booking_tools(ScriptedLLM()).specs()}
    assert set(specs) == {
        "list_services",
        "find_available_slots",
        "find_customer",
        "register_customer",
        "list_customer_appointments",
        "book_appointment",
        "get_cancellation_quote",
        "cancel_appointment",
        "reschedule_appointment",
        "handoff_to_human",
    }
    booking = specs["book_appointment"].parameters
    assert booking["required"] == ["customer_id", "service_id", "start"]
    assert booking["properties"]["start"]["format"] == "date-time"
    assert "title" not in str(booking)
    assert specs["book_appointment"].description.startswith("Book an appointment.")


def _ctx(events: list[ToolEvent]) -> ToolContext:
    return ToolContext(
        conversation_id="conv_1",
        business=BUSINESS,
        backend=None,
        now=NOW,
        events=events,  # type: ignore[arg-type]
    )


def _event(status: str, arguments: dict) -> ToolEvent:
    return ToolEvent(
        id="evt",
        call_id="call",
        tool="book_appointment",
        side_effect="write",
        arguments=arguments,
        status=status,  # type: ignore[arg-type]
        result={},
        created_at=datetime.now(UTC),
    )


def test_idempotency_key_is_stable_across_retries_but_not_after_success() -> None:
    tool = build_booking_tools(ScriptedLLM()).get("book_appointment")
    assert tool is not None
    params = BookParams(
        customer_id="pat_0003", service_id="checkup", start=datetime(2026, 11, 3, 9, tzinfo=UTC)
    )
    arguments = params.model_dump(mode="json")

    first = idempotency_key(_ctx([]), tool, params)
    after_failure = idempotency_key(_ctx([_event("error", arguments)]), tool, params)
    after_success = idempotency_key(_ctx([_event("ok", arguments)]), tool, params)

    assert first == after_failure
    assert first != after_success


class LoseFirstResponse(httpx.AsyncBaseTransport):
    """The request reaches the server, but the response is lost (a timeout, from our side)."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = await self.inner.handle_async_request(request)
        if len(self.requests) == 1:
            raise httpx.ReadTimeout("response lost", request=request)
        return response


async def test_retried_booking_is_deduplicated_by_the_clinic() -> None:
    clinic = create_clinic_app(FixedClock(NOW), chaos=ChaosConfig(), filler=False)
    transport = LoseFirstResponse(httpx.ASGITransport(app=clinic))
    backend = ClinicHttpBackend(
        httpx.AsyncClient(transport=transport, base_url="http://clinic"), backoff_s=0
    )

    appointment = await backend.book(
        "pat_0003",
        "checkup",
        datetime.fromisoformat("2026-11-03T09:00:00-05:00"),
        idempotency_key="key-1",
    )

    assert len(transport.requests) == 2
    assert {r.headers["Idempotency-Key"] for r in transport.requests} == {"key-1"}
    booked = [a for a in clinic.state.store.appointments.values() if a.patient_id == "pat_0003"]
    assert [a.id for a in booked if a.status != "cancelled" and a.start > NOW] == [appointment.id]


async def test_writes_without_a_key_are_never_retried() -> None:
    clinic = create_clinic_app(FixedClock(NOW), chaos=ChaosConfig(error_rate=1.0), filler=False)
    transport = LoseFirstResponse(httpx.ASGITransport(app=clinic))
    backend = ClinicHttpBackend(
        httpx.AsyncClient(transport=transport, base_url="http://clinic"), backoff_s=0
    )
    with pytest.raises(BackendError) as error:
        await backend.create_customer("Ana Lopez", "+15550100199")
    assert error.value.code == "timeout"
    assert len(transport.requests) == 1


def test_system_prompt_includes_time_customer_and_policies() -> None:
    prompt = render_system_prompt(BUSINESS, now=NOW, customer_phone="+15550100101")
    assert "You are Ava, the front-desk assistant of BrightSmile Dental." in prompt
    assert "It is Monday, November 2, 2026, 8:00 AM (America/New_York)." in prompt
    assert "writing from +15550100101" in prompt
    assert "$50 fee" in prompt
    assert "Q: Is there parking?" in prompt

    anonymous = render_system_prompt(BUSINESS, now=NOW, customer_phone=None)
    assert "Ask for their phone number" in anonymous
