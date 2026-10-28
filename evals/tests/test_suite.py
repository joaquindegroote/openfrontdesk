"""Running a scenario: a fresh clinic and engine on the scenario's frozen clock."""

import json
from pathlib import Path

import pytest
from ofd_evals.scenarios import load_scenarios
from ofd_evals.suite import run_scenario
from openfrontdesk.llm.scripted import ScriptedLLM, reply
from openfrontdesk.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


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
