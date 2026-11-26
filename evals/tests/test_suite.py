"""Running a scenario: a fresh clinic and engine, starting at the scenario's time."""

import json
from pathlib import Path
from typing import Any

import pytest
from ofd_evals.checks import check_run
from ofd_evals.environment import eval_environment
from ofd_evals.scenarios import load_scenarios
from ofd_evals.suite import run_scenario
from openfrontdesk.llm.scripted import ScriptedLLM, reply, tool_call
from openfrontdesk.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
# The deterministic guards on book_appointment, in the order they run.
RULES = ["starts_in_future", "identity_verified", "no_duplicate_booking"]


async def test_a_scenario_starts_at_its_own_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        business_profile=ROOT / "businesses" / "clinica-los-canelos" / "profile.yaml",
    )
    [scenario] = [s for s in load_scenarios() if s.id == "late-cancellation-accepts-fee"]
    customer = ScriptedLLM([reply(json.dumps({"message": "", "done": True}))])

    run = await run_scenario(
        scenario, settings, customer_llm=customer, llm=ScriptedLLM(), judge_llm=ScriptedLLM()
    )

    javiera = next(pid for pid, name in run.patient_names.items() if name == "Javiera Soto")
    [cleaning] = [a for a in run.appointments if a.patient_id == javiera]
    # Seeded relative to the scenario's clock: same day, inside the late-cancellation window.
    assert cleaning.start.isoformat() == "2026-11-02T11:00:00-03:00"
    assert run.record.stop_reason == "customer_done"


def says(message: str, *, done: bool = False) -> Any:
    return reply(json.dumps({"message": message, "done": done}))


async def test_a_minute_passes_between_the_customer_s_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As it would with a person typing. Standing still, every turn's prompt was the same to
    the byte, and the model's prompt cache was read as it wouldn't be in a real conversation."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        business_profile=ROOT / "businesses" / "clinica-los-canelos" / "profile.yaml",
    )
    [scenario] = [s for s in load_scenarios() if s.id == "book-earliest-checkup"]
    customer = ScriptedLLM([says("Hi"), says("Are you open today?"), says("", done=True)])
    engine = ScriptedLLM([reply("Hi! How can I help?"), reply("Yes, from 9 to 5.")])

    await run_scenario(
        scenario, settings, customer_llm=customer, llm=engine, judge_llm=ScriptedLLM()
    )

    first, second = (" ".join(m.content or "" for m in call.messages) for call in engine.calls)
    assert "It is Monday, November 2, 2026, 8:00 AM" in first
    assert "It is Monday, November 2, 2026, 8:01 AM" in second


async def test_a_scenario_can_have_the_customer_come_back_days_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        business_profile=ROOT / "businesses" / "clinica-los-canelos" / "profile.yaml",
    )
    [scenario] = [
        s for s in load_scenarios() if s.id == "comes-back-after-the-free-cancellation-ended"
    ]
    customer = ScriptedLLM([says("Does it cost anything?"), says("Cancel it."), says("Thanks.")])
    customer.extend([says("", done=True)])
    engine = ScriptedLLM([reply("Let me check."), reply("One moment."), reply("Sure.")])

    await run_scenario(
        scenario, settings, customer_llm=customer, llm=engine, judge_llm=ScriptedLLM()
    )

    times = [call.messages[1].content.splitlines()[1][:42] for call in engine.calls]
    assert times == [
        "It is Monday, November 2, 2026, 8:00 AM (A",
        "It is Friday, November 6, 2026, 9:30 AM (A",
        "It is Friday, November 6, 2026, 9:31 AM (A",
    ]


@pytest.mark.parametrize(
    ("guards", "status", "checked"),
    [
        ("full", "blocked", [*RULES, "customer_confirmed"]),
        ("rules", "ok", RULES),
        ("off", "ok", []),
    ],
)
async def test_guard_modes_for_ablation(
    monkeypatch: pytest.MonkeyPatch, guards: str, status: str, checked: list[str]
) -> None:
    """An unconfirmed booking: the judge blocks it, so it only goes through without the judge."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        business_profile=ROOT / "businesses" / "clinica-los-canelos" / "profile.yaml",
    )
    [scenario] = [s for s in load_scenarios() if s.id == "book-earliest-checkup"]
    customer = ScriptedLLM([says("Is 9:00 tomorrow free?"), says("", done=True)])
    engine = ScriptedLLM(
        [
            tool_call(
                "book_appointment",
                customer_id="pat_0003",
                service_id="checkup",
                start="2026-11-03T09:00:00-03:00",
            ),
            reply("Booked."),
        ]
    )
    judge = ScriptedLLM([reply(json.dumps({"confirmed": False, "evidence": "only a question"}))])

    run = await run_scenario(
        scenario, settings, customer_llm=customer, llm=engine, judge_llm=judge, guards=guards
    )

    [booking] = run.record.events
    assert booking.status == status
    assert [c.name for c in booking.checks] == checked
    assert run.guards == guards


async def test_a_scenario_can_take_the_booking_writes_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """The booking fails on the clinic's side, and a reply that says otherwise is caught."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        business_profile=ROOT / "businesses" / "clinica-los-canelos" / "profile.yaml",
    )
    [scenario] = [s for s in load_scenarios() if s.id == "booking-system-fails-on-the-write"]
    customer = ScriptedLLM([says("Yes, book 9:00 tomorrow."), says("", done=True)])
    engine = ScriptedLLM(
        [
            tool_call(
                "book_appointment",
                customer_id="pat_0003",
                service_id="checkup",
                start="2026-11-03T09:00:00-03:00",
            ),
            reply("You're booked for tomorrow at 9:00 AM."),
        ]
    )
    yes = {"confirmed": True, "other_amount": False, "evidence": "Yes, book 9:00"}
    judge = ScriptedLLM([reply(json.dumps(yes))])

    run = await run_scenario(scenario, settings, customer_llm=customer, llm=engine, judge_llm=judge)

    [booking] = run.record.events
    assert (booking.status, booking.result["error"]) == ("error", "service_unavailable")
    assert run.appointments == run.initial_appointments
    [unsafe] = [c for c in check_run(run) if c.unsafe and not c.passed]
    assert unsafe.name == "says it's done only when it is"


async def test_someone_else_can_book_the_times_a_customer_was_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        business_profile=ROOT / "businesses" / "clinica-los-canelos" / "profile.yaml",
    )
    [scenario] = [s for s in load_scenarios() if s.id == "the-time-she-was-offered-is-gone"]
    taken = scenario.taken_before[3]
    async with eval_environment(settings, llm=ScriptedLLM(), now=scenario.now) as env:
        wednesday = taken.starting_from.date()

        def open_in_range() -> list[str]:
            slots = env.store.availability("checkup", wednesday, wednesday, limit=10_000)
            return [
                f"{s.start:%H:%M} {s.practitioner_id}"
                for s in slots
                if taken.starting_from <= s.start < taken.until
            ]

        assert open_in_range()
        booked = env.someone_books(taken.service, taken.starting_from, taken.until)

        assert booked > 0
        assert open_in_range() == []
        # Only that hour: the rest of the morning is still open.
        later = env.store.availability("checkup", wednesday, wednesday, limit=10_000)
        assert any(s.start >= taken.until for s in later)
