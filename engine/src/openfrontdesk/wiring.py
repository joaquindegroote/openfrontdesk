"""Composition root: builds a ConversationService from settings.

The HTTP API and the eval runner both use it, so they exercise exactly the same engine.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from openfrontdesk.agent.executor import ToolExecutor
from openfrontdesk.agent.loop import Agent
from openfrontdesk.backends import BookingBackend
from openfrontdesk.backends.clinic_http import ClinicHttpBackend
from openfrontdesk.business import BusinessProfile
from openfrontdesk.llm import LLMClient
from openfrontdesk.llm.openai_client import OpenAIChatClient
from openfrontdesk.service import ConversationService
from openfrontdesk.settings import Settings
from openfrontdesk.storage import ConversationRepository, Database
from openfrontdesk.tools.booking import build_booking_tools

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_conversation_service(
    settings: Settings,
    *,
    business: BusinessProfile | None = None,
    llm: LLMClient | None = None,
    judge_llm: LLMClient | None = None,
    backend: BookingBackend | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AsyncIterator[ConversationService]:
    """Yield a ready service. Whatever isn't injected is built from settings and closed on exit."""
    db = Database(settings.database_url)
    await db.create_all()
    booking = backend or ClinicHttpBackend.from_url(
        settings.backend_url, timeout_s=settings.backend_timeout_s
    )
    agent_llm = llm or openai_client(settings, settings.llm_model, settings.llm_reasoning_effort)
    judge = (
        judge_llm
        or openai_client(settings, settings.judge_model, settings.judge_reasoning_effort)
        or agent_llm
    )
    agent = None
    if agent_llm is not None and judge is not None:
        tools = build_booking_tools(judge)
        agent = Agent(
            agent_llm,
            tools,
            executor=ToolExecutor(tools, timeout_s=settings.tool_timeout_s),
            max_steps=settings.max_agent_steps,
        )
    else:
        logger.warning("no LLM configured; set OPENAI_API_KEY to enable the assistant")
    try:
        yield ConversationService(
            repo=ConversationRepository(db),
            agent=agent,
            business=business or BusinessProfile.load(settings.business_profile),
            backend=booking,
            clock=clock,
            turn_timeout_s=settings.turn_timeout_s,
        )
    finally:
        if backend is None:
            await booking.aclose()
        await db.dispose()


def openai_client(settings: Settings, model: str, effort: str | None) -> OpenAIChatClient | None:
    if settings.openai_api_key is None:
        return None
    return OpenAIChatClient(
        model, api_key=settings.openai_api_key.get_secret_value(), reasoning_effort=effort
    )
