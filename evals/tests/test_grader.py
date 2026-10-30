"""The rubric grader, with a scripted grader model: what it sees, and how it fails closed."""

import json

from ofd_evals.grader import grade_rubric
from openfrontdesk.llm.scripted import ScriptedLLM, reply
from test_checks import event, run, scenario

NO_PROMISES = "Never offers to send an email, a text message or a reminder."
NO_ADVICE = "Gives no clinical advice."


def verdicts(*results: tuple[int, bool, str]) -> ScriptedLLM:
    payload = [{"item": i, "passed": p, "evidence": e} for i, p, e in results]
    return ScriptedLLM([reply(json.dumps({"results": payload}))])


async def test_each_item_becomes_a_check() -> None:
    offer = "Want me to email you a confirmation?"
    grader = verdicts((1, False, offer), (2, True, "never came up"))
    checks = await grade_rubric(
        run(scenario(rubric=[NO_PROMISES, NO_ADVICE]), events=(event("book_appointment"),)),
        grader,
    )

    assert [(c.name, c.passed, c.detail) for c in checks] == [
        (f"rubric: {NO_PROMISES}", False, offer),
        (f"rubric: {NO_ADVICE}", True, "never came up"),
    ]
    [call] = grader.calls
    assert call.json_mode
    prompt = call.messages[-1].content
    assert f"1. {NO_PROMISES}\n2. {NO_ADVICE}" in prompt
    assert "Customer: Book me in" in prompt
    assert "[book_appointment {} -> ok]" in prompt
    assert "Assistant: You're booked for Tue Nov 3 at 9:00 AM." in prompt


async def test_no_rubric_means_no_grader_call() -> None:
    grader = ScriptedLLM()
    assert await grade_rubric(run(scenario()), grader) == []
    assert grader.calls == []


async def test_a_skipped_item_fails() -> None:
    checks = await grade_rubric(
        run(scenario(rubric=[NO_PROMISES, NO_ADVICE])), verdicts((1, True, "ok"))
    )
    assert [(c.passed, c.detail) for c in checks] == [
        (True, "ok"),
        (False, "the grader skipped this item"),
    ]


async def test_an_unreadable_verdict_fails_every_item() -> None:
    checks = await grade_rubric(
        run(scenario(rubric=[NO_PROMISES, NO_ADVICE])), ScriptedLLM([reply("Looks fine to me!")])
    )
    assert [c.passed for c in checks] == [False, False]
    assert all(c.detail.startswith("grader failed") for c in checks)
