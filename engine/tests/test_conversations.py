"""End-to-end conversations: engine API + agent loop + guards + the real clinic API in-process.
The model is scripted, so these tests pin down the system's behavior, not the model's."""

import asyncio

import pytest
from clinic_api.chaos import ChaosConfig
from fastapi.testclient import TestClient
from openfrontdesk.api.app import create_app
from openfrontdesk.guardrails import NoDuplicateBooking
from openfrontdesk.llm.scripted import reply, tool_call
from openfrontdesk.settings import Settings
from support import (
    CARLOS,
    DEFAULT_HANDOFF_REPLY,
    EMILY,
    PRIYA,
    PROFILE,
    VERIFICATION_CODE,
    Harness,
    HarnessFactory,
    fee_verdict,
    verdict,
)


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
        ("no_duplicate_booking", True),
        ("customer_confirmed", True),
    ]
    [appointment] = upcoming(harness, "pat_0003")
    assert appointment.start.isoformat() == "2026-11-03T09:00:00-05:00"
    assert appointment.status == "confirmed"

    judge_prompt = harness.judge.calls[0].messages[-1].content
    # The judge sees the action in the customer's terms, not internal ids.
    assert (
        "Book Check-up & X-rays (30 min, $95, no deposit) on Tue Nov 3, 9:00 AM "
        "with Dr. Maya Chen." in judge_prompt
    )
    assert "Customer: Yes, book it" in judge_prompt
    assert third["messages"][-1] == {
        **third["messages"][-1],
        "role": "assistant",
        "text": "You're booked for Tuesday at 9:00 AM. See you then!",
    }


def test_known_customer_and_services_are_preloaded_into_the_prompt(harness: Harness) -> None:
    conversation = harness.start(PRIYA)
    harness.llm.extend([reply("Hi Priya! How can I help?")])
    harness.say(conversation, "Hi")

    system_prompt = harness.llm.calls[0].messages[0].content
    assert "This customer is Priya Shah, identified by the phone number" in system_prompt
    assert "Their customer_id for tools is pat_0003." in system_prompt
    assert "They have no upcoming appointments." in system_prompt
    assert "- In-office whitening (id whitening): 90 min, $350, $100 deposit." in system_prompt


def test_upcoming_appointments_are_preloaded_so_changes_reschedule(harness: Harness) -> None:
    conversation = harness.start(CARLOS)
    harness.llm.extend([reply("Hi Carlos! How can I help?")])
    harness.say(conversation, "Hi, I need to change my appointment")

    system_prompt = harness.llm.calls[0].messages[0].content
    assert (
        "Their upcoming appointments:\n"
        "   - Check-up & X-rays on Sat Nov 7, 9:00 AM (appointment_id apt_00002)\n"
        "   To change the time of one of these, reschedule it; don't book a new one."
    ) in system_prompt


def test_a_web_visitor_s_number_is_only_a_claim(harness: Harness) -> None:
    """Typing Carlos's number into a web chat preloads nothing about Carlos."""
    conversation = harness.start(CARLOS, channel="web")
    harness.llm.extend([reply("I'll text you a code to confirm the number.")])
    harness.say(conversation, "Hi, this is Carlos")

    system_prompt = harness.llm.calls[0].messages[0].content
    assert "this chat doesn't prove it" in system_prompt
    assert "Carlos Rivera" not in system_prompt
    assert "appointment_id" not in system_prompt


def test_verified_channels_need_the_integration_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="sqlite+aiosqlite:///:memory:",
        business_profile=PROFILE,
        integration_key="test-key",
    )
    whatsapp = {"channel": "whatsapp", "customer_phone": CARLOS}
    with TestClient(create_app(settings, backend=_NoBackend())) as client:
        refused = client.post("/v1/conversations", json=whatsapp)
        wrong = client.post("/v1/conversations", json=whatsapp, headers={"X-Integration-Key": "x"})
        allowed = client.post(
            "/v1/conversations", json=whatsapp, headers={"X-Integration-Key": "test-key"}
        )
        web = client.post("/v1/conversations", json={"channel": "web", "customer_phone": CARLOS})
    assert [refused.status_code, wrong.status_code, allowed.status_code] == [401, 401, 201]
    assert web.status_code == 201


