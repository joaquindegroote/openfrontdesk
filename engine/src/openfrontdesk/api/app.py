"""HTTP API. Run with: uvicorn openfrontdesk.api.app:create_app --factory --port 8000"""

import hmac
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from openfrontdesk import __version__
from openfrontdesk.api.schemas import (
    BusinessOut,
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    CustomerMessageIn,
    CustomerMessageOut,
    MessageOut,
    StaffMessageIn,
    TakeOverIn,
    TurnOut,
    visible_messages,
)
from openfrontdesk.backends import BookingBackend
from openfrontdesk.business import BusinessProfile
from openfrontdesk.llm import LLMClient
from openfrontdesk.observability import configure_tracing
from openfrontdesk.service import (
    AssistantNotConfiguredError,
    ConversationNotFoundError,
    ConversationService,
)
from openfrontdesk.settings import Settings
from openfrontdesk.wiring import open_conversation_service

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    judge_llm: LLMClient | None = None,
    backend: BookingBackend | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Build the app. Tests inject fakes for the LLMs, the booking backend and the clock."""
    settings = settings or Settings()
    business = BusinessProfile.load(settings.business_profile)
    configure_tracing(settings.otlp_endpoint)
    if settings.integration_key is None:
        logger.warning("no integration key: any client can open a verified-channel conversation")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_conversation_service(
            settings, business=business, llm=llm, judge_llm=judge_llm, backend=backend, clock=clock
        ) as service:
            app.state.service = service
            yield

    app = FastAPI(title="openfrontdesk", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ConversationNotFoundError)
    async def not_found(_: Request, exc: ConversationNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"Conversation {exc} not found"})

    @app.exception_handler(AssistantNotConfiguredError)
    async def not_configured(_: Request, exc: AssistantNotConfiguredError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(_routes(business, settings.integration_key))
    return app


def _service(request: Request) -> ConversationService:
    return request.app.state.service


ServiceDep = Annotated[ConversationService, Depends(_service)]


def _routes(business: BusinessProfile, integration_key: SecretStr | None) -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.get("/business")
    async def get_business() -> BusinessOut:
        return BusinessOut.model_validate(business.model_dump())

    @router.post("/conversations", status_code=201)
    async def start_conversation(
        body: ConversationCreate,
        service: ServiceDep,
        x_integration_key: Annotated[str | None, Header()] = None,
    ) -> ConversationOut:
        # On WhatsApp, SMS or a call the phone number is proven by the carrier, so only a trusted
        # integration may open such a conversation. A web visitor's number is just a claim.
        if (
            body.channel != "web"
            and integration_key is not None
            and not hmac.compare_digest(x_integration_key or "", integration_key.get_secret_value())
        ):
            raise HTTPException(401, "Only an integration can open this kind of conversation.")
        conversation = await service.start(
            channel=body.channel,
            customer_phone=body.customer_phone,
            customer_name=body.customer_name,
        )
        return ConversationOut.model_validate(conversation)

    @router.get("/conversations")
    async def list_conversations(
        service: ServiceDep, status: str | None = None
    ) -> list[ConversationOut]:
        rows = await service.repo.list_conversations(status=status)
        return [ConversationOut.model_validate(r) for r in rows]

    @router.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, service: ServiceDep) -> ConversationDetail:
        conversation = await service.get(conversation_id)
        return ConversationDetail(
            conversation=ConversationOut.model_validate(conversation),
            messages=visible_messages(await service.repo.messages(conversation_id)),
            events=await service.repo.events(conversation_id),
            turns=[TurnOut.model_validate(t) for t in await service.repo.turns(conversation_id)],
        )

    @router.post("/conversations/{conversation_id}/messages")
    async def send_customer_message(
        conversation_id: str, body: CustomerMessageIn, service: ServiceDep
    ) -> CustomerMessageOut:
        outcome = await service.handle_customer_message(conversation_id, body.text)
        return CustomerMessageOut(
            conversation=ConversationOut.model_validate(outcome.conversation),
            messages=visible_messages(outcome.messages),
            events=outcome.events,
            turn=TurnOut.model_validate(outcome.turn) if outcome.turn else None,
        )

    @router.post("/conversations/{conversation_id}/staff-messages", status_code=201)
    async def send_staff_message(
        conversation_id: str, body: StaffMessageIn, service: ServiceDep
    ) -> MessageOut:
        row = await service.staff_reply(conversation_id, body.text, body.author)
        return visible_messages([row])[0]

    @router.post("/conversations/{conversation_id}/takeover")
    async def take_over(
        conversation_id: str, body: TakeOverIn, service: ServiceDep
    ) -> ConversationOut:
        conversation = await service.take_over(
            conversation_id, reason=body.reason, summary=body.summary
        )
        return ConversationOut.model_validate(conversation)

    @router.post("/conversations/{conversation_id}/resume")
    async def resume_assistant(conversation_id: str, service: ServiceDep) -> ConversationOut:
        return ConversationOut.model_validate(await service.resume_assistant(conversation_id))

    return router
