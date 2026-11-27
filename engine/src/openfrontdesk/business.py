from datetime import datetime
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel


class FAQ(BaseModel):
    question: str
    answer: str


class BusinessProfile(BaseModel):
    """What the assistant knows about the business beyond its booking system.

    Availability, prices and fees always come from the booking system. The profile holds the
    things a front-desk coordinator would know by heart: hours, policies, tone, when to escalate.
    """

    id: str
    name: str
    assistant_name: str
    language: str = "English"
    timezone: str
    address: str
    phone: str
    # How customers here write a phone without its country code: the code to put in front,
    # and how many digits such a number has. "56" and 9 in Chile, "1" and 10 in the US.
    # Without them, a number counts as written in full.
    calling_code: str | None = None
    local_phone_digits: int | None = None
    website: str | None = None
    opening_hours: list[str]
    about: str
    tone: str
    policies: list[str]
    handoff_when: list[str]
    # What the customer reads when a person takes over, by handoff reason, with "default" for
    # the rest. Fixed words rather than generated ones: a handoff never repeats the customer's
    # claims or promises an outcome.
    handoff_replies: dict[str, str] = {}
    # What the customer reads when they ask about another person. Fixed for the same reason:
    # a generated refusal tends to add "for details about her cleaning", which says she has one.
    someone_else_reply: str = (
        "I can only help with your own appointments, but {name} is welcome to contact us directly."
    )
    faq: list[FAQ] = []

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def local(self, when: datetime) -> datetime:
        """A time the model wrote without an offset is in the business's timezone."""
        return when if when.tzinfo else when.replace(tzinfo=self.tz)

    def phone_key(self, phone: str) -> str:
        """Digits with the country code, so "+56 9 5010 0102" and "9 5010 0102" compare equal,
        however the customer or the model wrote them."""
        digits = "".join(ch for ch in phone if ch.isdigit())
        local = not phone.strip().startswith("+") and len(digits) == self.local_phone_digits
        return f"{self.calling_code}{digits}" if local and self.calling_code else digits

    def handoff_reply(self, reason: str, fallback: str) -> str:
        return self.handoff_replies.get(reason) or self.handoff_replies.get("default") or fallback

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
