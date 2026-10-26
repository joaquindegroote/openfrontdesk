import json
import logging
from typing import Literal

from openfrontdesk.agent.context import CheckResult, ToolContext
from openfrontdesk.guardrails.base import ProposedAction
from openfrontdesk.llm import ChatMessage, LLMClient

logger = logging.getLogger(__name__)

JUDGE_INSTRUCTIONS = """\
You review actions proposed by an AI front-desk assistant, before they are executed.
Decide whether the customer explicitly confirmed the exact action described.

Confirmed means all of these are true:
- Before the customer's confirmation, the assistant clearly stated the key details of this
  action: what it is, the date and time, and any price, deposit or fee.
- The customer's latest relevant message clearly agrees ("yes", "book it", "that works",
  "go ahead", "please cancel it").
- What the customer agreed to is the same action: same service, date, time, and the same price,
  deposit or fee.

The action description is written by the system, so names may be worded or abbreviated
differently than in the conversation. Judge whether it is the same service, practitioner, time
and amount, not whether the wording matches.

Not confirmed: questions, hesitation, agreement to a different option, or a confirmation given
before the details changed.

Reply with JSON only: {"confirmed": true or false, "evidence": "<short quote or reason>"}"""


class CustomerConfirmed:
    """LLM judge: did the customer explicitly agree to this exact action? Fails closed."""

    kind: Literal["judge"] = "judge"
    name = "customer_confirmed"

    def __init__(self, llm: LLMClient, *, window: int = 10) -> None:
        self.llm = llm
        self.window = window

    async def check(self, ctx: ToolContext, action: ProposedAction) -> CheckResult:
        prompt = (
            f"Proposed action: {action.summary}\n\n"
            f"Conversation, oldest first:\n{self._transcript(ctx)}"
        )
        try:
            completion = await self.llm.complete(
                [ChatMessage.system(JUDGE_INSTRUCTIONS), ChatMessage.user(prompt)],
                json_mode=True,
            )
            verdict = json.loads(completion.text or "")
            confirmed = verdict.get("confirmed") is True
            evidence = str(verdict.get("evidence", "")).strip()[:300]
        except Exception:
            logger.warning("confirmation judge failed", exc_info=True)
            return CheckResult(
                name=self.name,
                passed=False,
                detail="Could not verify the customer's confirmation. Ask them to confirm.",
            )
        if confirmed:
            return CheckResult(name=self.name, passed=True, detail=f"Confirmed: {evidence}")
        return CheckResult(
            name=self.name,
            passed=False,
            detail=(
                f"The customer has not confirmed this yet ({evidence}). State the exact details "
                "and ask them to confirm before trying again."
            ),
        )

    def _transcript(self, ctx: ToolContext) -> str:
        lines = [
            f"{'Customer' if m.role == 'user' else 'Assistant'}: {m.content.strip()}"
            for m in ctx.transcript
            if m.role in ("user", "assistant") and m.content and m.content.strip()
        ]
        return "\n".join(lines[-self.window :]) or "(empty)"
