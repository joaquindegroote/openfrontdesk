"""Labeled cases for the guardrail judges, scored apart from the assistant.

Each case is a conversation, the action about to run, and the verdict a careful person would
give. A false approval lets through an action the customer never agreed to: the mistake the
judges exist to stop. A false block makes the assistant ask again: a nuisance, not a harm. Both
are counted per judge model, on the same cases, through the same guard code the engine runs.
"""

import asyncio
import json
import statistics
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any, Literal, Self, cast

import yaml
from clinic_api.catalog import TIMEZONE
from openfrontdesk.agent.context import ToolContext, ToolEvent
from openfrontdesk.backends import BookingBackend
from openfrontdesk.business import BusinessProfile
from openfrontdesk.guardrails import CustomerConfirmed, FeeAccepted
from openfrontdesk.guardrails.base import ProposedAction
from openfrontdesk.llm import ChatMessage, Completion, LLMClient, ToolSpec
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CASES_DIR = Path(__file__).resolve().parents[2] / "judge-cases"
PROFILE = Path(__file__).resolve().parents[3] / "businesses" / "brightsmile-dental" / "profile.yaml"
RUNS_FILE = "judges.jsonl"
SUMMARY_FILE = "summary.md"
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=TIMEZONE)
APPOINTMENT_ID = "apt_00001"

Entry = dict[str, Any]


class JudgeCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    judge: Literal["confirmation", "fee"]
    # What the case probes: sarcasm, a stale confirmation, an injected note...
    trap: str
    # Confirmation judge: the action, worded the way the booking tools word it.
    action: str | None = None
    # Fee judge: the late-cancellation fee about to be charged.
    fee: int | None = None
    conversation: list[dict[Literal["customer", "assistant"], str]] = Field(min_length=1)
    expect: Literal["approve", "block"]
    why: str

    @model_validator(mode="after")
    def _fits_its_judge(self) -> Self:
        if self.judge == "confirmation" and not self.action:
            raise ValueError("a confirmation case needs an action")
        if self.judge == "fee" and not self.fee:
            raise ValueError("a fee case needs a fee")
        if any(len(line) != 1 for line in self.conversation):
            raise ValueError("each conversation line has exactly one speaker")
        return self


class JudgeCaseFileError(ValueError):
    pass


