"""Saved results: what a run keeps, how it reads back, and the summary."""

from pathlib import Path

from ofd_evals.checks import CheckResult
from ofd_evals.report import (
    append_run,
    compare_markdown,
    format_checks,
    format_transcript,
    latest_results,
    load_runs,
    run_entry,
    summary_markdown,
)
from test_checks import event, run, scenario

MODELS = {"engine": "gpt-5-mini (low)", "customer": "gpt-5-mini"}
PASSED = [CheckResult("calls book_appointment", True, "statuses: ['ok']")]
FAILED = [CheckResult("calls book_appointment", False, "never called")]


def test_a_run_keeps_its_transcript_events_and_checks() -> None:
    blocked = event("book_appointment", status="blocked", checks=(("customer_confirmed", False),))
    entry = run_entry(run(scenario(), events=(blocked,)), FAILED, attempt=2)

    assert entry["scenario"] == "test"
    assert entry["attempt"] == 2
    assert entry["passed"] is False
    [turn] = entry["transcript"]
    assert turn["customer"] == "Book me in"
    assert turn["events"][0]["status"] == "blocked"
    assert turn["events"][0]["checks"][0] == {
        "name": "customer_confirmed",
        "passed": False,
        "detail": "",
    }
    assert "· book_appointment: blocked (customer_confirmed)" in format_transcript(entry)
    assert format_checks(entry) == "  FAIL  calls book_appointment: never called"


def test_runs_are_appended_and_read_back(tmp_path: Path) -> None:
    folder = tmp_path / "20261102-080000"
    first = run_entry(run(scenario()), PASSED, attempt=1)
    second = run_entry(run(scenario()), FAILED, attempt=2)
    append_run(folder, first)
    append_run(folder, second)

    assert load_runs(folder) == [first, second]
    assert load_runs(folder / "runs.jsonl") == [first, second]


def test_latest_results_is_the_newest_folder_with_runs(tmp_path: Path) -> None:
    assert latest_results(tmp_path) is None
    entry = run_entry(run(scenario()), PASSED, attempt=1)
    append_run(tmp_path / "20261101-090000", entry)
    append_run(tmp_path / "20261102-080000", entry)
    (tmp_path / "20261103-070000").mkdir()  # an empty folder from an interrupted start

    assert latest_results(tmp_path) == tmp_path / "20261102-080000"


def test_summary_counts_passes_and_lists_failed_checks() -> None:
    entries = [
        run_entry(run(scenario()), PASSED, attempt=1),
        run_entry(run(scenario()), FAILED, attempt=2),
    ]
    summary = summary_markdown(entries, MODELS)

    assert "engine gpt-5-mini (low) · customer gpt-5-mini" in summary
    assert "| test | 1/2 | 1.0 | 0 | 0.0 s |" in summary
    assert "1/2 runs passed every check." in summary
    assert "- test (run 2): calls book_appointment: never called" in summary


def test_results_compare_side_by_side() -> None:
    passed = run_entry(run(scenario()), PASSED, attempt=1)
    failed = run_entry(run(scenario()), FAILED, attempt=2)
    table = compare_markdown({"nano": [passed, failed], "mini": [passed, passed]})

    assert table.splitlines()[:3] == [
        "| scenario | nano | mini |",
        "|---|---|---|",
        "| test | 1/2 | 2/2 |",
    ]
    assert "| **runs passed** | 1/2 | 2/2 |" in table
