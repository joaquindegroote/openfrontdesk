"""Shared test helpers: an engine wired to the real clinic API in-process, with scripted LLMs."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from clinic_api.catalog import TIMEZONE
from clinic_api.chaos import ChaosConfig
from clinic_api.store import ClinicStore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openfrontdesk.business import BusinessProfile
from openfrontdesk.llm import Completion
from openfrontdesk.llm.scripted import ScriptedLLM, reply

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "businesses" / "brightsmile-dental" / "profile.yaml"
# Monday 8:00 AM at the clinic, before opening. Matches the clinic's frozen clock.
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=TIMEZONE)

EMILY = "+15550100101"  # pat_0001, cleaning today at 11:00 (apt_00001): late-cancellation fee
CARLOS = "+15550100102"  # pat_0002, check-up on Saturday (apt_00002): free to cancel
PRIYA = "+15550100103"  # pat_0003, no upcoming appointments
# The mock clinic's code in tests, where nobody receives a text.
VERIFICATION_CODE = "246810"
# What the reference business says when a person takes over for a reason it has no text for.
DEFAULT_HANDOFF_REPLY = BusinessProfile.load(PROFILE).handoff_replies["default"]


def verdict(confirmed: bool, evidence: str = "") -> Completion:
    """A judge response."""
    return reply(json.dumps({"confirmed": confirmed, "evidence": evidence}))


def fee_verdict(agreed: bool, quote: str = "") -> Completion:
    """A fee judge response."""
    return reply(json.dumps({"agreed": agreed, "quote": quote}))


@dataclass
class Harness:
    client: TestClient
    llm: ScriptedLLM
    judge: ScriptedLLM
    clinic: FastAPI

    @property
    def store(self) -> ClinicStore:
        return self.clinic.state.store

    def start(self, phone: str | None = None, *, channel: str | None = None) -> str:
        # A phone means a channel that proves it (WhatsApp); without one, a web visitor.
        body = {"customer_phone": phone, "channel": channel or ("whatsapp" if phone else "web")}
        response = self.client.post("/v1/conversations", json=body)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def say(self, conversation_id: str, text: str) -> dict[str, Any]:
        response = self.client.post(
            f"/v1/conversations/{conversation_id}/messages", json={"text": text}
        )
        assert response.status_code == 200, response.text
        return response.json()


class HarnessFactory(Protocol):
    def __call__(
        self, *, chaos: ChaosConfig | None = None, max_steps: int = 8, turn_timeout_s: float = 45.0
    ) -> Harness: ...
