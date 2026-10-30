"""Eval results on disk: every run as one JSON line, plus a Markdown summary.

A failed run is only useful if you can read what happened, so each line keeps the whole
transcript, with tool events and guardrail checks. `python -m ofd_evals show` prints it back.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ofd_evals.checks import CheckResult
from ofd_evals.runner import ConversationRecord
from ofd_evals.suite import ScenarioRun

RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"
RUNS_FILE = "runs.jsonl"
SUMMARY_FILE = "summary.md"

Entry = dict[str, Any]


def run_entry(run: ScenarioRun, checks: list[CheckResult], attempt: int) -> Entry:
    return {
        "scenario": run.scenario.id,
        "attempt": attempt,
        "guards": run.guards,
        "passed": all(c.passed for c in checks),
        "checks": [asdict(c) for c in checks],
        **conversation_entry(run.record),
    }


def conversation_entry(record: ConversationRecord) -> Entry:
    return {
        "stop_reason": record.stop_reason,
        "final_status": record.final_status,
        "handoff_reason": record.handoff_reason,
        "error": record.error,
        "tokens": record.tokens,
        "latency_ms": record.latency_ms,
        "transcript": [
            {
                "customer": turn.customer,
                "events": [
                    event.model_dump(
                        mode="json", include={"tool", "arguments", "status", "result", "checks"}
                    )
                    for event in turn.events
                ],
                "replies": turn.replies,
                "outcome": turn.outcome,
                "tokens": turn.input_tokens + turn.output_tokens,
                "latency_ms": turn.latency_ms,
            }
            for turn in record.turns
        ],
    }


def append_run(directory: Path, entry: Entry) -> None:
    """Saved as soon as each run ends, so an interrupted suite keeps what it finished."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / RUNS_FILE).open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry) + "\n")


def write_summary(directory: Path, entries: list[Entry], models: dict[str, str]) -> Path:
    path = directory / SUMMARY_FILE
    path.write_text(summary_markdown(entries, models), encoding="utf-8")
    return path


def latest_results(directory: Path = RESULTS_DIR) -> Path | None:
    """Folders are named by date and time, so the last one in order is the newest."""
    folders = sorted(p for p in directory.glob("*") if (p / RUNS_FILE).is_file())
    return folders[-1] if folders else None


def load_runs(path: Path) -> list[Entry]:
    file = path / RUNS_FILE if path.is_dir() else path
    lines = file.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def summary_markdown(entries: list[Entry], models: dict[str, str]) -> str:
    by_scenario: dict[str, list[Entry]] = {}
    for entry in entries:
        by_scenario.setdefault(entry["scenario"], []).append(entry)

    lines = [
        "# Eval results",
        "",
        " · ".join(f"{role} {model}" for role, model in models.items()),
        "",
        "| scenario | passed | avg turns | avg engine tokens | avg engine time |",
        "|---|---|---|---|---|",
    ]
    for scenario_id, runs in by_scenario.items():
        passed = sum(run["passed"] for run in runs)
        lines.append(
            f"| {scenario_id} | {passed}/{len(runs)} | "
            f"{_mean(len(r['transcript']) for r in runs):.1f} | "
            f"{_mean(r['tokens'] for r in runs):,.0f} | "
            f"{_mean(r['latency_ms'] for r in runs) / 1000:.1f} s |"
        )
    total = sum(entry["passed"] for entry in entries)
    lines += ["", f"{total}/{len(entries)} runs passed every check."]
    failures = [
        f"- {entry['scenario']} (run {entry['attempt']}): {check['name']}: {check['detail']}"
        for entry in entries
        for check in entry["checks"]
        if not check["passed"]
    ]
    if failures:
        lines += ["", "Failed checks:", *failures]
    return "\n".join(lines) + "\n"


def compare_markdown(results: dict[str, list[Entry]]) -> str:
    """Several result sets side by side, such as the same suite on different models."""
    labels = list(results)
    scenarios = sorted({e["scenario"] for entries in results.values() for e in entries})
    lines = [
        "| scenario | " + " | ".join(labels) + " |",
        "|---|" + "---|" * len(labels),
    ]
    for scenario_id in scenarios:
        cells = []
        for entries in results.values():
            runs = [e for e in entries if e["scenario"] == scenario_id]
            cells.append(f"{sum(r['passed'] for r in runs)}/{len(runs)}" if runs else "-")
        lines.append(f"| {scenario_id} | " + " | ".join(cells) + " |")

    def totals(label: str, cell: Callable[[list[Entry]], str]) -> str:
        return f"| **{label}** | " + " | ".join(cell(e) for e in results.values()) + " |"

    lines += [
        totals("runs passed", lambda e: f"{sum(r['passed'] for r in e)}/{len(e)}"),
        totals("avg engine tokens", lambda e: f"{_mean(r['tokens'] for r in e):,.0f}"),
        totals(
            "avg engine time per turn",
            lambda e: f"{_per_turn(e) / 1000:.1f} s",
        ),
    ]
    return "\n".join(lines) + "\n"


def _per_turn(entries: list[Entry]) -> float:
    turns = sum(len(e["transcript"]) for e in entries)
    return sum(e["latency_ms"] for e in entries) / turns if turns else 0.0


def format_transcript(entry: Entry) -> str:
    lines = []
    for turn in entry["transcript"]:
        lines.append(f"\ncustomer  > {turn['customer']}")
        for event in turn["events"]:
            failed = next((c for c in event["checks"] if not c["passed"]), None)
            note = f" ({failed['name']})" if failed else ""
            lines.append(f"            · {event['tool']}: {event['status']}{note}")
        lines += [f"assistant < {reply}" for reply in turn["replies"]]
    lines.append(
        f"\nended: {entry['stop_reason']} · status {entry['final_status']} · "
        f"{len(entry['transcript'])} turns · {entry['tokens']:,} engine tokens · "
        f"{entry['latency_ms'] / 1000:.1f} s in the engine"
    )
    if entry["error"]:
        lines.append(f"error: {entry['error']}")
    return "\n".join(lines)


def format_checks(entry: Entry) -> str:
    return "\n".join(
        f"  {'PASS' if c['passed'] else 'FAIL'}  {c['name']}"
        + ("" if c["passed"] else f": {c['detail']}")
        for c in entry["checks"]
    )


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0
