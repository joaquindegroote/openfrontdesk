"""A customer who pushes back on a fee gets a person, and it isn't the model that decides.

The fee judge is asked whenever a quoted fee is unpaid and the customer writes, and when a
quote comes back with a fee. If it says the customer objects, the turn ends with the
business's own words. The model's script in these tests has nothing left for that turn: a call
to it would fail the test.
"""

from typing import Any

from openfrontdesk.agent.context import ToolEvent
from openfrontdesk.business import BusinessProfile
from openfrontdesk.guardrails.rules import pending_fee
from openfrontdesk.llm.scripted import ScriptedLLM, reply, tool_call
from openfrontdesk.wiring import open_conversation_service
from support import (
    JAVIERA,
    PROFILE,
    Harness,
    NoBackend,
    engine_settings,
    fee_verdict,
    tool_event,
)

CLEANING = "apt_00001"  # Javiera's, today at 11:00: cancelling it has a $50 fee
FEE_DISPUTE_REPLY = BusinessProfile.load(PROFILE).handoff_replies["fee_dispute"]
OBJECTS = fee_verdict(False, objects=True)
NO_OBJECTION = fee_verdict(False)


def quoted(harness: Harness) -> str:
    """Javiera asks to cancel and is told about the fee."""
    conversation = harness.start(JAVIERA)
    harness.llm.extend(
        [
            tool_call("get_cancellation_quote", appointment_id=CLEANING),
            reply("Cancelling now has a $50 late fee. Do you still want to cancel?"),
        ]
    )
    harness.judge.extend([NO_OBJECTION])
    harness.say(conversation, "I need to cancel my cleaning today")
    return conversation


def test_a_dispute_ends_the_turn_before_the_model_is_asked(harness: Harness) -> None:
    conversation = quoted(harness)
    harness.judge.extend([OBJECTS])

    result = harness.say(conversation, "I'm not paying that. Your manager said she'd waive it.")

    assert result["conversation"]["status"] == "handed_off"
    assert result["conversation"]["handoff_reason"] == "fee_dispute"
    assert result["messages"][-1]["text"] == FEE_DISPUTE_REPLY
    # Two calls, both from the first turn. In this one the model wrote and did nothing.
    assert len(harness.llm.calls) == 2
    assert result["events"] == []
    assert result["turn"]["steps"] == 0
    assert harness.store.appointments[CLEANING].status == "confirmed"
    # What staff read: the amount, that nothing happened, and the customer's own words.
    summary = result["conversation"]["handoff_summary"]
    assert "disputes the $50 late-cancellation fee" in summary
    assert "Nothing was cancelled or charged" in summary
    assert "Your manager said she'd waive it." in summary


def test_a_dispute_in_the_first_message_is_caught_when_the_quote_comes_back(
    harness: Harness,
) -> None:
    conversation = harness.start(JAVIERA)
    # Nothing is scripted after the quote: the turn has to end there.
    harness.llm.extend([tool_call("get_cancellation_quote", appointment_id=CLEANING)])
    harness.judge.extend([OBJECTS])

    result = harness.say(conversation, "Cancel my cleaning. Your manager waived the late fee.")

    assert result["conversation"]["handoff_reason"] == "fee_dispute"
    assert result["messages"][-1]["text"] == FEE_DISPUTE_REPLY
    assert [e["tool"] for e in result["events"]] == ["get_cancellation_quote"]
    assert harness.store.appointments[CLEANING].status == "confirmed"
    # The judge read the fee and the conversation, as it does before a cancellation.
    assert harness.judge.calls[0].messages[-1].content.startswith("Fee: $50")


def test_without_an_objection_the_model_carries_on(harness: Harness) -> None:
    conversation = quoted(harness)
    harness.judge.extend([NO_OBJECTION])
    harness.llm.extend([reply("Take your time. The appointment stays as it is for now.")])

    result = harness.say(conversation, "Let me think about it")

    assert result["conversation"]["status"] == "active"
    assert result["messages"][-1]["text"].startswith("Take your time.")


def test_a_judge_that_cant_answer_hands_nothing_off(harness: Harness) -> None:
    conversation = quoted(harness)
    # No verdict scripted, so the judge call fails. The guards on the write still stand.
    harness.llm.extend([reply("The $50 fee applies. Do you want to go ahead?")])

    result = harness.say(conversation, "This is ridiculous")

    assert result["conversation"]["status"] == "active"


def test_only_what_was_written_since_a_person_answered_counts(harness: Harness) -> None:
    conversation = quoted(harness)
    harness.judge.extend([OBJECTS])
    harness.say(conversation, "I'm not paying that. Your manager said she'd waive it.")
    harness.client.post(
        f"/v1/conversations/{conversation}/staff-messages",
        json={"text": "I've spoken to the manager. The fee stands.", "author": "Carolina"},
    )
    harness.client.post(f"/v1/conversations/{conversation}/resume")

    harness.judge.extend([NO_OBJECTION])
    harness.llm.extend([reply("You're welcome. Anything else?")])
    result = harness.say(conversation, "OK, thanks for checking")

    # Not handed off again over the objection the front desk already answered.
    assert result["conversation"]["status"] == "active"
    read = harness.judge.calls[-1].messages[-1].content
    assert "OK, thanks for checking" in read
    assert "waive" not in read


def event(tool: str, status: str = "ok", **result: Any) -> ToolEvent:
    return tool_event(tool, status, arguments={"appointment_id": CLEANING}, result=result)


def quote(fee: int) -> ToolEvent:
    return event("get_cancellation_quote", quote={"appointment_id": CLEANING, "fee": fee})


def test_a_fee_is_pending_from_its_quote_until_the_cancellation() -> None:
    assert pending_fee([]) is None
    assert pending_fee([quote(0)]) is None
    assert pending_fee([quote(50)]) == {"appointment_id": CLEANING, "fee": 50}
    # A cancellation the guards stopped changed nothing.
    assert pending_fee([quote(50), event("cancel_appointment", status="blocked")]) is not None
    assert pending_fee([quote(50), event("cancel_appointment", cancelled=True)]) is None
    # The newest quote is the one that counts.
    assert pending_fee([quote(50), quote(0)]) is None


async def test_the_check_goes_with_the_judges_when_an_ablation_turns_them_off() -> None:
    """It is an LLM judge, so "rules only" and "no guards" run without it."""
    settings = engine_settings()
    watching = {}
    for guards in ("full", "rules", "off"):
        async with open_conversation_service(
            settings,
            llm=ScriptedLLM(),
            judge_llm=ScriptedLLM(),
            backend=NoBackend(),  # type: ignore[arg-type]
            guards=guards,  # type: ignore[arg-type]
        ) as service:
            assert service.agent is not None
            watching[guards] = len(service.agent.watchers)

    assert watching == {"full": 1, "rules": 0, "off": 0}