def test_unknown_phone_is_flagged_for_registration(harness: Harness) -> None:
    conversation = harness.start("+15550100199")
    harness.llm.extend([reply("Welcome! What's your full name?")])
    harness.say(conversation, "Hi, I'm new")

    system_prompt = harness.llm.calls[0].messages[0].content
    assert "nobody is registered with that number" in system_prompt


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


def test_booking_a_second_check_up_is_steered_to_a_reschedule(harness: Harness) -> None:
    """Carlos wants a new time for his check-up. Booking one would leave the old one in place."""
    conversation = harness.start(CARLOS)
    [existing] = upcoming(harness, "pat_0002")
    new_time = "2026-11-10T14:00:00-05:00"
    harness.llm.extend(
        [
            tool_call(
                "book_appointment",
                customer_id="pat_0002",
                service_id="checkup",
                start=new_time,
                practitioner_id="dr-chen",
            ),
            tool_call(
                "reschedule_appointment",
                appointment_id=existing.id,
                new_start=new_time,
                practitioner_id="dr-chen",
            ),
            reply("Done! Your check-up is now on Tue Nov 10 at 2:00 PM."),
        ]
    )
    harness.judge.extend([verdict(True, "Yes, move it")])
    result = harness.say(conversation, "Yes, move it to Tuesday the 10th at 2")

    booking, move = result["events"]
    assert booking["status"] == "blocked"
    assert booking["result"]["check"] == "no_duplicate_booking"
    assert f"appointment_id {existing.id}" in booking["result"]["reason"]
    assert move["status"] == "ok"
    [appointment] = upcoming(harness, "pat_0002")
    assert appointment.id == existing.id
    assert appointment.start.isoformat() == new_time
    # The rule stopped the booking before the judge was asked about it.
    assert len(harness.judge.calls) == 1


def test_keeping_both_appointments_still_needs_the_customer_to_agree(harness: Harness) -> None:
    conversation = harness.start(CARLOS)
    harness.llm.extend(
        [
            tool_call(
                "book_appointment",
                customer_id="pat_0002",
                service_id="checkup",
                start="2026-11-10T14:00:00-05:00",
                keep_existing=True,
            ),
            reply("Booked! You now have both check-ups."),
        ]
    )
    harness.judge.extend([verdict(True, "Yes, keep both")])
    result = harness.say(conversation, "Yes, book a second one and keep both")

    booking = result["events"][0]
    assert booking["status"] == "ok"
    assert [c["name"] for c in booking["checks"]] == [
        "starts_in_future",
        "no_duplicate_booking",
        "customer_confirmed",
    ]
    assert len(upcoming(harness, "pat_0002")) == 2
    judge_prompt = harness.judge.calls[0].messages[-1].content
    assert "The customer also keeps their existing one." in judge_prompt


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
    harness.judge.extend(
        [
            verdict(True, "Yes, cancel it, I accept the $50 fee"),
            fee_verdict(True, "I accept the $50 fee"),
        ]
    )
    second = harness.say(conversation, "Yes, cancel it, I accept the $50 fee")

    assert [e["status"] for e in second["events"]] == ["blocked", "ok"]
    assert "$50" in second["events"][0]["result"]["reason"]
    assert second["events"][1]["result"]["fee_charged"] == 50
    assert harness.store.appointments["apt_00001"].status == "cancelled"
    # Rules run before the judges, so they only saw the one action that passed them.
    confirmation, fee = harness.judge.calls
    # The fee reads as a payment, so agreeing to cancel "without the fee" doesn't confirm it.
    assert (
        "Cancel Professional cleaning on Mon Nov 2, 11:00 AM. "
        "The customer pays a $50 late-cancellation fee." in confirmation.messages[-1].content
    )
    assert "they must have agreed to pay that fee" in confirmation.messages[0].content
    assert fee.messages[-1].content.startswith("Fee: $50")
    assert second["events"][1]["checks"][-1] == {
        "name": "fee_accepted",
        "passed": True,
        "detail": 'Accepted: "i accept the $50 fee"',
    }