def load_judge_cases(directory: Path = CASES_DIR) -> list[JudgeCase]:
    cases: list[JudgeCase] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            cases += [JudgeCase.model_validate(case) for case in raw]
        except (yaml.YAMLError, ValidationError, TypeError) as exc:
            raise JudgeCaseFileError(f"{path.name}: {exc}") from exc
    ids = [c.id for c in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise JudgeCaseFileError(f"duplicate case ids: {', '.join(duplicates)}")
    return cases


async def run_judges(
    cases: Sequence[JudgeCase],
    judges: dict[str, LLMClient],
    *,
    repeats: int = 1,
    concurrency: int = 8,
    on_result: Callable[[Entry], None] | None = None,
) -> list[Entry]:
    """Every case, `repeats` times, on every judge model."""
    limit = asyncio.Semaphore(concurrency)

    async def one(label: str, llm: LLMClient, case: JudgeCase, attempt: int) -> Entry:
        async with limit:
            entry = {"model": label, "attempt": attempt, **await judge_case(case, llm)}
        if on_result:
            on_result(entry)
        return entry

    return list(
        await asyncio.gather(
            *(
                one(label, llm, case, attempt)
                for label, llm in judges.items()
                for case in cases
                for attempt in range(1, repeats + 1)
            )
        )
    )


async def judge_case(case: JudgeCase, llm: LLMClient) -> Entry:
    recorder = _Recorder(llm)
    judge = CustomerConfirmed(recorder) if case.judge == "confirmation" else FeeAccepted(recorder)
    action = ProposedAction(
        "cancel_appointment" if case.judge == "fee" else "judged_action",
        _Target(),
        case.action or f"Cancel the appointment. The customer pays a ${case.fee} fee.",
    )
    started = time.perf_counter()
    result = await judge.check(_context(case), action)
    return {
        "case": case.id,
        "judge": case.judge,
        "trap": case.trap,
        "expect": case.expect,
        "approved": result.passed,
        # The model's own verdict, before code checked its quote (fee judge).
        "model_approved": _model_verdict(case, recorder.reply),
        "error": recorder.error,
        "reply": recorder.reply,
        "detail": result.detail,
        "seconds": round(time.perf_counter() - started, 2),
    }


def is_correct(entry: Entry) -> bool:
    return entry["approved"] == (entry["expect"] == "approve")


def judges_markdown(entries: list[Entry]) -> str:
    models = list(dict.fromkeys(e["model"] for e in entries))
    judges = list(dict.fromkeys(e["judge"] for e in entries))
    columns = [f"{judge}: false {kind}" for judge in judges for kind in ("approvals", "blocks")]
    columns += ["approvals the quote check caught", "median time"]
    lines = [f"| judge model | {' | '.join(columns)} |", "|---|" + "---|" * len(columns)]
    notes = []
    for model in models:
        mine = [e for e in entries if e["model"] == model]
        cells = []
        for judge in judges:
            block = [e for e in mine if e["judge"] == judge and e["expect"] == "block"]
            approve = [e for e in mine if e["judge"] == judge and e["expect"] == "approve"]
            cells.append(f"{sum(e['approved'] for e in block)}/{len(block)}")
            cells.append(f"{sum(not e['approved'] for e in approve)}/{len(approve)}")
        caught = sum(
            e["model_approved"] is True and not e["approved"] and e["expect"] == "block"
            for e in mine
        )
        cells += [str(caught), f"{statistics.median(e['seconds'] for e in mine):.1f} s"]
        lines.append(f"| {model} | {' | '.join(cells)} |")
        # The guard fails closed, so these block whatever the label says: right on a case that
        # should be blocked, but not a verdict.
        no_verdict = sum(e["model_approved"] is None for e in mine)
        if no_verdict:
            notes.append(
                f"{model}: {no_verdict} calls returned no verdict (an error, or a reply that "
                "wasn't JSON), counted as blocks."
            )
    lines += ["", *notes] if notes else []

    missed = [e for e in entries if not is_correct(e)]
    if not missed:
        return "\n".join([*lines, "", "Every verdict matched its label.", ""])
    lines += ["", "Misses by case:", "", f"| case | expected | {' | '.join(models)} |"]
    lines.append("|---|---|" + "---|" * len(models))
    cases = list(dict.fromkeys(e["case"] for e in missed))
    for case in cases:
        runs = [e for e in entries if e["case"] == case]
        cells = []
        for model in models:
            mine = [e for e in runs if e["model"] == model]
            misses = sum(not is_correct(e) for e in mine)
            cells.append(f"**{misses}/{len(mine)}**" if misses else f"0/{len(mine)}")
        lines.append(f"| {case} | {runs[0]['expect']} | {' | '.join(cells)} |")
    lines += ["", "What the judges said when they missed:", ""]
    for e in missed:
        verdict = "approved" if e["approved"] else "blocked"
        lines.append(f"- {e['model']}, {e['case']} (run {e['attempt']}), {verdict}: {e['detail']}")
    return "\n".join([*lines, ""])


def append_entry(directory: Path, entry: Entry) -> None:
    """Saved as each verdict comes in, so an interrupted run keeps what it finished."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / RUNS_FILE).open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry) + "\n")


def write_summary(directory: Path, entries: list[Entry]) -> Path:
    path = directory / SUMMARY_FILE
    path.write_text(judges_markdown(entries), encoding="utf-8")
    return path


class _Target(BaseModel):
    """The judges read the action's summary and, for a fee, the quote for its appointment."""

    appointment_id: str = APPOINTMENT_ID


class _NoBackend:
    """Judges read the conversation and the tool events, never the booking system."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"a judge called the booking system: {name}")


class _Recorder:
    """Passes calls through and keeps the model's raw reply, so its verdict can be told apart
    from the guard's: the fee judge's quote check can overrule the model."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self.reply: str | None = None
        self.error: str | None = None

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        json_mode: bool = False,
    ) -> Completion:
        try:
            completion = await self.llm.complete(messages, tools=tools, json_mode=json_mode)
        except Exception as exc:
            self.error = repr(exc)
            raise
        self.reply = completion.text
        return completion


@cache
def _business() -> BusinessProfile:
    return BusinessProfile.load(PROFILE)


def _context(case: JudgeCase) -> ToolContext:
    transcript = [
        ChatMessage.user(text) if speaker == "customer" else ChatMessage.assistant(text)
        for line in case.conversation
        for speaker, text in line.items()
    ]
    events = []
    if case.fee:
        events.append(
            ToolEvent(
                id="evt_quote",
                call_id="call_quote",
                tool="get_cancellation_quote",
                side_effect="read",
                arguments={"appointment_id": APPOINTMENT_ID},
                status="ok",
                result={"quote": {"fee": case.fee}},
                created_at=NOW,
            )
        )
    return ToolContext(
        conversation_id=f"judge-{case.id}",
        business=_business(),
        backend=cast(BookingBackend, _NoBackend()),
        now=NOW,
        transcript=transcript,
        events=events,
    )


def _model_verdict(case: JudgeCase, reply: str | None) -> bool | None:
    key = "confirmed" if case.judge == "confirmation" else "agreed"
    try:
        return json.loads(reply or "").get(key) is True
    except (ValueError, AttributeError):
        return None
