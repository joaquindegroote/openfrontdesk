"""Composition root: builds a ConversationService from settings.

The HTTP API and the eval runner both use it, so they exercise exactly the same engine.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from openfrontdesk.agent.executor import GuardMode, ToolExecutor
from openfrontdesk.agent.loop import Agent
from openfrontdesk.backends import BookingBackend
from openfrontdesk.backends.clinic_http import ClinicHttpBackend
from openfrontdesk.business import BusinessProfile
from openfrontdesk.llm import LLMClient
from openfrontdesk.llm.anthropic_client import AnthropicChatClient
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
    guards: GuardMode = "full",
) -> AsyncIterator[ConversationService]:
    """Yield a ready service. Whatever isn't injected is built from settings and closed on exit.

    `guards` is for ablation evals only; the HTTP API never sets it.
    """
    db = Database(settings.database_url)
    await db.create_all()
    booking = backend or ClinicHttpBackend.from_url(
        settings.backend_url, timeout_s=settings.backend_timeout_s
    )
    agent_llm = llm or llm_client(settings, settings.llm_model, settings.llm_reasoning_effort)
    judge = (
        judge_llm
        or llm_client(settings, settings.judge_model, settings.judge_reasoning_effort)
        or agent_llm
    )
    agent = None
    if agent_llm is not None and judge is not None:
        tools = build_booking_tools(judge)
        agent = Agent(
            agent_llm,
            tools,
            executor=ToolExecutor(tools, timeout_s=settings.tool_timeout_s, guards=guards),
            max_steps=settings.max_agent_steps,
        )
    else:
        logger.warning("no LLM configured; set the API key for the configured models")
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


def llm_client(settings: Settings, model: str, effort: str | None) -> LLMClient | None:
    """The client for a model name: "claude-..." runs on Anthropic, anything else on OpenAI.
    None when that provider's API key isn't set."""
    if model.startswith("claude-"):
        if settings.anthropic_api_key is None:
            return None
        return AnthropicChatClient(
            model, api_key=settings.anthropic_api_key.get_secret_value(), effort=effort
        )
    if settings.openai_api_key is None:
        return None
    return OpenAIChatClient(
        model, api_key=settings.openai_api_key.get_secret_value(), reasoning_effort=effort
    )
