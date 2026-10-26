"""End-to-end conversations: engine API + agent loop + guards + the real clinic API in-process.
The model is scripted, so these tests pin down the system's behavior, not the model's."""

import pytest
from clinic_api.chaos import ChaosConfig
from fastapi.testclient import TestClient
from openfrontdesk.agent.loop import HANDOFF_REPLY
from openfrontdesk.api.app import create_app
from openfrontdesk.llm.scripted import reply, tool_call
from openfrontdesk.settings import Settings
from support import EMILY, PRIYA, PROFILE, Harness, HarnessFactory, verdict


def upcoming(harness: Harness, patient_id: str) -> list:
    return [a for a in harness.store.patient_appointments(patient_id) if a.status != "cancelled"]


def test_books_after_explicit_confirmation(harness: Harness) -> None:
    conversation = harness.start(PRIYA)
    harness.llm.extend(
        [
            tool_call("find_customer", phone=PRIYA),
            tool_call(
                "find_available_slots",
                service_id="checkup",
                date_from="2026-11-03",
                part_of_day="morning",
            ),
            reply("Hi Priya! Tomorrow I have 9:00 AM with Dr. Maya Chen. Does that work?"),
        ]
    )
    first = harness.say(conversation, "Hi, can I get a check-up tomorrow morning?")
    slots = first["events"][1]["result"]["slots"]
    assert slots[0]["label"] == "Tue Nov 3, 9:00 AM"

    harness.llm.extend(
        [
            reply("To confirm: a check-up ($95) on Tue Nov 3 at 9:00 AM with Dr. Chen. Book it?"),
        ]
    )
    harness.say(conversation, "9:00 with Dr. Chen please")

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
    third = harness.say(conversation, "Yes, book it")

    booking = third["events"][0]
    assert booking["status"] == "ok"
    assert [(c["name"], c["passed"]) for c in booking["checks"]] == [
        ("starts_in_future", True),
        ("customer_confirmed", True),
    ]
    [appointment] = upcoming(harness, "pat_0003")
    assert appointment.start.isoformat() == "2026-11-03T09:00:00-05:00"
    assert appointment.status == "confirmed"

    judge_prompt = harness.judge.calls[0].messages[-1].content
    assert "Book 'checkup' for customer pat_0003 on Tue Nov 3, 9:00 AM" in judge_prompt
    assert "Customer: Yes, book it" in judge_prompt
    assert third["messages"][-1] == {
        **third["messages"][-1],
        "role": "assistant",
        "text": "You're booked for Tuesday at 9:00 AM. See you then!",
    }


def test_unconfirmed_booking_is_blocked_before_reaching_the_clinic(harness: Harness) -> None:
    conversation = harness.start(PRIYA)
    harness.llm.extend(
        [
            tool_call(
                "book_appointment",
                customer_id="pat_0003",
                service_id="checkup",
                start="2026-11-03T10:00:00-05:00",
            ),
            reply("Just to confirm: a check-up on Tue Nov 3 at 10:00 AM for $95. Shall I book it?"),
        ]
    )
    harness.judge.extend([verdict(False, "the customer only asked about availability")])
    result = harness.say(conversation, "Do you have anything Tuesday at 10?")

    event = result["events"][0]
    assert event["status"] == "blocked"
    assert event["result"]["check"] == "customer_confirmed"
    assert upcoming(harness, "pat_0003") == []
    # The model is told why, so it can ask for confirmation instead of retrying.
    tool_message = harness.llm.calls[-1].messages[-1]
    assert tool_message.role == "tool"
    assert '"status": "blocked"' in tool_message.content


def test_judge_failure_fails_closed(harness: Harness) -> None:
    conversation = harness.start(PRIYA)
    harness.llm.extend(
        [
            tool_call(
                "book_appointment",
                customer_id="pat_0003",
                service_id="checkup",
                start="2026-11-03T10:00:00-05:00",
            ),
            reply("Could you confirm Tuesday at 10:00 AM?"),
        ]
    )
    harness.judge.extend([reply("I think so?")])  # not JSON
    result = harness.say(conversation, "Yes")

    assert result["events"][0]["status"] == "blocked"
    assert "Could not verify" in result["events"][0]["result"]["reason"]
    assert upcoming(harness, "pat_0003") == []


def test_late_cancellation_requires_quote_and_fee_acceptance(harness: Harness) -> None:
    conversation = harness.start(EMILY)
    harness.llm.extend(
        [
            tool_call("cancel_appointment", appointment_id="apt_00001"),
            tool_call("get_cancellation_quote", appointment_id="apt_00001"),
            reply("Cancelling now has a $50 late fee. Do you still want to cancel?"),
        ]
    )
    first = harness.say(conversation, "I need to cancel today's cleaning")
    assert [e["status"] for e in first["events"]] == ["blocked", "ok"]
    assert first["events"][0]["result"]["check"] == "cancellation_fee_disclosed"
    assert first["events"][1]["result"]["quote"]["fee"] == 50

    harness.llm.extend(
        [
            tool_call("cancel_appointment", appointment_id="apt_00001", reason="sick"),
            tool_call(
                "cancel_appointment",
                appointment_id="apt_00001",
                reason="sick",
                fee_acknowledged=True,
            ),
            reply("Done: your cleaning is cancelled and a $50 fee was charged."),
        ]
    )
    harness.judge.extend([verdict(True, "Yes, cancel it, I accept the $50 fee")])
    second = harness.say(conversation, "Yes, cancel it, I accept the $50 fee")

    assert [e["status"] for e in second["events"]] == ["blocked", "ok"]
    assert "$50" in second["events"][0]["result"]["reason"]
    assert second["events"][1]["result"]["fee_charged"] == 50
    assert harness.store.appointments["apt_00001"].status == "cancelled"
    # Rules run before the judge, so the judge only saw the one action that passed them.
    assert len(harness.judge.calls) == 1


