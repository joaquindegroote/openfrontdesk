"""Runs scenarios: each one in a fresh clinic and engine, with the scenario's frozen clock."""

from dataclasses import dataclass

from clinic_api.models import Appointment
from openfrontdesk.llm import LLMClient
from openfrontdesk.settings import Settings

from ofd_evals.customer import SimulatedCustomer
from ofd_evals.environment import eval_environment
from ofd_evals.runner import ConversationRecord, run_conversation
from ofd_evals.scenarios import Scenario


@dataclass
class ScenarioRun:
    scenario: Scenario
    record: ConversationRecord
    # The clinic's state when the conversation ended, to check what really happened.
    appointments: list[Appointment]
    patient_names: dict[str, str]


async def run_scenario(
    scenario: Scenario,
    settings: Settings,
    *,
    customer_llm: LLMClient,
    llm: LLMClient | None = None,
    judge_llm: LLMClient | None = None,
) -> ScenarioRun:
    async with eval_environment(settings, llm=llm, judge_llm=judge_llm, now=scenario.now) as env:
        customer = SimulatedCustomer(customer_llm, scenario.customer)
        record = await run_conversation(env.service, customer, max_turns=scenario.max_turns)
        return ScenarioRun(
            scenario=scenario,
            record=record,
            appointments=list(env.store.appointments.values()),
            patient_names={p.id: p.name for p in env.store.patients.values()},
        )
