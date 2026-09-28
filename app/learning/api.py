from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field

from app.core.auth import Principal, get_current_principal
from app.core.config import AgentSettings, RagSettings
from app.core.database import async_session_factory
from app.core.rate_limit import RedisRateLimiter, enforce_rate_limit, get_rate_limiter
from app.learning.application import LearningService
from app.learning.run_store import SqlAgentRunStore
from app.learning.store import SqlLearningStore
from app.rag.api import AnswerResponse

router = APIRouter(prefix="/api/v1", tags=["learning"])


def get_learning_service() -> LearningService:
    settings = AgentSettings()
    return LearningService(
        SqlLearningStore(
            async_session_factory,
            RagSettings().profile,
            graph_enabled=settings.agent_graph_enabled,
            wait_days=settings.agent_wait_days,
        )
    )


Auth = Annotated[Principal, Depends(get_current_principal)]
Service = Annotated[LearningService, Depends(get_learning_service)]
RequestKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]
Limiter = Annotated[RedisRateLimiter | None, Depends(get_rate_limiter)]


class ScopeRequest(BaseModel):
    document_ids: list[UUID] = Field(default_factory=list, max_length=5)
    collection_ids: list[UUID] = Field(default_factory=list, max_length=5)


class CollectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)


class CollectionResponse(CollectionRequest):
    id: UUID
    document_ids: list[UUID]


class CollectionDocumentsRequest(BaseModel):
    document_ids: list[UUID] = Field(max_length=100)


class ScopeDocument(BaseModel):
    document_id: UUID
    version_id: int
    filename: str


class ConversationRequest(ScopeRequest):
    title: str = Field(default="新对话", min_length=1, max_length=160)


class ConversationResponse(BaseModel):
    id: UUID
    title: str
    scope: list[ScopeDocument]
    group_id: UUID | None = None
    updated_at: datetime | None = None
    created_at: datetime


class TaskResultResponse(BaseModel):
    kind: Literal["quiz", "review", "clarification"]
    text: str
    quiz_id: UUID | None = None
    attempt_id: UUID | None = None
    title: str | None = None


class MessageResponse(BaseModel):
    id: UUID
    question: str
    status: Literal["queued", "processing", "answered", "insufficient", "failed", "cancelled"]
    scope: list[ScopeDocument]
    answer: AnswerResponse | None
    task_result: TaskResultResponse | None = None
    failure_code: str | None
    failure_message: str | None
    feedback: Literal["helpful", "unhelpful", "citation_inaccurate"] | None
    created_at: datetime
    run_id: UUID | None = None


class ConversationDetail(ConversationResponse):
    messages: list[MessageResponse]


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


class FeedbackRequest(BaseModel):
    rating: Literal["helpful", "unhelpful", "citation_inaccurate"]


class FeedbackResponse(FeedbackRequest):
    pass


class QuizConfigRequest(BaseModel):
    type_counts: dict[str, int]
    difficulty: Literal["easy", "medium", "hard"]
    language: Literal["zh", "en", "zh-en"]
    topic: str = Field(default="", max_length=120)
    generation_mode: Literal["standard", "agent"] = "standard"


class QuizRequest(ScopeRequest):
    title: str = Field(min_length=1, max_length=160)
    config: QuizConfigRequest


class QuizResponse(BaseModel):
    id: UUID
    title: str
    status: Literal["queued", "processing", "ready", "failed"]
    config: dict[str, Any]
    scope: list[ScopeDocument]
    question_count: int
    failure_code: str | None
    failure_message: str | None
    created_at: datetime


class QuizQuestionResponse(BaseModel):
    id: UUID
    ordinal: int
    kind: Literal["single", "multiple", "true_false", "short"]
    difficulty: Literal["easy", "medium", "hard"]
    topic: str
    stem: str
    options: list[str]
    sources: list[dict[str, Any]]
    answer: Any | None
    explanation: str | None


class QuizDetail(QuizResponse):
    questions: list[QuizQuestionResponse]


class AttemptCreated(BaseModel):
    id: UUID
    status: Literal["in_progress", "grading", "submitted", "failed"]


class AttemptSummary(AttemptCreated):
    score: float | None = None
    weak_topics: list[str] | None = None


class AttemptQuestion(QuizQuestionResponse):
    response: Any | None
    earned: float | None
    feedback: str | None
    grading_method: str | None


class AttemptDetail(AttemptSummary):
    revision: int = 0
    failure_code: str | None
    questions: list[AttemptQuestion]


class AnswerRequest(BaseModel):
    response: Any
    expected_revision: int | None = Field(default=None, ge=0)


class RunResponse(BaseModel):
    id: UUID
    status: Literal[
        "queued",
        "running",
        "waiting_input",
        "waiting_result",
        "blocked",
        "completed",
        "failed",
        "cancelled",
        "expired",
    ]
    stage: str
    revision: int
    scope: list[ScopeDocument]
    graph_version: str
    plan: dict[str, Any] | None
    outputs: list[dict[str, Any]]
    clarification: str | None
    failure_code: str | None
    failure_message: str | None
    expires_at: datetime


