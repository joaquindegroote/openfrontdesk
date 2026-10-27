"""A simulated customer: an LLM that role-plays a persona with a goal, one message at a time."""

import json
from dataclasses import dataclass
from typing import Literal

from openfrontdesk.llm import ChatMessage, LLMClient
from pydantic import BaseModel, Field

Speaker = Literal["customer", "assistant", "staff"]

INSTRUCTIONS = """\
You are role-playing a customer who is chatting with a business's front-desk assistant.
You are the customer, never the assistant. Write only the customer's next message.

- Write like a real person in a chat: short, natural, in {language}. {style}
- Pursue your goal. Answer the assistant's questions using only the facts you were given; if
  you're asked something you don't know, say so.
- React to what the assistant actually said. Don't invent appointments, times or prices.
- When you're offered options, pick the one that best fits your goal, or say why none fits.
  When you're asked to confirm details that match your goal, confirm them.
- Set "done" to true only when the assistant has said your request is completed (for example,
  the appointment is booked or cancelled), when it clearly can't be done, or when you've been
  told a person will take over. Being offered options or asked a question is never done.
  When done, leave "message" empty.

Reply with JSON only: {{"message": "<your next message, or empty>", "done": true or false}}"""


class Persona(BaseModel):
    name: str
    goal: str = Field(description="What the customer wants, in their own words.")
    # Known to the channel, like a WhatsApp number. None means an anonymous web visitor.
    phone: str | None = None
    facts: dict[str, str] = Field(
        default_factory=dict, description="What the customer can say if asked."
    )
    style: str = "Polite and brief."
    language: str = "English"


@dataclass(frozen=True)
class CustomerTurn:
    message: str
    done: bool


class SimulatorError(Exception):
    """The simulated customer produced something that isn't a valid turn."""


class SimulatedCustomer:
    def __init__(self, llm: LLMClient, persona: Persona) -> None:
        self.llm = llm
        self.persona = persona

    async def next_turn(self, transcript: list[tuple[Speaker, str]]) -> CustomerTurn:
        completion = await self.llm.complete(
            [
                ChatMessage.system(
                    INSTRUCTIONS.format(language=self.persona.language, style=self.persona.style)
                ),
                ChatMessage.user(self._brief(transcript)),
            ],
            json_mode=True,
        )
        try:
            turn = json.loads(completion.text or "")
            message = str(turn.get("message") or "").strip()
            done = turn.get("done") is True
        except (json.JSONDecodeError, AttributeError) as exc:
            raise SimulatorError(f"not a JSON turn: {completion.text!r}") from exc
        if not message and not done:
            raise SimulatorError("empty message without ending the conversation")
        return CustomerTurn(message=message, done=done)

    def _brief(self, transcript: list[tuple[Speaker, str]]) -> str:
        persona = self.persona
        facts = "\n".join(f"- {key}: {value}" for key, value in persona.facts.items())
        lines = [
            f"{'You' if speaker == 'customer' else speaker.capitalize()}: {text}"
            for speaker, text in transcript
        ]
        return (
            f"You are {persona.name}.\n"
            f"Your goal: {persona.goal}\n"
            f"Facts you can share if asked:\n{facts or '- (none)'}\n\n"
            "Conversation so far:\n"
            + ("\n".join(lines) if lines else "(none yet: write your first message)")
        )
