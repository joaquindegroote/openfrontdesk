"""The simulation loop with scripted models: what the simulator sees, and when a run stops."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from clinic_api.catalog import TIMEZONE
from ofd_evals import (
    CustomerTurn,
    Persona,
    SimulatedCustomer,
    SimulatorError,
    eval_environment,
    run_conversation,
)
from openfrontdesk.llm import Completion
from openfrontdesk.llm.scripted import ScriptedLLM, reply, tool_call
from openfrontdesk.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=TIMEZONE)  # Monday, before opening
PRIYA = Persona(
    name="Priya Shah",
    phone="+15550100103",
    goal="Book a check-up tomorrow morning.",
    facts={"full name": "Priya Shah"},
)


def says(message: str, *, done: bool = False) -> Completion:
    """A simulated-customer turn."""
    return reply(json.dumps({"message": message, "done": done}))


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        business_profile=ROOT / "businesses" / "brightsmile-dental" / "profile.yaml",
    )


async def test_customer_sees_its_goal_and_the_conversation() -> None:
    llm = ScriptedLLM([says("Is 9:00 free?")])
    customer = SimulatedCustomer(llm, PRIYA)

    turn = await customer.next_turn(
        [("customer", "Hi"), ("assistant", "Hi Priya! Tomorrow I have 9:00 or 10:00 AM.")]
    )

    assert turn == CustomerTurn(message="Is 9:00 free?", done=False)
    brief = llm.calls[0].messages[-1].content
    assert "Your goal: Book a check-up tomorrow morning." in brief
    assert "You: Hi\nAssistant: Hi Priya! Tomorrow I have 9:00 or 10:00 AM." in brief
    assert llm.calls[0].json_mode


async def test_ending_right_after_a_question_gets_one_nudge_to_answer() -> None:
    llm = ScriptedLLM([says("", done=True), says("Yes, please book it.")])
    customer = SimulatedCustomer(llm, PRIYA)

    turn = await customer.next_turn(
        [("customer", "9:00 please"), ("assistant", "A check-up at 9:00 with Dr. Chen. Book it?")]
    )

    assert turn == CustomerTurn(message="Yes, please book it.", done=False)
    assert "The assistant's last message asks you something" in llm.calls[1].messages[-1].content


async def test_a_request_to_confirm_without_a_question_mark_also_gets_the_nudge() -> None:
    llm = ScriptedLLM([says("", done=True), says("Yes")])
    customer = SimulatedCustomer(llm, PRIYA)

    turn = await customer.next_turn([("assistant", 'Reply "Yes" and I\'ll book it now.')])

    assert turn.message == "Yes"
    assert len(llm.calls) == 2


async def test_a_second_done_stands_and_statements_need_no_nudge() -> None:
    llm = ScriptedLLM([says("", done=True), says("", done=True), says("", done=True)])
    customer = SimulatedCustomer(llm, PRIYA)

    asked = await customer.next_turn([("assistant", "You're booked! Anything else?")])
    told = await customer.next_turn([("assistant", "You're booked for Tuesday at 9:00 AM.")])

    assert asked.done and told.done
    assert len(llm.calls) == 3  # two for the question, one for the statement


async def test_customer_output_must_be_a_json_turn() -> None:
    customer = SimulatedCustomer(ScriptedLLM([reply("Sure, 9 works!")]), PRIYA)
    with pytest.raises(SimulatorError):
        await customer.next_turn([])


async def test_runs_until_the_customer_is_done(settings: Settings) -> None:
    customer_llm = ScriptedLLM(
        [
            says("Can I get a check-up tomorrow morning?"),
            says("Yes, book it."),
            says("Thanks!", done=True),
        ]
    )
    agent = ScriptedLLM(
        [
            tool_call(
                "find_available_slots",
                service_id="checkup",
                date_from="2026-11-03",
                part_of_day="morning",
            ),
            reply("Tomorrow at 9:00 AM with Dr. Maya Chen, $95. Shall I book it?"),
            tool_call(
                "book_appointment",
                customer_id="pat_0003",
                service_id="checkup",
                start="2026-11-03T09:00:00-05:00",
                practitioner_id="dr-chen",
            ),
            reply("Booked for Tue Nov 3 at 9:00 AM."),
        ]
    )
    judge = ScriptedLLM([reply(json.dumps({"confirmed": True, "evidence": "Yes, book it."}))])

    async with eval_environment(settings, llm=agent, judge_llm=judge, now=NOW) as env:
        record = await run_conversation(env.service, SimulatedCustomer(customer_llm, PRIYA))
        booked = env.store.patient_appointments("pat_0003")

    assert record.stop_reason == "customer_done"
    assert [t.customer for t in record.turns] == [
        "Can I get a check-up tomorrow morning?",
        "Yes, book it.",
    ]
    assert record.turns[-1].replies == ["Booked for Tue Nov 3 at 9:00 AM."]
    assert [(e.tool, e.status) for e in record.events] == [
        ("find_available_slots", "ok"),
        ("book_appointment", "ok"),
    ]
    assert [a.start.isoformat() for a in booked] == ["2026-11-03T09:00:00-05:00"]


async def test_stops_when_the_conversation_is_handed_off(settings: Settings) -> None:
    customer_llm = ScriptedLLM([says("I want to talk to a person."), says("Hello?")])
    agent = ScriptedLLM(
        [
            tool_call(
                "handoff_to_human", reason="customer_request", summary="Priya asked for a person."
            ),
        ]
    )

    async with eval_environment(settings, llm=agent, judge_llm=ScriptedLLM(), now=NOW) as env:
        record = await run_conversation(env.service, SimulatedCustomer(customer_llm, PRIYA))

    assert record.stop_reason == "handed_off"
    assert record.final_status == "handed_off"
    assert customer_llm.remaining == 1  # the customer isn't asked to talk to staff


async def test_stops_after_max_turns(settings: Settings) -> None:
    customer_llm = ScriptedLLM([says("Hi"), says("Hello?")])
    agent = ScriptedLLM([reply("Hi! How can I help?"), reply("Still here.")])

    async with eval_environment(settings, llm=agent, judge_llm=ScriptedLLM(), now=NOW) as env:
        record = await run_conversation(
            env.service, SimulatedCustomer(customer_llm, PRIYA), max_turns=2
        )

    assert record.stop_reason == "max_turns"
    assert len(record.turns) == 2


async def test_a_broken_simulator_ends_the_run_with_the_error(settings: Settings) -> None:
    customer_llm = ScriptedLLM([reply("not json")])

    async with eval_environment(settings, llm=ScriptedLLM(), judge_llm=ScriptedLLM()) as env:
        record = await run_conversation(env.service, SimulatedCustomer(customer_llm, PRIYA))

    assert record.stop_reason == "simulator_error"
    assert "not a JSON turn" in (record.error or "")
    assert record.turns == []
