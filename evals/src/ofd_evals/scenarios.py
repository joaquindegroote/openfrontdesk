"""Eval scenarios: a simulated customer plus what must be true when the conversation ends.

Expectations are mostly about the outcome (the clinic's real state, the tools that ran, how
the conversation ended). Rubric items, graded by an LLM, cover what code can't see in a reply,
like a promise the assistant can't keep. Neither grades wording or style.
"""

from datetime import date, datetime, time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ofd_evals.customer import Persona

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "scenarios"

Weekday = Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppointmentExpectation(_Strict):
    """How many of a patient's upcoming appointments match all the given conditions."""

    patient: str = Field(description="Patient's full name.")
    service: str = Field(description="Service id, e.g. 'cleaning'.")
    count: int = 1
    status: Literal["confirmed", "pending_deposit", "cancelled"] | None = None
    day: date | None = None
    between: tuple[date, date] | None = None
    not_on_weekdays: list[Weekday] = []
    starts_before: time | None = None
    starts_after: time | None = None
    cancellation_fee: int | None = None


class Expectations(_Strict):
    ends: Literal["customer_done", "handed_off"] | None = None
    # Any of these handoff reasons is acceptable.
    handoff_reasons: list[str] = []
    must_call: list[str] = []
    must_not_call: list[str] = []
    appointments: list[AppointmentExpectation] = []
    # Statements about the assistant's replies that must hold, graded by an LLM.
    rubric: list[str] = []


class Scenario(_Strict):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    title: str
    # Frozen clock for the clinic and the engine, so slots and fees are the same on every run.
    now: datetime
    max_turns: int = 8
    customer: Persona
    expect: Expectations = Expectations()


class ScenarioFileError(ValueError):
    pass


def load_scenarios(directory: Path = SCENARIOS_DIR) -> list[Scenario]:
    scenarios = [load_scenario(path) for path in sorted(directory.glob("*.yaml"))]
    ids = [s.id for s in scenarios]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ScenarioFileError(f"duplicate scenario ids: {', '.join(duplicates)}")
    return scenarios


def load_scenario(path: Path) -> Scenario:
    try:
        return Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValidationError) as exc:
        raise ScenarioFileError(f"{path.name}: {exc}") from exc
