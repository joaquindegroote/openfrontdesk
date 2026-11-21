"""Saved results: what a run keeps, how it reads back, and the summary."""

from pathlib import Path

import pytest
from ofd_evals.checks import ENGINE_FAILURES, CheckResult
from ofd_evals.report import (
    append_run,
    compare_markdown,
    format_checks,
    format_transcript,
    latest_results,
    load_runs,
    run_entry,
    suite_failed,
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
        "error": False,
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
    assert "| test | 1/2 | 1.0 | 0 | - | - | 0.0 s |" in summary
    assert "1,000 conversations" not in summary
    assert "1/2 runs passed every check. 0 did something they shouldn't have." in summary
    assert "- test (run 2): calls book_appointment: never called" in summary


def test_a_run_says_what_it_cost_and_how_much_came_from_the_cache() -> None:
    conversation = run(scenario())
    conversation.record.turns[0].usage = {
        # 10,000 tokens in: 2,000 at $0.10 a million, 6,000 read at $0.01, 2,000 written at
        # $0.125. And 1,000 out at $0.50.
        "claude-haiku-5-5": {
            "input_tokens": 10_000,
            "output_tokens": 1_000,
            "cache_read_tokens": 6_000,
            "cache_write_tokens": 2_000,
        },
        # A dated name is priced as its model: 4,000 in at $0.25, none of it cached.
        "gpt-5-mini-2025-08-07": {"input_tokens": 4_000, "output_tokens": 0},
    }
    entry = run_entry(conversation, PASSED, attempt=1)

    assert entry["cost_usd"] == pytest.approx(0.00201)
    summary = summary_markdown([entry], MODELS)
    assert "| test | 1/1 | 1.0 | 0 | 43% | $2.01 | 0.0 s |" in summary
    assert "1,000 conversations like these cost $2.01 in engine tokens" in summary
    assert "$0.0020 (43% of input from cache)" in format_transcript(entry)
    table = compare_markdown({"haiku": [entry]})
    assert "| **input read from the prompt cache** | 43% |" in table
    assert "| **cost per 1,000 conversations** | $2.01 |" in table


def test_a_model_without_a_price_shows_no_cost() -> None:
    conversation = run(scenario())
    conversation.record.turns[0].usage = {"some-local-model": {"input_tokens": 500}}
    entry = run_entry(conversation, PASSED, attempt=1)

    assert entry["cost_usd"] is None
    assert "| test | 1/1 | 1.0 | 0 | 0% | - | 0.0 s |" in summary_markdown([entry], MODELS)


def test_a_suite_can_fail_only_on_unsafe_outcomes_and_crashes() -> None:
    undone = run_entry(run(scenario()), FAILED, attempt=1)
    unsafe = run_entry(
        run(scenario()), [CheckResult("appointment kept", False, "moved", unsafe=True)], attempt=1
    )
    crashed = run_entry(run(scenario()), [CheckResult(ENGINE_FAILURES, False, "turn 1")], attempt=1)
    passed = run_entry(run(scenario()), PASSED, attempt=1)

    assert suite_failed([passed, undone], "any")
    assert not suite_failed([passed, undone], "unsafe")
    assert suite_failed([passed, unsafe], "unsafe")
    assert suite_failed([passed, crashed], "unsafe")
    assert not suite_failed([passed], "any")


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
    assert "| **runs with an unsafe outcome** | 0/2 | 0/2 |" in table
