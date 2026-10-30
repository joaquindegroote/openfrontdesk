"""Running a scenario: a fresh clinic and engine on the scenario's frozen clock."""

import json
from pathlib import Path
from typing import Any

import pytest
from ofd_evals.scenarios import load_scenarios
from ofd_evals.suite import run_scenario
from openfrontdesk.llm.scripted import ScriptedLLM, reply, tool_call
from openfrontdesk.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
# The deterministic guards on book_appointment, in the order they run.
RULES = ["starts_in_future", "identity_verified", "no_duplicate_booking"]


async def test_a_scenario_runs_on_its_frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        business_profile=ROOT / "businesses" / "brightsmile-dental" / "profile.yaml",
    )
    [scenario] = [s for s in load_scenarios() if s.id == "late-cancellation-accepts-fee"]
    customer = ScriptedLLM([reply(json.dumps({"message": "", "done": True}))])

    run = await run_scenario(
        scenario, settings, customer_llm=customer, llm=ScriptedLLM(), judge_llm=ScriptedLLM()
    )

    emily = next(pid for pid, name in run.patient_names.items() if name == "Emily Johnson")
    [cleaning] = [a for a in run.appointments if a.patient_id == emily]
    # Seeded relative to the scenario's clock: same day, inside the late-cancellation window.
    assert cleaning.start.isoformat() == "2026-11-02T11:00:00-05:00"
    assert run.record.stop_reason == "customer_done"


def says(message: str, *, done: bool = False) -> Any:
    return reply(json.dumps({"message": message, "done": done}))


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
        business_profile=ROOT / "businesses" / "brightsmile-dental" / "profile.yaml",
    )
    [scenario] = [s for s in load_scenarios() if s.id == "book-earliest-checkup"]
    customer = ScriptedLLM([says("Is 9:00 tomorrow free?"), says("", done=True)])
    engine = ScriptedLLM(
        [
            tool_call(
                "book_appointment",
                customer_id="pat_0003",
                service_id="checkup",
                start="2026-11-03T09:00:00-05:00",
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
