"""Two engine processes on one Postgres: a conversation's turns still run one at a time, and a
message delivered to both is answered once. Needs a real Postgres, so it runs in CI and is
skipped without OFD_TEST_POSTGRES_URL."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
import pytest
from clinic_api.chaos import ChaosConfig
from clinic_api.clock import FixedClock
from clinic_api.main import create_app as create_clinic_app
from openfrontdesk.backends.clinic_http import ClinicHttpBackend
from openfrontdesk.llm.scripted import ScriptedLLM
from openfrontdesk.service import ConversationService
from openfrontdesk.storage.db import Database
from openfrontdesk.wiring import open_conversation_service
from support import NOW, VERIFICATION_CODE, engine_settings
from test_concurrency import SlowLLM

POSTGRES = os.environ.get("OFD_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES, reason="needs OFD_TEST_POSTGRES_URL")


@asynccontextmanager
async def two_workers(llm: SlowLLM) -> AsyncIterator[tuple[ConversationService, ...]]:
    """Two services with nothing in common but the database, as two processes would be. They
    share the model here only so the test can see whether their calls overlap."""
    clinic = create_clinic_app(
        FixedClock(NOW), chaos=ChaosConfig(), filler=False, verification_code=VERIFICATION_CODE
    )
    settings = engine_settings(database_url=POSTGRES)
    async with AsyncExitStack() as stack:
        workers = []
        for _ in range(2):
            http = await stack.enter_async_context(
                httpx.AsyncClient(transport=httpx.ASGITransport(app=clinic), base_url="http://c")
            )
            workers.append(
                await stack.enter_async_context(
                    open_conversation_service(
                        settings,
                        llm=llm,
                        judge_llm=ScriptedLLM(),
                        backend=ClinicHttpBackend(http, backoff_s=0),
                        clock=lambda: NOW,
                    )
                )
            )
        yield tuple(workers)


async def start(service: ConversationService) -> str:
    conversation = await service.start(channel="web", customer_phone=None, customer_name=None)
    return conversation.id


async def test_two_workers_take_turns_on_one_conversation() -> None:
    llm = SlowLLM()
    async with two_workers(llm) as (one, two):
        conversation = await start(one)
        await asyncio.gather(
            one.handle_customer_message(conversation, "Hi, I'd like a cleaning."),
            two.handle_customer_message(conversation, "Next week if possible."),
        )

    assert llm.most_at_once == 1
    # Whichever went second saw the other's message: the transcript didn't interleave.
    assert sorted(len(seen) for seen in llm.seen) == [1, 2]


async def test_two_workers_answer_one_message_once() -> None:
    llm = SlowLLM()
    async with two_workers(llm) as (one, two):
        conversation = await start(one)
        outcomes = await asyncio.gather(
            one.handle_customer_message(conversation, "Hi", message_id="wamid.1"),
            two.handle_customer_message(conversation, "Hi", message_id="wamid.1"),
        )

    assert len(llm.seen) == 1
    assert sorted(o.duplicate for o in outcomes) == [False, True]


async def test_different_conversations_don_t_wait_for_each_other() -> None:
    llm = SlowLLM()
    async with two_workers(llm) as (one, two):
        first, second = await start(one), await start(two)
        await asyncio.gather(
            one.handle_customer_message(first, "Hi, I'd like a cleaning."),
            two.handle_customer_message(second, "Can I book a check-up?"),
        )

    assert llm.most_at_once == 2


async def test_without_the_database_lock_they_would_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the lock is for: take it away, and the two workers run the same conversation at
    the same time."""

    @asynccontextmanager
    async def no_lock(self: Database, conversation_id: str) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(Database, "conversation_lock", no_lock)
    llm = SlowLLM()
    async with two_workers(llm) as (one, two):
        conversation = await start(one)
        await asyncio.gather(
            one.handle_customer_message(conversation, "Hi, I'd like a cleaning."),
            two.handle_customer_message(conversation, "Next week if possible."),
        )

    assert llm.most_at_once == 2
