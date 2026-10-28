"""Simulated-customer evals for openfrontdesk.

uv run python -m ofd_evals run                      # every scenario in evals/scenarios
uv run python -m ofd_evals run -s free-reschedule -n 3
uv run python -m ofd_evals chat --goal "Book a cleaning next week" --phone +15550100103
"""

import argparse
import asyncio

from openfrontdesk.llm import LLMClient
from openfrontdesk.settings import Settings
from openfrontdesk.wiring import openai_client

from ofd_evals.customer import Persona, SimulatedCustomer
from ofd_evals.environment import eval_environment
from ofd_evals.runner import ConversationRecord, run_conversation
from ofd_evals.scenarios import load_scenarios
from ofd_evals.suite import ScenarioRun, run_scenario


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ofd_evals", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--customer-model", default="gpt-5-mini")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run scenarios.")
    run.add_argument("-s", "--scenario", action="append", help="Scenario id (repeatable).")
    run.add_argument("-n", "--repeats", type=int, default=1, help="Runs per scenario.")
    run.add_argument("-q", "--quiet", action="store_true", help="Only print the summary.")

    chat = commands.add_parser("chat", help="Run one ad-hoc simulated conversation.")
    chat.add_argument("--goal", required=True, help="What the simulated customer wants.")
    chat.add_argument("--name", default="Alex Morgan", help="The customer's name.")
    chat.add_argument("--phone", help="Phone the chat comes from (omit for a web visitor).")
    chat.add_argument("--fact", action="append", default=[], help="key=value the customer knows")
    chat.add_argument("--style", default="Polite and brief.")
    chat.add_argument("--max-turns", type=int, default=8)

    args = parser.parse_args()
    settings = Settings()
    customer_llm = openai_client(settings, args.customer_model, "low")
    if customer_llm is None:
        raise SystemExit("Set OPENAI_API_KEY (in .env) to run evals.")

    if args.command == "chat":
        persona = Persona(
            name=args.name,
            goal=args.goal,
            phone=args.phone,
            facts=dict(fact.split("=", 1) for fact in args.fact),
            style=args.style,
        )
        _print_transcript(asyncio.run(_chat(settings, customer_llm, persona, args.max_turns)))
    else:
        asyncio.run(_run(settings, customer_llm, args.scenario, args.repeats, args.quiet))


async def _chat(
    settings: Settings, customer_llm: LLMClient, persona: Persona, max_turns: int
) -> ConversationRecord:
    async with eval_environment(settings) as env:
        customer = SimulatedCustomer(customer_llm, persona)
        return await run_conversation(env.service, customer, max_turns=max_turns)


async def _run(
    settings: Settings,
    customer_llm: LLMClient,
    selected: list[str] | None,
    repeats: int,
    quiet: bool,
) -> None:
    scenarios = load_scenarios()
    if selected:
        unknown = set(selected) - {s.id for s in scenarios}
        if unknown:
            raise SystemExit(f"Unknown scenario(s): {', '.join(sorted(unknown))}")
        scenarios = [s for s in scenarios if s.id in selected]

    runs: list[ScenarioRun] = []
    for scenario in scenarios:
        for attempt in range(1, repeats + 1):
            run = await run_scenario(scenario, settings, customer_llm=customer_llm)
            runs.append(run)
            if not quiet:
                print(f"\n=== {scenario.id} (run {attempt}/{repeats}) ===")
                _print_transcript(run.record)
    _print_summary(runs)


def _print_transcript(record: ConversationRecord) -> None:
    for turn in record.turns:
        print(f"\ncustomer  > {turn.customer}")
        for event in turn.events:
            failed = next((c for c in event.checks if not c.passed), None)
            note = f" ({failed.name})" if failed else ""
            print(f"            · {event.tool}: {event.status}{note}")
        for reply in turn.replies:
            print(f"assistant < {reply}")
    print(
        f"\nended: {record.stop_reason} · status {record.final_status} · "
        f"{len(record.turns)} turns · {record.tokens:,} engine tokens · "
        f"{record.latency_ms / 1000:.1f} s in the engine"
    )
    if record.error:
        print(f"error: {record.error}")


def _print_summary(runs: list[ScenarioRun]) -> None:
    print("\n| scenario | ended | turns | engine tokens | engine time |")
    print("|---|---|---|---|---|")
    for run in runs:
        record = run.record
        print(
            f"| {run.scenario.id} | {record.stop_reason} | {len(record.turns)} | "
            f"{record.tokens:,} | {record.latency_ms / 1000:.1f} s |"
        )


if __name__ == "__main__":
    main()
