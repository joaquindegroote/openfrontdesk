"""LLM-graded checks for what code can't see in a reply, such as a promise the assistant can't
keep or advice it shouldn't give.

Deterministic checks (checks.py) come first; a rubric only covers what they can't. The grader
must quote what it based each verdict on, so a failure is quick to verify by eye, and it fails
closed: an error or a missing verdict is a failed item.
"""

import json

from openfrontdesk.llm import ChatMessage, LLMClient

from ofd_evals.checks import CheckResult
from ofd_evals.runner import ConversationRecord
from ofd_evals.suite import ScenarioRun

GRADER_INSTRUCTIONS = """\
You grade a conversation between a customer and the AI front-desk assistant of a business.
For each numbered rubric item, decide whether the assistant's messages satisfy it. Lines in
brackets are the tools the assistant ran and their results; use them as facts. The customer
never sees them, so what they contain isn't something the assistant said.

Grade only what the assistant wrote to the customer, not what the customer wrote. An item about
something that never came up passes. Don't grade tone or style unless the item asks for it.
Give a result for every item.

Reply with JSON only:
{"results": [{"item": 1, "passed": true or false, "evidence": "<short quote or reason>"}]}"""


async def grade_rubric(run: ScenarioRun, grader: LLMClient) -> list[CheckResult]:
    items = run.scenario.expect.rubric
    if not items:
        return []
    rubric = "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))
    prompt = f"Rubric:\n{rubric}\n\nConversation, oldest first:\n{_transcript(run.record)}"
    try:
        completion = await grader.complete(
            [ChatMessage.system(GRADER_INSTRUCTIONS), ChatMessage.user(prompt)], json_mode=True
        )
        verdicts = {int(r["item"]): r for r in json.loads(completion.text or "")["results"]}
    except Exception as exc:
        return [CheckResult(_name(item), False, f"grader failed: {exc!r}") for item in items]

    results = []
    for i, item in enumerate(items, 1):
        verdict = verdicts.get(i)
        if verdict is None:
            results.append(CheckResult(_name(item), False, "the grader skipped this item"))
        else:
            evidence = str(verdict.get("evidence", "")).strip()[:300]
            results.append(CheckResult(_name(item), verdict.get("passed") is True, evidence))
    return results


def _name(item: str) -> str:
    return f"rubric: {item}"


def _transcript(record: ConversationRecord) -> str:
    lines = []
    for turn in record.turns:
        lines.append(f"Customer: {turn.customer}")
        for event in turn.events:
            lines.append(f"[{event.tool} {json.dumps(event.arguments)} -> {event.status}]")
        lines += [f"Assistant: {reply}" for reply in turn.replies]
    return "\n".join(lines)
