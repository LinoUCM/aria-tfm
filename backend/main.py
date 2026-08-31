from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import structlog

from core.config import settings
from core.database import create_tables
from api.routes import chat, documents, webhooks, auth, users

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("aria_starting", version=settings.app_version)
    await create_tables()
    # Inicializar servicios lazy
    from services.indexing_service import get_indexing_service
    global indexing_service_instance
    import services.indexing_service as idx_module
    idx_module.indexing_service = get_indexing_service()
    logger.info("services_initialized")
    yield
    logger.info("aria_shutting_down")

app = FastAPI(
    title="ARIA — Autonomous Resolution & Intelligence Agent for Operations",
    version=settings.app_version,
    description="""
    ARIA is a multimodal multi-agent AI system for Operations teams.
    
    **Features:**
    - 🤖 Multi-agent LangGraph orchestration
    - 📚 Dynamic RAG knowledge base (files + wiki URLs)
    - 🖼️ Vision analysis (Gemini Vision)
    - 🎤 Voice input (Groq Whisper) + audio output (ElevenLabs)
    - 🔔 Datadog webhook integration (+ simulator)
    - 🔍 Web search fallback (Tavily)
    - 📡 Real-time streaming via SSE
    """,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", settings.frontend_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

# Routers
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(webhooks.router)
app.include_router(auth.router)


@app.get("/health", tags=["System"])
async def health():
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
    }


@app.get("/", tags=["System"])
async def root():
    return {
        "name": "ARIA",
        "description": "Autonomous Resolution & Intelligence Agent for Operations",
        "docs": "/docs",
        "health": "/health",
    }
# Añade el router de users
app.include_router(users.router)