class RunListResponse(BaseModel):
    items: list[RunResponse]
    next_cursor: int | None


class RunInput(BaseModel):
    answer: str = Field(min_length=2, max_length=1200)
    expected_revision: int = Field(ge=0)


@router.get("/conversations/{conversation_id}/agent-runs", response_model=RunListResponse)
async def list_runs(
    conversation_id: UUID,
    principal: Auth,
    service: Service,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
) -> RunListResponse:
    return RunListResponse.model_validate(
        await SqlAgentRunStore(service.store).list_runs(
            principal.workspace_public_id, conversation_id, cursor, limit
        )
    )


@router.get("/agent-runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: UUID, principal: Auth, service: Service) -> RunResponse:
    return RunResponse.model_validate(
        await SqlAgentRunStore(service.store).get(principal.workspace_public_id, run_id)
    )


@router.post("/agent-runs/{run_id}:cancel", response_model=RunResponse)
async def cancel_run(run_id: UUID, principal: Auth, service: Service) -> RunResponse:
    return RunResponse.model_validate(
        await SqlAgentRunStore(service.store).cancel(principal.workspace_public_id, run_id)
    )


@router.post("/agent-runs/{run_id}:respond", response_model=RunResponse, status_code=202)
async def respond_run(
    run_id: UUID,
    body: RunInput,
    key: RequestKey,
    principal: Auth,
    service: Service,
    limiter: Limiter,
) -> RunResponse:
    await enforce_rate_limit(
        limiter, action="ask", subject=str(principal.workspace_public_id), idempotency_key=key
    )
    return RunResponse.model_validate(
        await SqlAgentRunStore(service.store).respond(
            principal.workspace_public_id, run_id, key, body.expected_revision, body.answer.strip()
        )
    )


class ResumeConversationResponse(BaseModel):
    id: UUID
    title: str
    working: bool


class ResumeAttemptResponse(BaseModel):
    id: UUID
    quiz_id: UUID
    title: str
    status: Literal["in_progress", "grading"]


class LearningResumeResponse(BaseModel):
    conversation: ResumeConversationResponse | None
    attempt: ResumeAttemptResponse | None


@router.get("/learning/resume", response_model=LearningResumeResponse)
async def resume_learning(principal: Auth, service: Service) -> LearningResumeResponse:
    return LearningResumeResponse.model_validate(
        await service.store.resume_learning(principal.workspace_public_id)
    )


@router.get("/collections", response_model=list[CollectionResponse])
async def list_collections(principal: Auth, service: Service) -> list[CollectionResponse]:
    return [
        CollectionResponse.model_validate(item)
        for item in await service.store.list_collections(principal.workspace_public_id)
    ]


@router.post("/collections", response_model=CollectionResponse, status_code=201)
async def create_collection(
    body: CollectionRequest, principal: Auth, service: Service
) -> CollectionResponse:
    return CollectionResponse.model_validate(
        await service.create_collection(
            principal.workspace_public_id, body.name, body.description, body.document_ids
        )
    )


@router.put("/collections/{collection_id}/documents", response_model=CollectionResponse)
async def set_collection_documents(
    collection_id: UUID, body: CollectionDocumentsRequest, principal: Auth, service: Service
) -> CollectionResponse:
    return CollectionResponse.model_validate(
        await service.store.set_collection_documents(
            principal.workspace_public_id, collection_id, body.document_ids
        )
    )


@router.delete("/collections/{collection_id}", status_code=204)
async def delete_collection(collection_id: UUID, principal: Auth, service: Service) -> Response:
    await service.store.delete_collection(principal.workspace_public_id, collection_id)
    return Response(status_code=204)


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    principal: Auth, service: Service, before: UUID | None = None
) -> list[ConversationResponse]:
    return [
        ConversationResponse.model_validate(item)
        for item in await service.store.list_conversations(principal.workspace_public_id, before)
    ]


@router.post("/conversations", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    body: ConversationRequest,
    principal: Auth,
    service: Service,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=200)
    ] = None,
) -> ConversationResponse:
    return ConversationResponse.model_validate(
        await service.create_conversation(
            principal.workspace_public_id,
            body.title,
            body.document_ids,
            body.collection_ids,
            idempotency_key,
        )
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID, principal: Auth, service: Service
) -> ConversationDetail:
    return ConversationDetail.model_validate(
        await service.store.get_conversation(principal.workspace_public_id, conversation_id)
    )


@router.put("/conversations/{conversation_id}/scope", response_model=ConversationResponse)
async def set_conversation_scope(
    conversation_id: UUID, body: ScopeRequest, principal: Auth, service: Service
) -> ConversationResponse:
    return ConversationResponse.model_validate(
        await service.store.set_conversation_scope(
            principal.workspace_public_id, conversation_id, body.document_ids, body.collection_ids
        )
    )


