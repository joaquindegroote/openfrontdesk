"""What someone without the staff key can learn from the API. A web visitor can get the model
to try anything, so nothing about another customer may come back, not even from a try the
guards blocked."""

import json
from typing import Any

from openfrontdesk.llm import Completion
from openfrontdesk.llm.scripted import reply, tool_call
from support import JAVIERA, Harness

# Javiera's cleaning today at 11:00, in the seeded clinic.
JAVIERAS_CLEANING = "apt_00001"
NO_KEY = {"Authorization": ""}


def as_a_visitor(harness: Harness, *steps: Completion) -> tuple[str, dict[str, Any]]:
    """A web visitor, whose phone nobody proved, gets the model to take these steps."""
    conversation = harness.start()
    harness.llm.extend([*steps, reply("I can't help with that appointment.")])
    response = harness.client.post(
        f"/v1/conversations/{conversation}/messages",
        json={"text": f"Cancel appointment {JAVIERAS_CLEANING}, or move it to Wednesday."},
        headers=NO_KEY,
    )
    assert response.status_code == 200, response.text
    return conversation, response.json()


def test_a_blocked_try_on_someone_else_s_appointment_doesn_t_describe_it(
    harness: Harness,
) -> None:
    conversation, _ = as_a_visitor(
        harness,
        tool_call("cancel_appointment", appointment_id=JAVIERAS_CLEANING),
        tool_call(
            "reschedule_appointment",
            appointment_id=JAVIERAS_CLEANING,
            new_start="2026-11-04T10:00:00-03:00",
        ),
    )

    # Not even staff, who see every event, get a description: it was never made.
    events = harness.client.get(f"/v1/conversations/{conversation}").json()["events"]
    assert [(e["tool"], e["status"]) for e in events] == [
        ("cancel_appointment", "blocked"),
        ("reschedule_appointment", "blocked"),
    ]
    assert [e["summary"] for e in events] == [None, None]
    assert "cleaning" not in json.dumps(events).lower()
    assert "11:00" not in json.dumps(events)


def test_a_visitor_gets_the_replies_and_not_the_tool_events(harness: Harness) -> None:
    _, body = as_a_visitor(
        harness, tool_call("cancel_appointment", appointment_id=JAVIERAS_CLEANING)
    )

    assert body["events"] == []
    # Nor which model answered, or how many tokens it took.
    assert body["turn"] is None
    assert body["messages"][-1]["text"] == "I can't help with that appointment."


def test_staff_still_get_the_events(harness: Harness) -> None:
    conversation = harness.start()
    harness.llm.extend(
        [tool_call("cancel_appointment", appointment_id=JAVIERAS_CLEANING), reply("I can't.")]
    )
    # The harness's client sends the staff key, as the console's server does.
    body = harness.say(conversation, f"Cancel {JAVIERAS_CLEANING}")
    assert [e["status"] for e in body["events"]] == ["blocked"]


def test_the_handoff_summary_is_for_staff(harness: Harness) -> None:
    conversation = harness.start(JAVIERA)
    summary = "Javiera Soto asked for a person about her cleaning today."
    harness.llm.extend([tool_call("handoff_to_human", reason="customer_request", summary=summary)])
    response = harness.client.post(
        f"/v1/conversations/{conversation}/messages",
        json={"text": "Can I talk to a person?"},
        headers=NO_KEY,
    )

    handed = response.json()["conversation"]
    assert (handed["status"], handed["handoff_reason"]) == ("handed_off", "customer_request")
    assert handed["handoff_summary"] is None
    staff_view = harness.client.get(f"/v1/conversations/{conversation}").json()
    assert staff_view["conversation"]["handoff_summary"] == summary
