import json
import structlog
from typing import AsyncGenerator

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from app.api.chat.schemas import ChatMessageRequest, ChatMessageResponse
from app.api.deps import SessionRepoDep, ScoreRepoDep, get_conversation_handler
from app.application.conversation.commands import SendMessageCommand
from app.application.scoring.champ_extractor import extract_champ_task
from app.application.qualification.handler import HandoffHandler
from app.metrics import HANDOFF_COUNTER, INSTANT_HANDOFF_TRIGGERS

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/message/{session_id}", response_model=ChatMessageResponse)
async def send_message(
    session_id: str,
    body: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    session_repo: SessionRepoDep,
    score_repo: ScoreRepoDep,
) -> dict:
    handler = get_conversation_handler(session_repo, score_repo)
    cmd = SendMessageCommand(session_id=session_id, content=body.content)
    result = await handler.handle_message(cmd)

    if "error" in result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["error"])

    champ_scheduled = False
    handoff_triggered = False

    if result.get("should_instant_handoff"):
        # LAYER 1: Instant handoff — skip extraction entirely
        instant_reason = result.get("instant_handoff_reason", "")
        HANDOFF_COUNTER.labels(path="chat_instant").inc()
        INSTANT_HANDOFF_TRIGGERS.labels(reason=instant_reason).inc()
        handoff_handler = HandoffHandler(session_repo, score_repo)
        background_tasks.add_task(handoff_handler.handle, session_id)
        handoff_triggered = True
    elif result.get("should_extract_champ"):
        # LAYER 2 + 3: Judge extraction (force_handoff passed through)
        force_handoff = result.get("force_handoff_after_extract", False)
        background_tasks.add_task(
            extract_champ_task, session_id, session_repo, score_repo,
            force_handoff=force_handoff,
        )
        champ_scheduled = True

    return {
        "status": "ok",
        "session_id": session_id,
        "msg_count": result["msg_count"],
        "champ_scheduled": champ_scheduled,
        "handoff_triggered": handoff_triggered,
        "signal_analysis": result.get("signal_analysis"),
    }


@router.get("/stream/{session_id}")
async def stream_response(
    session_id: str,
    request: Request,
    session_repo: SessionRepoDep,
    score_repo: ScoreRepoDep,
) -> EventSourceResponse:
    handler = get_conversation_handler(session_repo, score_repo)

    async def event_generator() -> AsyncGenerator[dict, None]:
        # Stream tokens
        async for token in handler.stream_response(session_id):
            if await request.is_disconnected():
                break

            if token.startswith("[ERROR"):
                yield {"event": "error", "data": json.dumps({"message": token})}
                return
            elif token.startswith("[STREAM_ERROR"):
                yield {"event": "error", "data": json.dumps({"message": token})}
                return

            yield {"event": "token", "data": json.dumps({"token": token})}

        # Check for score updates / handoff via Redis pubsub
        yield {"event": "done", "data": json.dumps({"session_id": session_id})}

    return EventSourceResponse(event_generator())
