"""Runs scenarios: each one in a fresh clinic and engine, starting at the scenario's time."""

import re
from dataclasses import dataclass
from itertools import count

from clinic_api.chaos import ChaosConfig
from clinic_api.models import Appointment
from openfrontdesk.agent.executor import GuardMode
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
    # The clinic's state before and after the conversation, to check what really happened.
    initial_appointments: list[Appointment]
    appointments: list[Appointment]
    patient_names: dict[str, str]
    # Which guards ran: "full" normally, "rules" or "off" in an ablation.
    guards: GuardMode = "full"
    # Links the business itself publishes, like its website, which a reply may contain.
    business_links: tuple[str, ...] = ()
    # Dollar amounts the assistant has from the start: each service's price and deposit, what's
    # left to pay after the deposit, and the amounts written in the business profile.
    business_amounts: frozenset[int] = frozenset()


async def run_scenario(
    scenario: Scenario,
    settings: Settings,
    *,
    customer_llm: LLMClient,
    llm: LLMClient | None = None,
    judge_llm: LLMClient | None = None,
    guards: GuardMode = "full",
) -> ScenarioRun:
    async with eval_environment(
        settings,
        llm=llm,
        judge_llm=judge_llm,
        now=scenario.now,
        chaos=ChaosConfig(down=tuple(scenario.clinic_down)),
        guards=guards,
    ) as env:
        initial = list(env.store.appointments.values())
        services = await env.service.backend.list_services()
        profile = env.service.business.model_dump_json()
        amounts = {int(a) for a in re.findall(r"\$(\d+)", profile)}
        for service in services:
            amounts |= {service.price, service.deposit, service.price - service.deposit}
        customer = SimulatedCustomer(customer_llm, scenario.customer)
        next_message = count(2)

        def while_the_customer_is_away() -> None:
            number = next(next_message)
            env.time_passes(scenario.sent_at.get(number))
            if taken := scenario.taken_before.get(number):
                env.someone_books(taken.service, taken.starting_from, taken.until)

        record = await run_conversation(
            env.service,
            customer,
            max_turns=scenario.max_turns,
            after_turn=while_the_customer_is_away,
        )
        return ScenarioRun(
            scenario=scenario,
            record=record,
            initial_appointments=initial,
            appointments=list(env.store.appointments.values()),
            patient_names={p.id: p.name for p in env.store.patients.values()},
            guards=guards,
            business_links=tuple(filter(None, [env.service.business.website])),
            business_amounts=frozenset(amounts),
        )
