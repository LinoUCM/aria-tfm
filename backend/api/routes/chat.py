import uuid
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.database import get_db, AsyncSessionLocal
from core.security import get_current_user_data, TokenData
from models.database import Conversation, Incident
from models.schemas import ChatRequest
from services.sse_manager import sse_manager
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("/")
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user_data),
):
    """
    Main chat endpoint. Returns a channel_id for SSE streaming.
    The actual response streams via GET /chat/stream/{channel_id}
    """
    channel_id = str(uuid.uuid4())

    conversation_id = request.conversation_id
    if conversation_id:
        # Verificar que la conversación existe y, si tiene dueño, que es el
        # usuario actual. Las conversaciones anónimas antiguas (owner_username
        # None) siguen siendo accesibles por cualquiera, por compatibilidad.
        result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
        existing = result.scalar_one_or_none()
        if not existing or (existing.owner_username and existing.owner_username != current_user.username):
            raise HTTPException(404, "Conversación no encontrada")
    else:
        conversation = Conversation(
            id=uuid.uuid4(),
            incident_id=request.incident_id,
            owner_username=current_user.username,
            messages=[],
            agents_used=[],
            input_modalities=[],
        )
        db.add(conversation)
        await db.commit()
        conversation_id = conversation.id

    # Lanzamos el procesamiento en segundo plano. NO le pasamos la sesión db
    # de esta petición: ya estará cerrada para cuando esta tarea se ejecute
    # de verdad. _process_chat abre su propia sesión, igual que ya hace
    # _analyze_incident_async en webhooks.py.
    import asyncio
    asyncio.create_task(
        _process_chat(
            channel_id=channel_id,
            conversation_id=conversation_id,
            request=request,
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


@router.get("/conversations")
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user_data),
):
    """Lista las conversaciones del usuario logado, más recientes primero."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.owner_username == current_user.username)
        .order_by(Conversation.updated_at.desc())
        .limit(50)
    )
    conversations = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "title": c.title or "Nueva conversación",
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in conversations
    ]


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user_data),
):
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if conv.owner_username and conv.owner_username != current_user.username:
        # No revelamos que existe una conversación ajena: mismo 404 que "no existe"
        raise HTTPException(404, "Conversation not found")
    return conv


async def _process_chat(channel_id: str, conversation_id: UUID, request: ChatRequest):
    """Background task: runs the agent orchestrator, streams results, and
    persists the exchange in its own DB session."""
    final_response = ""
    try:
        from agents.orchestrator import orchestrator
        final_response = await orchestrator.run(
            channel_id=channel_id,
            conversation_id=conversation_id,
            message=request.message,
            image_base64=request.image_base64,
            audio_base64=request.audio_base64,
        )
    except Exception as e:
        logger.error("chat_processing_error", channel_id=channel_id, error=str(e))
        await sse_manager.error(channel_id, str(e))
        return

    # Persistencia en su propia sesión y su propio try/except: un fallo aquí
    # no debe reportarse como error al usuario, que ya recibió su respuesta
    # correctamente vía SSE.
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
            conversation = result.scalar_one_or_none()
            if conversation:
                # Reasignar la lista completa (no usar .append() in-place):
                # SQLAlchemy no detecta mutaciones in-place de columnas JSON.
                messages = list(conversation.messages or [])
                messages.append({
                    "role": "user",
                    "content": request.message,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                messages.append({
                    "role": "assistant",
                    "content": final_response,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                conversation.messages = messages
                conversation.updated_at = datetime.utcnow()
                if not conversation.title and request.message:
                    conversation.title = request.message[:80]
                db.add(conversation)
                await db.commit()
    except Exception as e:
        logger.error("chat_persist_error", channel_id=channel_id,
                     conversation_id=str(conversation_id), error=str(e))
