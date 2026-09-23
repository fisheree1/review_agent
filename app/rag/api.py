from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.core.auth import Principal, get_current_principal
from app.core.config import RagSettings
from app.core.database import async_session_factory
from app.documents.schemas import CitationLocatorResponse
from app.rag.application import RagService
from app.rag.domain import Scope
from app.rag.store import SqlRagStore

router = APIRouter(prefix="/api/v1/documents/{document_id}", tags=["rag"])


class IndexResponse(BaseModel):
    status: Literal["not_indexed", "queued", "processing", "ready", "failed"]
    completed: int
    total: int
    failure_code: str | None
    failure_message: str | None


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


class CitationResponse(BaseModel):
    source_id: UUID
    quote: str
    unit: int
    locator: CitationLocatorResponse
    document_id: UUID | None = None
    version_id: int | None = None


class ClaimResponse(BaseModel):
    text: str
    citations: list[CitationResponse]


class AnswerResponse(BaseModel):
    insufficient_evidence: bool
    claims: list[ClaimResponse]


class QuestionResponse(BaseModel):
    id: UUID
    question: str
    status: Literal["queued", "processing", "answered", "insufficient", "failed", "cancelled"]
    answer: AnswerResponse | None
    version: int
    failure_code: str | None
    failure_message: str | None


def get_rag_service() -> RagService:
    return RagService(SqlRagStore(async_session_factory, RagSettings().profile))


Auth = Annotated[Principal, Depends(get_current_principal)]
Service = Annotated[RagService, Depends(get_rag_service)]
RequestKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]


@router.get("/index", response_model=IndexResponse)
async def index_status(document_id: UUID, principal: Auth, service: Service) -> IndexResponse:
    return IndexResponse.model_validate(
        await service.index(Scope(principal.workspace_public_id, document_id))
    )


@router.post("/index", response_model=IndexResponse, status_code=202)
async def start_index(
    document_id: UUID, principal: Auth, service: Service, idempotency_key: RequestKey
) -> IndexResponse:
    return IndexResponse.model_validate(
        await service.index(Scope(principal.workspace_public_id, document_id), idempotency_key)
    )


@router.post("/questions", response_model=QuestionResponse, status_code=202)
async def ask(
    document_id: UUID,
    body: AskRequest,
    principal: Auth,
    service: Service,
    idempotency_key: RequestKey,
) -> QuestionResponse:
    return QuestionResponse.model_validate(
        await service.ask(
            Scope(principal.workspace_public_id, document_id), idempotency_key, body.question
        )
    )


@router.get("/questions", response_model=list[QuestionResponse])
async def question_history(
    document_id: UUID, principal: Auth, service: Service
) -> list[QuestionResponse]:
    return [
        QuestionResponse.model_validate(question)
        for question in await service.history(Scope(principal.workspace_public_id, document_id))
    ]


@router.get("/questions/{question_id}", response_model=QuestionResponse)
async def get_question(
    document_id: UUID, question_id: UUID, principal: Auth, service: Service
) -> QuestionResponse:
    return QuestionResponse.model_validate(
        await service.question(Scope(principal.workspace_public_id, document_id), question_id)
    )


@router.post("/questions/{question_id}:cancel", response_model=QuestionResponse)
async def cancel_question(
    document_id: UUID, question_id: UUID, principal: Auth, service: Service
) -> QuestionResponse:
    return QuestionResponse.model_validate(
        await service.cancel(Scope(principal.workspace_public_id, document_id), question_id)
    )
