"""Simulated-customer evals for openfrontdesk.

uv run python -m ofd_evals run                      # every scenario in evals/scenarios
uv run python -m ofd_evals run -s free-reschedule -n 3
uv run python -m ofd_evals show --failed            # the latest results
uv run python -m ofd_evals compare nano=evals/results/a mini=evals/results/b
uv run python -m ofd_evals chat --goal "Book a cleaning next week" --phone +15550100103
uv run python -m ofd_evals judges -n 3 -m gpt-5-mini -m claude-opus-5-5:medium
"""

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from openfrontdesk.agent.executor import GuardMode
from openfrontdesk.llm import LLMClient
from openfrontdesk.settings import Settings
from openfrontdesk.wiring import llm_client

from ofd_evals.checks import check_run
from ofd_evals.customer import Persona, SimulatedCustomer
from ofd_evals.environment import eval_environment
from ofd_evals.grader import grade_rubric
from ofd_evals.judges import (
    append_entry,
    is_correct,
    judges_markdown,
    load_judge_cases,
    run_judges,
)
from ofd_evals.judges import write_summary as write_judges_summary
from ofd_evals.report import (
    RESULTS_DIR,
    Entry,
    append_run,
    compare_markdown,
    conversation_entry,
    format_checks,
    format_transcript,
    latest_results,
    load_runs,
    run_entry,
    summary_markdown,
    write_summary,
)
from ofd_evals.runner import ConversationRecord, run_conversation
from ofd_evals.scenarios import load_scenarios
from ofd_evals.suite import run_scenario


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ofd_evals", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--engine-model", help="Model the assistant runs on (default: settings).")
    parser.add_argument("--judge-model", help="Confirmation judge model (default: settings).")
    parser.add_argument("--customer-model", default="gpt-5-mini")
    parser.add_argument("--grader-model", default="gpt-5-mini", help="Grades rubric items.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run scenarios.")
    run.add_argument("-s", "--scenario", action="append", help="Scenario id (repeatable).")
    run.add_argument("-n", "--repeats", type=int, default=1, help="Runs per scenario.")
    run.add_argument("-q", "--quiet", action="store_true", help="Only print the summary.")
    run.add_argument(
        "--out", type=Path, help="Results folder (default: evals/results/<date-time>)."
    )
    run.add_argument(
        "--guardrails",
        choices=["full", "rules", "off"],
        default="full",
        help="Ablation: 'rules' skips the LLM judges, 'off' skips every guard.",
    )

    show = commands.add_parser("show", help="Print the runs saved in a results folder.")
    show.add_argument(
        "path", type=Path, nargs="?", help="Results folder or runs.jsonl (default: the latest)."
    )
    show.add_argument("--failed", action="store_true", help="Only runs that failed a check.")
    show.add_argument("-s", "--scenario", action="append", help="Scenario id (repeatable).")

    compare = commands.add_parser("compare", help="Saved results side by side, as Markdown.")
    compare.add_argument(
        "results", nargs="+", help="Results folders, optionally labeled: label=path."
    )

    chat = commands.add_parser("chat", help="Run one ad-hoc simulated conversation.")
    chat.add_argument("--goal", required=True, help="What the simulated customer wants.")
    chat.add_argument("--name", default="Alex Morgan", help="The customer's name.")
    chat.add_argument("--phone", help="Phone the chat comes from (omit for a web visitor).")
    chat.add_argument("--fact", action="append", default=[], help="key=value the customer knows")
    chat.add_argument("--style", default="Polite and brief.")
    chat.add_argument("--max-turns", type=int, default=8)

    judges = commands.add_parser("judges", help="Score the guardrail judges on labeled cases.")
    judges.add_argument(
        "-m",
        "--model",
        action="append",
        help="Judge model, optionally model:effort (repeatable; default: the judge in settings).",
    )
    judges.add_argument("-c", "--case", action="append", help="Case id (repeatable).")
    judges.add_argument("-n", "--repeats", type=int, default=1, help="Runs per case and model.")
    judges.add_argument(
        "--out", type=Path, help="Results folder (default: evals/results/judges-<date-time>)."
    )

    args = parser.parse_args()
    if args.command == "show":
        path = args.path or latest_results()
        if path is None:
            raise SystemExit(f"No results in {RESULTS_DIR} yet.")
        _show(load_runs(path), failed_only=args.failed, scenarios=args.scenario)
        return
    if args.command == "compare":
        labeled = [r.split("=", 1) if "=" in r else [Path(r).name, r] for r in args.results]
        print(compare_markdown({label: load_runs(Path(path)) for label, path in labeled}), end="")
        return

    overrides = {"llm_model": args.engine_model, "judge_model": args.judge_model}
    settings = Settings().model_copy(update={k: v for k, v in overrides.items() if v})
    if args.command == "judges":
        _judges(settings, args.model, args.case, args.repeats, args.out)
        return
    customer_llm = llm_client(settings, args.customer_model, "low")
    if customer_llm is None:
        raise SystemExit(f"Set the API key for {args.customer_model} (in .env) to run evals.")

    if args.command == "chat":
        persona = Persona(
            name=args.name,
            goal=args.goal,
            phone=args.phone,
            facts=dict(fact.split("=", 1) for fact in args.fact),
            style=args.style,
        )
        record = asyncio.run(_chat(settings, customer_llm, persona, args.max_turns))
        print(format_transcript(conversation_entry(record)))
    else:
        grader = llm_client(settings, args.grader_model, "medium")
        if grader is None:
            raise SystemExit(f"Set the API key for {args.grader_model} (in .env).")
        models = {
            "engine": f"{settings.llm_model} ({settings.llm_reasoning_effort})",
            "judge": settings.judge_model,
            "customer": args.customer_model,
            "grader": args.grader_model,
            "guardrails": args.guardrails,
        }
        out = args.out or RESULTS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
        entries = asyncio.run(
            _run(
                settings,
                customer_llm,
                grader,
                args.scenario,
                args.repeats,
                args.quiet,
                out,
                args.guardrails,
            )
        )
        print("\n" + summary_markdown(entries, models))
        print(f"Saved to {write_summary(out, entries, models).parent}")
        if not all(entry["passed"] for entry in entries):
            raise SystemExit(1)


