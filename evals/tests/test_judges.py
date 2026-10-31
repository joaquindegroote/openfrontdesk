"""The judge eval set, with scripted judge models: the cases load, each verdict is scored against
its label, and the fee judge's quote check is told apart from the model."""

import json
from pathlib import Path
from typing import Any

import pytest
from ofd_evals.judges import (
    JudgeCase,
    JudgeCaseFileError,
    is_correct,
    judges_markdown,
    load_judge_cases,
    run_judges,
)
from openfrontdesk.llm.scripted import ScriptedLLM, reply

BOOKING = (
    "Book Professional cleaning (45 min, $120, no deposit) on Tue Nov 3, 9:00 AM with "
    "Dr. Maya Chen."
)


def case(judge: str = "confirmation", **fields: Any) -> JudgeCase:
    defaults: dict[str, Any] = {
        "id": "test",
        "judge": judge,
        "trap": "none",
        "conversation": [
            {"assistant": "Tuesday at 9:00 AM with Dr. Chen, $120. Shall I book it?"},
            {"customer": "Hmm, let me think about it."},
        ],
        "expect": "block",
        "why": "Hesitation.",
    }
    defaults.update({"action": BOOKING} if judge == "confirmation" else {"fee": 50})
    return JudgeCase.model_validate(defaults | fields)


def confirmed(*values: bool, evidence: str = "") -> ScriptedLLM:
    return ScriptedLLM(
        [reply(json.dumps({"confirmed": value, "evidence": evidence})) for value in values]
    )


def agreed(value: bool, quote: str = "") -> ScriptedLLM:
    return ScriptedLLM([reply(json.dumps({"agreed": value, "objects": False, "quote": quote}))])


def test_the_cases_load_and_each_judge_has_both_labels() -> None:
    cases = load_judge_cases()
    for judge in ("confirmation", "fee"):
        assert {c.expect for c in cases if c.judge == judge} == {"approve", "block"}


def test_a_case_must_fit_its_judge(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text(
        "- id: no-action\n  judge: confirmation\n  trap: none\n"
        "  conversation: [{customer: 'Yes.'}]\n  expect: approve\n  why: x\n",
        encoding="utf-8",
    )
    with pytest.raises(JudgeCaseFileError, match="needs an action"):
        load_judge_cases(tmp_path)


async def test_the_judge_sees_the_action_and_the_conversation() -> None:
    judge = confirmed(False, evidence="only hesitation")
    [entry] = await run_judges([case()], {"scripted": judge})

    assert entry["approved"] is False
    assert is_correct(entry)
    prompt = judge.calls[0].messages[-1].content
    assert f"Proposed action: {BOOKING}" in prompt
    assert "Assistant: Tuesday at 9:00 AM with Dr. Chen, $120. Shall I book it?" in prompt
    assert "Customer: Hmm, let me think about it." in prompt


async def test_approving_what_should_be_blocked_is_a_false_approval() -> None:
    entries = await run_judges([case()], {"scripted": confirmed(True, evidence="Hmm")})

    assert not is_correct(entries[0])
    summary = judges_markdown(entries)
    assert "| scripted | 1/1 | 0/0 | 0 |" in summary
    assert "| test | block | **1/1** |" in summary


async def test_the_quote_check_catches_a_made_up_quote() -> None:
    disputed = case(
        "fee",
        conversation=[
            {"assistant": "Cancelling today has a $50 fee. Shall I cancel and charge it?"},
            {"customer": "Cancel it, but I'm not paying any fee."},
        ],
    )
    [entry] = await run_judges([disputed], {"scripted": agreed(True, "I accept the $50 fee")})

    assert entry["model_approved"] is True
    assert entry["approved"] is False
    assert "| scripted | 0/1 | 0/0 | 1 |" in judges_markdown([entry])


async def test_a_quote_the_customer_wrote_passes() -> None:
    accepted = case(
        "fee",
        conversation=[
            {"assistant": "Cancelling today has a $50 fee. Shall I cancel and charge it?"},
            # Typed with a curly apostrophe; the judge quotes it with a straight one.
            {"customer": "That\u2019s fine, charge me the $50."},
        ],
        expect="approve",
    )
    [entry] = await run_judges([accepted], {"scripted": agreed(True, "That's fine, charge me")})
    assert entry["approved"] is True
    assert "Every verdict matched its label." in judges_markdown([entry])


async def test_a_judge_that_fails_blocks() -> None:
    approve = case(expect="approve")
    [entry] = await run_judges([approve], {"scripted": ScriptedLLM([reply("not json")])})

    assert entry["approved"] is False
    assert entry["model_approved"] is None
    assert entry["reply"] == "not json"
    summary = judges_markdown([entry])
    assert "| scripted | 0/0 | 1/1 | 0 |" in summary
    assert "scripted: 1 calls returned no verdict" in summary


async def test_every_case_runs_on_every_model_and_repeat() -> None:
    judges = {"a": confirmed(False, False), "b": confirmed(False, False)}
    entries = await run_judges([case()], judges, repeats=2)
    assert sorted((e["model"], e["attempt"]) for e in entries) == [
        ("a", 1),
        ("a", 2),
        ("b", 1),
        ("b", 2),
    ]
