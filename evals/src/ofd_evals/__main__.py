"""Run one simulated conversation against the engine and print it.

uv run python -m ofd_evals --goal "Book a cleaning next week" --phone +15550100103
"""

import argparse
import asyncio

from openfrontdesk.settings import Settings
from openfrontdesk.wiring import openai_client

from ofd_evals.customer import Persona, SimulatedCustomer
from ofd_evals.environment import eval_environment
from ofd_evals.runner import ConversationRecord, run_conversation


def main() -> None:
    parser = argparse.ArgumentParser(prog="ofd_evals", description=__doc__)
    parser.add_argument("--goal", required=True, help="What the simulated customer wants.")
    parser.add_argument("--name", default="Alex Morgan", help="The customer's name.")
    parser.add_argument("--phone", help="Phone the chat comes from (omit for a web visitor).")
    parser.add_argument("--fact", action="append", default=[], help="key=value the customer knows")
    parser.add_argument("--style", default="Polite and brief.")
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--customer-model", default="gpt-5-mini")
    args = parser.parse_args()

    persona = Persona(
        name=args.name,
        goal=args.goal,
        phone=args.phone,
        facts=dict(fact.split("=", 1) for fact in args.fact),
        style=args.style,
    )
    record = asyncio.run(_run(persona, args.customer_model, args.max_turns))
    _print(record)


async def _run(persona: Persona, customer_model: str, max_turns: int) -> ConversationRecord:
    settings = Settings()
    customer_llm = openai_client(settings, customer_model, "low")
    if customer_llm is None:
        raise SystemExit("Set OPENAI_API_KEY (in .env) to run evals.")
    async with eval_environment(settings) as env:
        customer = SimulatedCustomer(customer_llm, persona)
        return await run_conversation(env.service, customer, max_turns=max_turns)


def _print(record: ConversationRecord) -> None:
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


if __name__ == "__main__":
    main()
