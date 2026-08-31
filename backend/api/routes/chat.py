import uuid
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.database import get_db
from models.database import Conversation, Incident
from models.schemas import ChatRequest
from services.sse_manager import sse_manager
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("/")
async def chat(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    """
    Main chat endpoint. Returns a channel_id for SSE streaming.
    The actual response streams via GET /chat/stream/{channel_id}
    """
    channel_id = str(uuid.uuid4())

    # Get or create conversation
    conversation_id = request.conversation_id
    if not conversation_id:
        conversation = Conversation(
            id=uuid.uuid4(),
            incident_id=request.incident_id,
            messages=[],
            agents_used=[],
            input_modalities=[],
        )
        db.add(conversation)
        await db.commit()
        conversation_id = conversation.id

    # Launch agent processing in background
    import asyncio
    asyncio.create_task(
        _process_chat(
            channel_id=channel_id,
            conversation_id=conversation_id,
            request=request,
            db=db,
        )
    )

    return {
        "channel_id": channel_id,
        "conversation_id": str(conversation_id),
    }


@router.get("/stream/{channel_id}")
async def stream_chat(channel_id: str):
    """SSE endpoint — streams agent events and tokens in real time."""
    return StreamingResponse(
        sse_manager.stream(channel_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    return conv


async def _process_chat(channel_id: str, conversation_id: UUID, request: ChatRequest, db):
    """Background task: runs the agent orchestrator and streams results."""
    try:
        from agents.orchestrator import orchestrator
        await orchestrator.run(
            channel_id=channel_id,
            conversation_id=conversation_id,
            message=request.message,
            image_base64=request.image_base64,
            audio_base64=request.audio_base64,
        )
    except Exception as e:
        logger.error("chat_processing_error", channel_id=channel_id, error=str(e))
        await sse_manager.error(channel_id, str(e))