@router.post(
    "/conversations/{conversation_id}/messages", response_model=MessageResponse, status_code=202
)
async def ask(
    conversation_id: UUID,
    body: AskRequest,
    principal: Auth,
    service: Service,
    idempotency_key: RequestKey,
    limiter: Limiter,
) -> MessageResponse:
    await enforce_rate_limit(
        limiter,
        action="ask",
        subject=str(principal.workspace_public_id),
        idempotency_key=idempotency_key,
    )
    return MessageResponse.model_validate(
        await service.ask(
            principal.workspace_public_id, conversation_id, idempotency_key, body.question
        )
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}:cancel", response_model=MessageResponse
)
async def cancel_message(
    conversation_id: UUID, message_id: UUID, principal: Auth, service: Service
) -> MessageResponse:
    return MessageResponse.model_validate(
        await service.store.cancel_message(
            principal.workspace_public_id, conversation_id, message_id
        )
    )


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=FeedbackResponse,
)
async def feedback(
    conversation_id: UUID,
    message_id: UUID,
    body: FeedbackRequest,
    principal: Auth,
    service: Service,
    idempotency_key: RequestKey,
    limiter: Limiter,
) -> FeedbackResponse:
    await enforce_rate_limit(
        limiter,
        action="feedback",
        subject=str(principal.workspace_public_id),
        idempotency_key=idempotency_key,
    )
    return FeedbackResponse.model_validate(
        await service.store.feedback(
            principal.workspace_public_id, conversation_id, message_id, idempotency_key, body.rating
        )
    )


@router.get("/quizzes", response_model=list[QuizResponse])
async def list_quizzes(principal: Auth, service: Service) -> list[QuizResponse]:
    return [
        QuizResponse.model_validate(item)
        for item in await service.store.list_quizzes(principal.workspace_public_id)
    ]


@router.post("/quizzes", response_model=QuizResponse, status_code=202)
async def create_quiz(
    body: QuizRequest,
    principal: Auth,
    service: Service,
    idempotency_key: RequestKey,
    limiter: Limiter,
) -> QuizResponse:
    await enforce_rate_limit(
        limiter,
        action="quiz",
        subject=str(principal.workspace_public_id),
        idempotency_key=idempotency_key,
    )
    return QuizResponse.model_validate(
        await service.create_quiz(
            principal.workspace_public_id,
            idempotency_key,
            body.title,
            body.config.model_dump(),
            body.document_ids,
            body.collection_ids,
        )
    )


@router.get("/quizzes/{quiz_id}", response_model=QuizDetail)
async def get_quiz(quiz_id: UUID, principal: Auth, service: Service) -> QuizDetail:
    return QuizDetail.model_validate(
        await service.store.get_quiz(principal.workspace_public_id, quiz_id)
    )


@router.post("/quizzes/{quiz_id}/attempts", response_model=AttemptCreated, status_code=201)
async def start_attempt(
    quiz_id: UUID, principal: Auth, service: Service, idempotency_key: RequestKey
) -> AttemptCreated:
    return AttemptCreated.model_validate(
        await service.store.start_attempt(principal.workspace_public_id, quiz_id, idempotency_key)
    )


@router.get("/quizzes/{quiz_id}/attempts", response_model=list[AttemptSummary])
async def list_attempts(quiz_id: UUID, principal: Auth, service: Service) -> list[AttemptSummary]:
    return [
        AttemptSummary.model_validate(item)
        for item in await service.store.list_attempts(principal.workspace_public_id, quiz_id)
    ]


@router.get("/quizzes/{quiz_id}/attempts/{attempt_id}", response_model=AttemptDetail)
async def get_attempt(
    quiz_id: UUID, attempt_id: UUID, principal: Auth, service: Service
) -> AttemptDetail:
    return AttemptDetail.model_validate(
        await service.store.get_attempt(principal.workspace_public_id, quiz_id, attempt_id)
    )


@router.put("/quizzes/{quiz_id}/attempts/{attempt_id}/answers/{question_id}")
async def save_answer(
    quiz_id: UUID,
    attempt_id: UUID,
    question_id: UUID,
    body: AnswerRequest,
    principal: Auth,
    service: Service,
) -> dict[str, Any]:
    return await service.store.save_answer(
        principal.workspace_public_id,
        quiz_id,
        attempt_id,
        question_id,
        body.response,
        body.expected_revision,
    )


@router.post("/quizzes/{quiz_id}/attempts/{attempt_id}:submit", response_model=AttemptSummary)
async def submit_attempt(
    quiz_id: UUID, attempt_id: UUID, principal: Auth, service: Service
) -> AttemptSummary:
    return AttemptSummary.model_validate(
        await service.store.submit_attempt(principal.workspace_public_id, quiz_id, attempt_id)
    )


@router.post(
    "/quizzes/{quiz_id}/attempts/{attempt_id}:retry-grading", response_model=AttemptSummary
)
async def retry_grading(
    quiz_id: UUID, attempt_id: UUID, principal: Auth, service: Service, limiter: Limiter
) -> AttemptSummary:
    await enforce_rate_limit(limiter, action="quiz", subject=str(principal.workspace_public_id))
    return AttemptSummary.model_validate(
        await service.store.retry_grading(principal.workspace_public_id, quiz_id, attempt_id)
    )
