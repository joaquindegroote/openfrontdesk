"""What the engine relies on from any booking system. An adapter for a real one (Cal.com, a
practice-management system) is ready when it passes these, whatever its own rules are: the
tests book what they need through the backend and never assume a seeded patient, a price or a
fee policy.

To put a new adapter under them, add a function like `clinic_api` below and list it in
BACKENDS.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import count

import httpx
import pytest
from clinic_api.chaos import ChaosConfig
from clinic_api.clock import FixedClock
from clinic_api.main import create_app as create_clinic_app
from openfrontdesk.backends import BackendError, BookingBackend, Customer, Slot
from openfrontdesk.backends.clinic_http import ClinicHttpBackend
from support import NOW, VERIFICATION_CODE


@dataclass
class World:
    """A backend to test, and the two things a test can't learn from it."""

    backend: BookingBackend
    now: datetime
    # A code its verification accepts here, where nobody receives a text.
    code: str


@asynccontextmanager
async def clinic_api() -> AsyncIterator[World]:
    clinic = create_clinic_app(
        FixedClock(NOW), chaos=ChaosConfig(), verification_code=VERIFICATION_CODE
    )
    transport = httpx.ASGITransport(app=clinic)
    async with httpx.AsyncClient(transport=transport, base_url="http://clinic") as http:
        yield World(ClinicHttpBackend(http, backoff_s=0), now=NOW, code=VERIFICATION_CODE)


BACKENDS: dict[str, Callable[[], AbstractAsyncContextManager[World]]] = {
    "clinic-api": clinic_api,
}
_numbers = count(1)


@pytest.fixture(params=list(BACKENDS))
async def world(request: pytest.FixtureRequest) -> AsyncIterator[World]:
    async with BACKENDS[request.param]() as ready:
        yield ready


async def a_customer(world: World) -> Customer:
    number = next(_numbers)
    return await world.backend.create_customer(f"Test Person {number}", f"+1555777{number:04d}")


async def open_slots(world: World, service_id: str) -> list[Slot]:
    today = world.now.date()
    return await world.backend.find_slots(service_id, today, today + timedelta(days=10))


async def a_service_and_two_slots(world: World) -> tuple[str, Slot, Slot]:
    """A service with no deposit, and two of its open slots on different days. Both are a few
    days out, where no business minds an appointment being moved or cancelled."""
    services = await world.backend.list_services()
    service = next(s for s in services if s.deposit == 0)
    slots = await open_slots(world, service.id)
    first = next(s for s in slots if s.start >= world.now + timedelta(days=2))
    second = next(s for s in slots if s.start >= first.end + timedelta(days=1))
    return service.id, first, second


def taken(slots: list[Slot], slot: Slot) -> bool:
    return not any(
        s.start == slot.start and s.practitioner_id == slot.practitioner_id for s in slots
    )


async def test_services_have_what_the_assistant_quotes(world: World) -> None:
    services = await world.backend.list_services()

    assert services
    assert len({s.id for s in services}) == len(services)
    for service in services:
        assert service.name
        assert service.duration_minutes > 0
        assert 0 <= service.deposit <= service.price


async def test_open_slots_are_in_the_future_in_range_and_in_order(world: World) -> None:
    [service, *_] = await world.backend.list_services()
    today = world.now.date()
    slots = await world.backend.find_slots(service.id, today, today + timedelta(days=3))

    assert slots
    assert [s.start for s in slots] == sorted(s.start for s in slots)
    for slot in slots:
        assert slot.start > world.now
        assert today <= slot.start.astimezone(world.now.tzinfo).date() <= today + timedelta(days=3)
        assert slot.end - slot.start == timedelta(minutes=service.duration_minutes)
        assert slot.practitioner_name


async def test_a_booking_takes_its_slot(world: World) -> None:
    service, slot, _ = await a_service_and_two_slots(world)
    customer = await a_customer(world)

    booked = await world.backend.book(
        customer.id, service, slot.start, slot.practitioner_id, idempotency_key="book-1"
    )

    assert (booked.customer_id, booked.service_id) == (customer.id, service)
    assert (booked.start, booked.practitioner_id) == (slot.start, slot.practitioner_id)
    assert booked.status == "confirmed"
    assert taken(await open_slots(world, service), slot)
    assert [a.id for a in await world.backend.list_appointments(customer.id)] == [booked.id]
    assert await world.backend.get_appointment(booked.id) == booked


async def test_a_taken_slot_is_refused_and_not_worth_retrying(world: World) -> None:
    service, slot, _ = await a_service_and_two_slots(world)
    first, second = await a_customer(world), await a_customer(world)
    await world.backend.book(
        first.id, service, slot.start, slot.practitioner_id, idempotency_key="book-1"
    )

    with pytest.raises(BackendError) as refused:
        await world.backend.book(
            second.id, service, slot.start, slot.practitioner_id, idempotency_key="book-2"
        )

    assert not refused.value.retryable
    # By this code the engine knows to tell the model to search again.
    assert refused.value.code == "slot_unavailable"
    assert await world.backend.list_appointments(second.id) == []