async def _chat(
    settings: Settings, customer_llm: LLMClient, persona: Persona, max_turns: int
) -> ConversationRecord:
    async with eval_environment(settings) as env:
        customer = SimulatedCustomer(customer_llm, persona)
        return await run_conversation(env.service, customer, max_turns=max_turns)


async def _run(
    settings: Settings,
    customer_llm: LLMClient,
    grader: LLMClient,
    selected: list[str] | None,
    repeats: int,
    quiet: bool,
    out: Path,
    guards: GuardMode,
) -> list[Entry]:
    scenarios = load_scenarios()
    if selected:
        unknown = set(selected) - {s.id for s in scenarios}
        if unknown:
            raise SystemExit(f"Unknown scenario(s): {', '.join(sorted(unknown))}")
        scenarios = [s for s in scenarios if s.id in selected]

    entries: list[Entry] = []
    for scenario in scenarios:
        for attempt in range(1, repeats + 1):
            run = await run_scenario(scenario, settings, customer_llm=customer_llm, guards=guards)
            checks = check_run(run) + await grade_rubric(run, grader)
            entry = run_entry(run, checks, attempt)
            append_run(out, entry)
            entries.append(entry)
            if not quiet:
                print(f"\n=== {scenario.id} (run {attempt}/{repeats}) ===")
                print(format_transcript(entry))
                print(format_checks(entry))
    return entries


def _judges(
    settings: Settings,
    specs: list[str] | None,
    selected: list[str] | None,
    repeats: int,
    out: Path | None,
) -> None:
    clients: dict[str, LLMClient] = {}
    for spec in specs or [settings.judge_model]:
        model, _, effort = spec.partition(":")
        effort = effort or settings.judge_reasoning_effort or ""
        llm = llm_client(settings, model, effort or None)
        if llm is None:
            raise SystemExit(f"Set the API key for {model} (in .env).")
        clients[f"{model} ({effort})" if effort else model] = llm

    cases = load_judge_cases()
    if selected:
        unknown = set(selected) - {c.id for c in cases}
        if unknown:
            raise SystemExit(f"Unknown case(s): {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c.id in selected]

    out = out or RESULTS_DIR / datetime.now().strftime("judges-%Y%m%d-%H%M%S")
    entries = asyncio.run(
        run_judges(
            cases, clients, repeats=repeats, on_result=lambda entry: append_entry(out, entry)
        )
    )
    print(judges_markdown(entries))
    print(f"Saved to {write_judges_summary(out, entries).parent}")
    if not all(is_correct(entry) for entry in entries):
        raise SystemExit(1)


def _show(entries: list[Entry], *, failed_only: bool, scenarios: list[str] | None) -> None:
    shown = [
        e
        for e in entries
        if (not failed_only or not e["passed"]) and (not scenarios or e["scenario"] in scenarios)
    ]
    for entry in shown:
        verdict = "passed" if entry["passed"] else "FAILED"
        print(f"\n=== {entry['scenario']} (run {entry['attempt']}): {verdict} ===")
        print(format_transcript(entry))
        print(format_checks(entry))
    print(f"\n{len(shown)} of {len(entries)} runs shown.")


if __name__ == "__main__":
    main()
