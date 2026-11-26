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
from openfrontdesk.llm.scripted import ScriptedCall, ScriptedLLM, reply

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "businesses" / "clinica-los-canelos" / "profile.yaml"
# Monday 8:00 AM at the clinic, before opening. Matches the clinic's frozen clock.
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=TIMEZONE)

JAVIERA = "+56950100101"  # pat_0001, cleaning today at 11:00 (apt_00001): late-cancellation fee
CARLOS = "+56950100102"  # pat_0002, check-up on Saturday (apt_00002): free to cancel
CAMILA = "+56950100103"  # pat_0003, no upcoming appointments
# The staff key in tests. The harness's client sends it, like the console does.
STAFF_KEY = "test-staff-key"
# The integration key in tests. The harness sends it too, so it can open WhatsApp conversations.
INTEGRATION_KEY = "test-integration-key"
# The mock clinic's code in tests, where nobody receives a text.
VERIFICATION_CODE = "246810"
# What the reference business says when a person takes over for a reason it has no text for.
DEFAULT_HANDOFF_REPLY = BusinessProfile.load(PROFILE).handoff_replies["default"]
# When the booking system keeps failing, the customer is told the request wasn't finished.
OUTAGE_HANDOFF_REPLY = BusinessProfile.load(PROFILE).handoff_replies["system_unavailable"]
# And when the guards stopped the same action three times in a row.
STUCK_HANDOFF_REPLY = BusinessProfile.load(PROFILE).handoff_replies["blocked_repeatedly"]


def whole_system_prompt(call: ScriptedCall) -> str:
    """Both parts of the system prompt, as the model reads them."""
    return "\n".join(m.content or "" for m in call.messages if m.role == "system")


def verdict(confirmed: bool, evidence: str = "", *, other_amount: bool = False) -> Completion:
    """A judge response."""
    answer = {"confirmed": confirmed, "other_amount": other_amount, "evidence": evidence}
    return reply(json.dumps(answer))


def fee_verdict(agreed: bool, quote: str = "", *, objects: bool = False) -> Completion:
    """A fee judge response."""
    return reply(json.dumps({"agreed": agreed, "objects": objects, "quote": quote}))


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

    def say(
        self, conversation_id: str, text: str, *, message_id: str | None = None
    ) -> dict[str, Any]:
        body = {"text": text} | ({"message_id": message_id} if message_id else {})
        response = self.client.post(f"/v1/conversations/{conversation_id}/messages", json=body)
        assert response.status_code == 200, response.text
        return response.json()


class HarnessFactory(Protocol):
    def __call__(
        self,
        *,
        chaos: ChaosConfig | None = None,
        max_steps: int = 8,
        turn_timeout_s: float = 45.0,
        staff_key: str | None = STAFF_KEY,
    ) -> Harness: ...