async def test_the_same_key_gives_the_same_booking(world: World) -> None:
    """A retry after a lost response must not book twice."""
    service, slot, _ = await a_service_and_two_slots(world)
    customer = await a_customer(world)

    first = await world.backend.book(
        customer.id, service, slot.start, slot.practitioner_id, idempotency_key="book-1"
    )
    again = await world.backend.book(
        customer.id, service, slot.start, slot.practitioner_id, idempotency_key="book-1"
    )

    assert again == first
    assert len(await world.backend.list_appointments(customer.id)) == 1


async def test_a_key_can_t_be_reused_for_something_else(world: World) -> None:
    service, slot, other = await a_service_and_two_slots(world)
    customer = await a_customer(world)
    await world.backend.book(
        customer.id, service, slot.start, slot.practitioner_id, idempotency_key="book-1"
    )

    with pytest.raises(BackendError) as refused:
        await world.backend.book(
            customer.id, service, other.start, other.practitioner_id, idempotency_key="book-1"
        )

    assert not refused.value.retryable
    assert len(await world.backend.list_appointments(customer.id)) == 1


async def test_moving_an_appointment_frees_the_old_time(world: World) -> None:
    service, slot, later = await a_service_and_two_slots(world)
    customer = await a_customer(world)
    booked = await world.backend.book(
        customer.id, service, slot.start, slot.practitioner_id, idempotency_key="book-1"
    )

    moved = await world.backend.reschedule(
        booked.id, later.start, later.practitioner_id, idempotency_key="move-1"
    )
    again = await world.backend.reschedule(
        booked.id, later.start, later.practitioner_id, idempotency_key="move-1"
    )

    assert (moved.id, moved.start) == (booked.id, later.start)
    assert again == moved
    slots = await open_slots(world, service)
    assert not taken(slots, slot)
    assert taken(slots, later)
    assert [a.start for a in await world.backend.list_appointments(customer.id)] == [later.start]


async def test_moving_onto_a_taken_time_changes_nothing(world: World) -> None:
    service, slot, later = await a_service_and_two_slots(world)
    first, second = await a_customer(world), await a_customer(world)
    mine = await world.backend.book(
        first.id, service, slot.start, slot.practitioner_id, idempotency_key="book-1"
    )
    await world.backend.book(
        second.id, service, later.start, later.practitioner_id, idempotency_key="book-2"
    )

    with pytest.raises(BackendError) as refused:
        await world.backend.reschedule(
            mine.id, later.start, later.practitioner_id, idempotency_key="move-1"
        )

    assert not refused.value.retryable
    assert await world.backend.get_appointment(mine.id) == mine


@pytest.mark.parametrize("notice", ["days of notice", "as late as it gets"])
async def test_a_cancellation_charges_what_was_quoted_and_once(world: World, notice: str) -> None:
    service, slot, _ = await a_service_and_two_slots(world)
    if notice == "as late as it gets":
        # The first open slot: where a business with a late fee would charge it.
        slot = (await open_slots(world, service))[0]
    customer = await a_customer(world)
    booked = await world.backend.book(
        customer.id, service, slot.start, slot.practitioner_id, idempotency_key="book-1"
    )

    quote = await world.backend.cancellation_quote(booked.id)
    result = await world.backend.cancel(booked.id, "can't make it", idempotency_key="cancel-1")
    again = await world.backend.cancel(booked.id, "can't make it", idempotency_key="cancel-1")

    # Whatever the business's policy is, the quote is the price.
    assert quote.late == (world.now > quote.free_cancellation_until)
    assert quote.fee >= 0 and (quote.late or quote.fee == 0)
    assert result.fee_charged == quote.fee
    assert again == result
    assert result.appointment.status == "cancelled"
    assert (await world.backend.get_appointment(booked.id)).status == "cancelled"
    assert not taken(await open_slots(world, service), slot)


async def test_a_customer_is_found_by_the_phone_they_registered(world: World) -> None:
    phone = "+15557770000"
    assert await world.backend.find_customer(phone) is None

    created = await world.backend.create_customer("Ana Lopez", phone)
    found = await world.backend.find_customer(phone)

    assert found == created
    assert (created.name, created.phone) == ("Ana Lopez", phone)
    assert await world.backend.list_appointments(created.id) == []


async def test_only_the_code_that_was_sent_verifies_a_phone(world: World) -> None:
    phone = "+15557770001"
    wrong = "000000" if world.code != "000000" else "111111"

    await world.backend.send_verification_code(phone)

    assert not await world.backend.check_verification_code(phone, wrong)
    assert await world.backend.check_verification_code(phone, world.code)


async def test_what_doesn_t_exist_is_an_error_not_an_empty_answer(world: World) -> None:
    """The assistant tells "there's nothing" from "I couldn't look" by this."""
    customer = await a_customer(world)
    today = world.now.date()
    tomorrow_9 = (world.now + timedelta(days=1)).replace(hour=9, minute=0)

    for call in (
        world.backend.get_appointment("no-such-appointment"),
        world.backend.cancellation_quote("no-such-appointment"),
        world.backend.find_slots("no-such-service", today, today),
        world.backend.book(customer.id, "no-such-service", tomorrow_9, idempotency_key="k"),
    ):
        with pytest.raises(BackendError) as refused:
            await call
        assert not refused.value.retryable
