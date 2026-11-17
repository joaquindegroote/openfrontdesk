"""Channels deliver a message more than once: a webhook is retried when the answer was slow.
With the channel's id for the message, the second delivery gets the first outcome back and
nothing runs again."""

import pytest
from openfrontdesk.llm.scripted import reply, tool_call
from support import EMILY, PRIYA, Harness, verdict


def test_a_message_delivered_twice_is_answered_once(harness: Harness) -> None:
    conversation = harness.start(PRIYA)
    harness.llm.extend(
        [
            tool_call(
                "book_appointment",
                customer_id="pat_0003",
                service_id="checkup",
                start="2026-11-03T09:00:00-05:00",
                practitioner_id="dr-chen",
            ),
            reply("You're booked for Tuesday at 9:00 AM. See you then!"),
        ]
    )
    harness.judge.extend([verdict(True, "Yes, book it")])

    first = harness.say(conversation, "Yes, book it", message_id="wamid.1")
    again = harness.say(conversation, "Yes, book it", message_id="wamid.1")

    assert first["events"][0]["status"] == "ok"
    assert (first["duplicate"], again["duplicate"]) == (False, True)
    # The same reply, booking event and turn come back, and neither model was asked again.
    assert {**again, "duplicate": False} == first
    assert (len(harness.llm.calls), len(harness.judge.calls)) == (2, 1)
    assert len(harness.store.patient_appointments("pat_0003")) == 1
    detail = harness.client.get(f"/v1/conversations/{conversation}").json()
    assert [m["role"] for m in detail["messages"]] == ["customer", "assistant"]
    assert len(detail["turns"]) == 1


def test_another_id_or_no_id_is_a_new_message(harness: Harness) -> None:
    conversation = harness.start(PRIYA)
    harness.llm.extend([reply("Which day?"), reply("Which day works?"), reply("Any day?")])

    outcomes = [
        harness.say(conversation, "A check-up, please.", message_id="wamid.1"),
        harness.say(conversation, "A check-up, please.", message_id="wamid.2"),
        # Without an id there's nothing to compare, as on the web chat.
        harness.say(conversation, "A check-up, please."),
    ]

    assert [o["duplicate"] for o in outcomes] == [False, False, False]
    assert len(harness.llm.calls) == 3


def test_staff_don_t_get_the_same_message_twice(harness: Harness) -> None:
    conversation = harness.start(EMILY)
    harness.client.post(f"/v1/conversations/{conversation}/takeover", json={})

    first = harness.say(conversation, "Hello?", message_id="wamid.7")
    again = harness.say(conversation, "Hello?", message_id="wamid.7")

    assert (first["duplicate"], again["duplicate"]) == (False, True)
    assert again["messages"] == first["messages"]
    detail = harness.client.get(f"/v1/conversations/{conversation}").json()
    assert [m["text"] for m in detail["messages"]] == ["Hello?"]


def test_a_crash_after_the_booking_and_before_the_save_books_once(harness: Harness) -> None:
    """The worst moment to die: the clinic has the appointment and the engine has no record of
    the turn. The channel delivers the message again, the turn runs again, and the booking
    goes out with the same idempotency key, so the clinic answers with the first one."""
    conversation = harness.start(PRIYA)
    booking = tool_call(
        "book_appointment",
        customer_id="pat_0003",
        service_id="checkup",
        start="2026-11-03T09:00:00-05:00",
        practitioner_id="dr-chen",
    )
    service = harness.client.app.state.service  # type: ignore[attr-defined]
    save_turn = service.repo.save_turn

    async def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the engine died before saving the turn")

    harness.llm.extend([booking])
    harness.judge.extend([verdict(True, "Yes, book it")])
    service.repo.save_turn = crash
    with pytest.raises(RuntimeError, match="died before saving"):
        harness.say(conversation, "Yes, book it", message_id="wamid.9")
    [booked] = harness.store.patient_appointments("pat_0003")
    assert harness.client.get(f"/v1/conversations/{conversation}").json()["messages"] == []

    # The engine is back, and the channel delivers the same message again.
    service.repo.save_turn = save_turn
    harness.llm.extend([booking, reply("You're booked for Tuesday at 9:00 AM.")])
    harness.judge.extend([verdict(True, "Yes, book it")])
    again = harness.say(conversation, "Yes, book it", message_id="wamid.9")

    # Nothing was stored for that message id, so this is a new turn, not a duplicate...
    assert not again["duplicate"]
    assert again["events"][0]["status"] == "ok"
    # ...and the clinic still has one appointment, the first.
    assert harness.store.patient_appointments("pat_0003") == [booked]
    assert again["events"][0]["result"]["appointment"]["id"] == booked.id
