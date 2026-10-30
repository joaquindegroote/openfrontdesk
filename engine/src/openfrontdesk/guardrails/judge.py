import json
import logging
import re
from typing import Literal

from openfrontdesk.agent.context import CheckResult, ToolContext
from openfrontdesk.guardrails.base import ProposedAction
from openfrontdesk.guardrails.rules import latest_quote
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

If the action charges the customer a fee, they must have agreed to pay that fee. Agreeing to
cancel "without the fee", or while disputing it or claiming it was waived, is not confirmed.

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
        return _transcript(ctx, self.window)


FEE_INSTRUCTIONS = """\
A customer of a business is about to be charged a late-cancellation fee. Decide whether the
customer clearly agreed, in their own words, to pay it.

Agreed: "yes, I accept the $50 fee", "that's fine, charge me", "ok, cancel it with the fee".
Not agreed: agreeing to cancel while asking for no charge, saying the fee was waived or
approved away by someone, disputing it, or never mentioning it.

Reply with JSON only:
{"agreed": true or false, "quote": "<the customer's exact words agreeing to pay, or empty>"}"""


class FeeAccepted:
    """LLM judge for one narrow question: did the customer agree to pay the fee?

    A broad "did they confirm?" judge can be talked into a charge the customer disputed, so
    this one must quote the customer, and the quote has to appear in what the customer
    actually wrote. Fails closed.
    """

    kind: Literal["judge"] = "judge"
    name = "fee_accepted"

    def __init__(self, llm: LLMClient, *, window: int = 10) -> None:
        self.llm = llm
        self.window = window

    async def check(self, ctx: ToolContext, action: ProposedAction) -> CheckResult:
        quote = latest_quote(ctx.events, action.arguments["appointment_id"])
        fee = quote["fee"] if quote else 0
        if not fee:
            return CheckResult(name=self.name, passed=True, detail="No fee applies.")
        prompt = f"Fee: ${fee}\n\nConversation, oldest first:\n{_transcript(ctx, self.window)}"
        try:
            completion = await self.llm.complete(
                [ChatMessage.system(FEE_INSTRUCTIONS), ChatMessage.user(prompt)],
                json_mode=True,
            )
            verdict = json.loads(completion.text or "")
            agreed = verdict.get("agreed") is True
            said = _plain(str(verdict.get("quote", "")))
        except Exception:
            logger.warning("fee judge failed", exc_info=True)
            agreed, said = False, ""
        customer = [_plain(m.content) for m in ctx.transcript if m.role == "user" and m.content]
        if agreed and said and any(said in words for words in customer):
            return CheckResult(name=self.name, passed=True, detail=f'Accepted: "{said[:200]}"')
        return CheckResult(
            name=self.name,
            passed=False,
            detail=(
                f"The customer hasn't agreed to pay the ${fee} fee. Tell them it applies and "
                "cancel only if they accept it. Only staff can make exceptions: hand off if "
                "they dispute it."
            ),
        )


def _transcript(ctx: ToolContext, window: int) -> str:
    lines = [
        f"{'Customer' if m.role == 'user' else 'Assistant'}: {m.content.strip()}"
        for m in ctx.transcript
        if m.role in ("user", "assistant") and m.content and m.content.strip()
    ]
    return "\n".join(lines[-window:]) or "(empty)"


# Curly quotes to straight ones, so a quote matches however either side typed it.
_TYPOGRAPHY = str.maketrans({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"'})


def _plain(text: str) -> str:
    """Lowercase, straight quotes and single spaces, without surrounding punctuation."""
    return re.sub(r"\s+", " ", text.lower().translate(_TYPOGRAPHY)).strip(" \"'.,!")