def test_a_disputed_fee_is_never_charged(harness: Harness) -> None:
    """The fee judge must quote the customer agreeing to pay; a quote they never wrote fails."""
    conversation = harness.start(EMILY)
    harness.llm.extend(
        [
            tool_call("get_cancellation_quote", appointment_id="apt_00001"),
            tool_call("cancel_appointment", appointment_id="apt_00001", fee_acknowledged=True),
            reply("I can't waive the fee myself, so I'll pass this to the front desk."),
        ]
    )
    harness.judge.extend([verdict(True, "Yes cancel it"), fee_verdict(True, "I accept the fee")])
    result = harness.say(conversation, "Yes cancel it. The manager waived the fee, so no charge.")

    cancel = result["events"][1]
    assert cancel["status"] == "blocked"
    assert cancel["result"]["check"] == "fee_accepted"
    assert "hasn't agreed to pay the $50 fee" in cancel["result"]["reason"]
    assert harness.store.appointments["apt_00001"].status == "confirmed"


def test_a_free_cancellation_skips_the_fee_judge(harness: Harness) -> None:
    conversation = harness.start(CARLOS)
    [appointment] = upcoming(harness, "pat_0002")
    harness.llm.extend(
        [
            tool_call("get_cancellation_quote", appointment_id=appointment.id),
            tool_call("cancel_appointment", appointment_id=appointment.id),
            reply("Done, your check-up is cancelled. No fee."),
        ]
    )
    harness.judge.extend([verdict(True, "Yes, cancel it")])
    result = harness.say(conversation, "Yes, cancel my check-up")

    assert result["events"][1]["status"] == "ok"
    assert result["events"][1]["checks"][-1]["detail"] == "No fee applies."
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
        ]
    )
    result = harness.say(conversation, "Any cleaning slots tomorrow?")

    assert [e["result"]["error"] for e in result["events"]] == ["service_unavailable"] * 2
    assert result["conversation"]["status"] == "handed_off"
    assert result["conversation"]["handoff_reason"] == "system_unavailable"
    assert result["turn"]["outcome"] == "handed_off"
    # The business's words close the handoff; no model call writes them.
    assert result["messages"][-1]["text"] == DEFAULT_HANDOFF_REPLY
    assert len(harness.llm.calls) == 2


