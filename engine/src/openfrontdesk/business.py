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
    website: str | None = None
    opening_hours: list[str]
    about: str
    tone: str
    policies: list[str]
    handoff_when: list[str]
    faq: list[FAQ] = []

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
