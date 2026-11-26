"""Count again from saved runs, without running anything.

Every number in docs/eval-results.md came from folders of saved runs, and they are published
(docs/eval-runs.md says where). This prints what a set of folders shows: how many runs, which
checks failed in how many of them, and in how many a reply contains a phrase.
"""

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from ofd_evals import judges
from ofd_evals.checks import said
from ofd_evals.report import RUNS_FILE, Entry, load_runs


def load_folders(paths: Sequence[Path]) -> tuple[list[Entry], list[Entry]]:
    """The scenario runs and the judge verdicts saved in these folders, together."""
    runs: list[Entry] = []
    verdicts: list[Entry] = []
    for path in paths:
        if (path / RUNS_FILE).is_file():
            runs += load_runs(path)
        elif (path / judges.RUNS_FILE).is_file():
            verdicts += load_runs(path / judges.RUNS_FILE)
        else:
            raise SystemExit(f"No saved runs in {path}.")
    return runs, verdicts


def recount_markdown(
    entries: list[Entry], *, phrases: Sequence[str] = (), checks: Sequence[str] = ()
) -> str:
    """`checks` are listed even when no run failed them: a zero is a result too."""
    passed = sum(entry["passed"] for entry in entries)
    unsafe = sum(bool(entry.get("unsafe")) for entry in entries)
    lines = [
        f"{len(entries)} runs: {passed} passed every check, {unsafe} had an unsafe outcome.",
    ]
    # A check counts once per run, however many times it failed in it.
    failed = Counter(
        name
        for entry in entries
        for name in {check["name"] for check in entry["checks"] if not check["passed"]}
    )
    for name in checks:
        failed.setdefault(name, 0)
    if failed:
        lines += ["", "| check that failed | runs |", "|---|---|"]
        lines += [f"| {name} | {count}/{len(entries)} |" for name, count in failed.most_common()]
    if phrases:
        saying = {phrase: sum(_says(entry, [phrase]) for entry in entries) for phrase in phrases}
        lines += ["", "| a reply says | runs |", "|---|---|"]
        lines += [f'| "{phrase}" | {count}/{len(entries)} |' for phrase, count in saying.items()]
        if len(phrases) > 1:
            any_of_them = sum(_says(entry, phrases) for entry in entries)
            lines.append(f"| any of them | {any_of_them}/{len(entries)} |")
    return "\n".join(lines) + "\n"


def _says(entry: Entry, phrases: Sequence[str]) -> bool:
    replies = [reply for turn in entry["transcript"] for reply in turn["replies"]]
    return any(said(phrase, reply) is not None for phrase in phrases for reply in replies)