def test_invalid_arguments_go_back_to_the_model(harness: Harness) -> None:
    conversation = harness.start(PRIYA)
    harness.llm.extend(
        [
            tool_call("book_appointment", customer_id="pat_0003"),
            reply("Which service and time would you like?"),
        ]
    )
    result = harness.say(conversation, "Book me in")

    event = result["events"][0]
    assert event["status"] == "invalid"
    assert {d["field"] for d in event["result"]["details"]} == {"service_id", "start"}
    assert harness.judge.calls == []


def test_booking_system_outage_hands_off_to_staff(make_harness: HarnessFactory) -> None:
    harness = make_harness(chaos=ChaosConfig(error_rate=1.0))
    conversation = harness.start(PRIYA)
    harness.llm.extend(
        [
            tool_call("find_available_slots", service_id="cleaning", date_from="2026-11-03"),
            tool_call("find_available_slots", service_id="cleaning", date_from="2026-11-03"),
            reply("Sorry, our booking system is down. A team member will follow up here soon."),
        ]
    )
    result = harness.say(conversation, "Any cleaning slots tomorrow?")

    assert [e["result"]["error"] for e in result["events"]] == ["service_unavailable"] * 2
    assert result["conversation"]["status"] == "handed_off"
    assert result["conversation"]["handoff_reason"] == "system_unavailable"
    assert harness.llm.calls[-1].tools == []  # the closing message can't call tools
    assert result["turn"]["outcome"] == "handed_off"


def test_step_budget_ends_in_a_handoff(make_harness: HarnessFactory) -> None:
    harness = make_harness(max_steps=2)
    conversation = harness.start(PRIYA)
    harness.llm.extend([tool_call("list_services"), tool_call("list_services")])
    result = harness.say(conversation, "What do you offer?")

    assert result["conversation"]["handoff_reason"] == "step_limit"
    assert result["messages"][-1]["text"] == HANDOFF_REPLY


def test_staff_take_over_and_hand_back(harness: Harness) -> None:
    conversation = harness.start(EMILY)
    harness.llm.extend(
        [
            tool_call(
                "handoff_to_human",
                reason="customer_request",
                summary="Emily Johnson wants to talk to a person about her cleaning today.",
            ),
            reply("Of course, I'm connecting you with our front desk now."),
        ]
    )
    handed = harness.say(conversation, "Can I talk to a person?")
    assert handed["conversation"]["status"] == "handed_off"
    assert "Emily Johnson" in handed["conversation"]["handoff_summary"]

    # While staff own the conversation, customer messages are stored but not answered by the AI.
    waiting = harness.say(conversation, "Hello?")
    assert waiting["turn"] is None
    assert [m["role"] for m in waiting["messages"]] == ["customer"]

    staff = harness.client.post(
        f"/v1/conversations/{conversation}/staff-messages",
        json={"text": "Hi Emily, this is Dana. How can I help?", "author": "Dana"},
    )
    assert staff.status_code == 201
    resumed = harness.client.post(f"/v1/conversations/{conversation}/resume")
    assert resumed.json()["status"] == "active"

    harness.llm.extend([reply("Anything else I can help with?")])
    harness.say(conversation, "Thanks Dana, all sorted")
    history = harness.llm.calls[-1].messages
    assert any(
        m.content == "[Dana, front-desk staff] Hi Emily, this is Dana. How can I help?"
        for m in history
    )

    detail = harness.client.get(f"/v1/conversations/{conversation}").json()
    assert [m["role"] for m in detail["messages"]] == [
        "customer",
        "assistant",
        "customer",
        "staff",
        "customer",
        "assistant",
    ]
    assert len(detail["turns"]) == 2


def test_inbox_lists_handed_off_conversations(harness: Harness) -> None:
    active = harness.start(PRIYA)
    waiting = harness.start(EMILY)
    harness.client.post(f"/v1/conversations/{waiting}/takeover", json={"reason": "complaint"})

    listed = harness.client.get("/v1/conversations", params={"status": "handed_off"}).json()
    assert [c["id"] for c in listed] == [waiting]
    assert active not in [c["id"] for c in listed]


def test_messages_return_503_without_an_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite:///:memory:",
        business_profile=PROFILE,
    )
    with TestClient(create_app(settings, backend=_NoBackend())) as client:
        conversation = client.post("/v1/conversations", json={}).json()["id"]
        response = client.post(f"/v1/conversations/{conversation}/messages", json={"text": "hi"})
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


class _NoBackend:
    async def aclose(self) -> None:
        pass
