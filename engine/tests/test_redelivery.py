"""Channels deliver a message more than once: a webhook is retried when the answer was slow.
With the channel's id for the message, the second delivery gets the first outcome back and
nothing runs again."""

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