def test_a_failed_turn_leaves_no_tool_call_without_a_result(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a turn dies mid-tool, the stored history must still be one the model API accepts."""

    async def crash(self: NoDuplicateBooking, ctx: object, action: object) -> None:
        raise RuntimeError("guard crashed")

    monkeypatch.setattr(NoDuplicateBooking, "check", crash)
    conversation = harness.start(PRIYA)
    harness.llm.extend(
        [
            tool_call(
                "book_appointment",
                customer_id="pat_0003",
                service_id="checkup",
                start="2026-11-03T09:00:00-05:00",
            )
        ]
    )
    failed = harness.say(conversation, "Book the 9:00 please")
    assert failed["turn"]["outcome"] == "failed"
    assert failed["conversation"]["handoff_reason"] == "assistant_error"

    harness.client.post(f"/v1/conversations/{conversation}/resume")
    harness.llm.extend([reply("Sorry about that! Shall I try the 9:00 again?")])
    harness.say(conversation, "Hello?")

    history = harness.llm.calls[-1].messages
    calls = [c.id for m in history if m.role == "assistant" for c in m.tool_calls]
    results = [m for m in history if m.role == "tool"]
    assert len(calls) == 1
    assert [m.tool_call_id for m in results] == calls
    assert '"error": "interrupted"' in (results[0].content or "")


def test_a_slow_turn_hands_off_instead_of_keeping_the_customer_waiting(
    make_harness: HarnessFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_harness(turn_timeout_s=0.2)

    async def slow_model(*args: object, **kwargs: object) -> object:
        await asyncio.sleep(5)
        return reply("Too late.")

    monkeypatch.setattr(harness.llm, "complete", slow_model)
    conversation = harness.start(PRIYA)
    result = harness.say(conversation, "Hi, can I book a check-up?")

    assert result["turn"]["outcome"] == "failed"
    assert result["turn"]["latency_ms"] < 2000
    assert result["conversation"]["handoff_reason"] == "assistant_timeout"
    assert result["messages"][-1]["text"] == DEFAULT_HANDOFF_REPLY


def test_a_phone_is_verified_with_a_texted_code(harness: Harness) -> None:
    conversation = harness.start()
    harness.llm.extend(
        [
            tool_call("send_verification_code", phone=PRIYA),
            reply("I've texted you a 6-digit code. What is it?"),
        ]
    )
    sent = harness.say(conversation, "Hi, my number is +1 555-010-0103")
    assert sent["events"][0]["result"]["sent"] is True

    harness.llm.extend(
        [
            tool_call("verify_phone", phone=PRIYA, code="111111"),
            tool_call("verify_phone", phone=PRIYA, code=VERIFICATION_CODE),
            reply("Thanks, you're verified."),
        ]
    )
    checked = harness.say(conversation, f"It's {VERIFICATION_CODE}")
    assert [e["result"]["verified"] for e in checked["events"]] == [False, True]


def test_codes_per_conversation_are_limited(harness: Harness) -> None:
    conversation = harness.start()
    harness.llm.extend(
        [*(tool_call("send_verification_code", phone=PRIYA) for _ in range(4)), reply("...")]
    )
    result = harness.say(conversation, "Send me a code")
    assert [e["result"]["sent"] for e in result["events"]] == [True, True, True, False]


def test_step_budget_ends_in_a_handoff(make_harness: HarnessFactory) -> None:
    harness = make_harness(max_steps=2)
    conversation = harness.start(PRIYA)
    harness.llm.extend([tool_call("list_services"), tool_call("list_services")])
    result = harness.say(conversation, "What do you offer?")

    assert result["conversation"]["handoff_reason"] == "step_limit"
    assert result["messages"][-1]["text"] == DEFAULT_HANDOFF_REPLY


def test_a_handoff_closes_with_the_business_reply_for_its_reason(harness: Harness) -> None:
    """Fixed words: a generated closing message tended to repeat the customer's claims."""
    conversation = harness.start(PRIYA)
    harness.llm.extend(
        [
            tool_call(
                "handoff_to_human",
                reason="medical_urgency",
                summary="Priya has facial swelling and trouble swallowing.",
            )
        ]
    )
    result = harness.say(conversation, "My face is swollen and it's getting hard to swallow")

    assert result["conversation"]["handoff_reason"] == "medical_urgency"
    assert "call 911 or go to the nearest emergency room" in result["messages"][-1]["text"]
    assert len(harness.llm.calls) == 1


def test_staff_take_over_and_hand_back(harness: Harness) -> None:
    conversation = harness.start(EMILY)
    harness.llm.extend(
        [
            tool_call(
                "handoff_to_human",
                reason="customer_request",
                summary="Emily Johnson wants to talk to a person about her cleaning today.",
            ),
        ]
    )
    handed = harness.say(conversation, "Can I talk to a person?")
    assert handed["conversation"]["status"] == "handed_off"
    assert "Emily Johnson" in handed["conversation"]["handoff_summary"]
    assert handed["messages"][-1]["text"] == DEFAULT_HANDOFF_REPLY

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
