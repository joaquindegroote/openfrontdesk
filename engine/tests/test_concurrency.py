"""Messages that arrive at the same time: one conversation's turns run one after another, while
different conversations run side by side."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx
from clinic_api.chaos import ChaosConfig
from clinic_api.clock import FixedClock
from clinic_api.main import create_app as create_clinic_app
from openfrontdesk.backends.clinic_http import ClinicHttpBackend
from openfrontdesk.llm import ChatMessage, Completion, ToolSpec
from openfrontdesk.llm.scripted import ScriptedLLM, reply
from openfrontdesk.service import ConversationService
from openfrontdesk.settings import Settings
from openfrontdesk.wiring import open_conversation_service
from support import NOW, PROFILE, VERIFICATION_CODE


class SlowLLM:
    """Takes a moment to answer, and records how many calls overlap and what each one saw."""

    model = "slow"

    def __init__(self) -> None:
        self.running = 0
        self.most_at_once = 0
        self.seen: list[list[str]] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] = (),
        json_mode: bool = False,
    ) -> Completion:
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        await asyncio.sleep(0.05)
        self.running -= 1
        self.seen.append([m.content or "" for m in messages if m.role == "user"])
        return reply("Got it.")


@asynccontextmanager
async def service_on(llm: SlowLLM) -> AsyncIterator[ConversationService]:
    clinic = create_clinic_app(
        FixedClock(NOW), chaos=ChaosConfig(), filler=False, verification_code=VERIFICATION_CODE
    )
    transport = httpx.ASGITransport(app=clinic)
    async with httpx.AsyncClient(transport=transport, base_url="http://clinic") as http:
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url="sqlite+aiosqlite:///:memory:",
            business_profile=PROFILE,
        )
        async with open_conversation_service(
            settings,
            llm=llm,
            judge_llm=ScriptedLLM(),
            backend=ClinicHttpBackend(http, backoff_s=0),
            clock=lambda: NOW,
        ) as service:
            yield service


async def start(service: ConversationService) -> str:
    conversation = await service.start(channel="web", customer_phone=None, customer_name=None)
    return conversation.id


async def test_two_messages_at_once_take_turns() -> None:
    llm = SlowLLM()
    async with service_on(llm) as service:
        conversation = await start(service)
        await asyncio.gather(
            service.handle_customer_message(conversation, "Hi, I'd like a cleaning."),
            service.handle_customer_message(conversation, "Next week if possible."),
        )

    assert llm.most_at_once == 1
    # The second turn ran after the first had finished, so it saw the first message too.
    first, second = llm.seen
    assert first == ["Hi, I'd like a cleaning."]
    assert second == ["Hi, I'd like a cleaning.", "Next week if possible."]


async def test_different_conversations_run_side_by_side() -> None:
    llm = SlowLLM()
    async with service_on(llm) as service:
        one, two = await start(service), await start(service)
        await asyncio.gather(
            service.handle_customer_message(one, "Hi, I'd like a cleaning."),
            service.handle_customer_message(two, "Can I book a check-up?"),
        )

    assert llm.most_at_once == 2
