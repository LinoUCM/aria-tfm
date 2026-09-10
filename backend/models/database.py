import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Text, Boolean,
    DateTime, ForeignKey, JSON, Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship
import enum

from core.database import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    ON_CALL = "on_call"
    VIEWER = "viewer"


class DocumentStatus(str, enum.Enum):
    PROCESSING = "processing"
    INDEXED = "indexed"
    ERROR = "error"
    DELETED = "deleted"


class DocumentCategory(str, enum.Enum):
    RUNBOOK = "runbook"
    POSTMORTEM = "postmortem"
    ARCHITECTURE = "architecture"
    API_DOCS = "api_docs"
    PLAYBOOK = "playbook"
    CONFIGURATION = "configuration"
    OTHER = "other"


class DocumentSource(str, enum.Enum):
    FILE = "file"
    URL = "url"
    AUTO_GENERATED = "AUTO_GENERATED"


class IncidentSeverity(str, enum.Enum):
    CRITICAL = "P1"
    HIGH = "P2"
    MEDIUM = "P3"
    LOW = "P4"


class IncidentStatus(str, enum.Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"


class IncidentSource(str, enum.Enum):
    CHAT = "chat"
    VOICE = "voice"
    DATADOG_WEBHOOK = "datadog_webhook"


# ─── Users ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(SAEnum(UserRole), default=UserRole.ON_CALL, nullable=False)

    # Perfil visual y personal (para UI SaaS profesional)
    full_name = Column(String(100))
    avatar_url = Column(String(500))

    # Seguridad y control de acceso
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    failed_login_attempts = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime)

    # Auditoría
    last_login = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─── Documents ───────────────────────────────────────────────────────────────

class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String(500), nullable=False)
    title = Column(String(500))
    source = Column(SAEnum(DocumentSource), default=DocumentSource.FILE)
    source_url = Column(String(2000))          # si viene de wiki URL
    file_path = Column(String(1000))            # ruta local o R2 key
    file_type = Column(String(50))              # pdf, md, txt, docx, url
    file_hash = Column(String(64), unique=True) # MD5 para deduplicación
    file_size = Column(Integer)
    category = Column(SAEnum(DocumentCategory), default=DocumentCategory.OTHER)
    service_tag = Column(String(100))           # ej: "payment-service"
    status = Column(SAEnum(DocumentStatus), default=DocumentStatus.PROCESSING)
    chunks_count = Column(Integer, default=0)
    error_message = Column(Text)
    indexed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    rag_references = relationship("RagReference", back_populates="document")


# ─── Incidents ───────────────────────────────────────────────────────────────

class Incident(Base):
    __tablename__ = "incidents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    source = Column(SAEnum(IncidentSource), default=IncidentSource.CHAT)
    severity = Column(SAEnum(IncidentSeverity), default=IncidentSeverity.MEDIUM)
    status = Column(SAEnum(IncidentStatus), default=IncidentStatus.OPEN)
    service_affected = Column(String(255))
    suggested_action = Column(String(50), nullable=True)  # RESTART_CONTAINER | TERMINATE_IDLE_CONNECTIONS | FLUSH_REDIS_CACHE | null
    host = Column(String(255))
    tags = Column(JSON, default=list)            # ["service:payment", "env:prod"]
    metrics = Column(JSON, default=dict)         # métricas del webhook
    datadog_alert_id = Column(String(100))       # ID de la alerta en Datadog
    analysis = Column(JSON)                      # análisis generado por ARIA
    postmortem = Column(Text, nullable=True)     # informe Post-Mortem sintetizado y persistido
    resolution = Column(Text)
    resolution_steps = Column(JSON, default=list)
    resolved_by = Column(String(255))
    resolution_time_minutes = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    postmortem_filename = Column(String, nullable=True) # Guardará el nombre del archivo PDF/MD
    # Resultado real del envío de la alerta a n8n/Telegram (_send_n8n_notification).
    # "sent" (respuesta 2xx) / "failed" (excepción o no-2xx) / null si nunca se
    # intentó o N8N_WEBHOOK_URL no está configurado.
    notification_status = Column(String(10), nullable=True)
    notification_sent_at = Column(DateTime, nullable=True)

    # Relations
    conversations = relationship("Conversation", back_populates="incident")


# ─── Conversations ────────────────────────────────────────────────────────────

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True)
    owner_username = Column(String(255), nullable=True)
    title = Column(String(500))
    messages = Column(JSON, default=list)        # [{role, content, timestamp, agents_used}]
    agents_used = Column(JSON, default=list)     # agentes que intervinieron
    input_modalities = Column(JSON, default=list) # ["text", "voice", "image"]
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    incident = relationship("Incident", back_populates="conversations")
    rag_references = relationship("RagReference", back_populates="conversation")


# ─── RAG References (trazabilidad) ───────────────────────────────────────────

class RagReference(Base):
    __tablename__ = "rag_references"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"))
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"))
    chunk_content = Column(Text)
    relevance_score = Column(Float)
    chunk_index = Column(Integer)
    page_number = Column(Integer)
    # Índice que ocupará el mensaje del asistente que generó esta cita dentro
    # del array Conversation.messages (== len(messages)+1 en el momento de
    # sintetizar, ver _persist_rag_references). El frontend agrupa las citas de
    # cada burbuja por este valor al recargar el historial. Nullable: las filas
    # persistidas antes de introducir la columna quedan en NULL sin backfill.
    message_index = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relations
    conversation = relationship("Conversation", back_populates="rag_references")
    document = relationship("Document", back_populates="rag_references")


# ─── Audit Logs ───────────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False)
    action_id = Column(String(100), nullable=False)
    target = Column(String(255), nullable=False)
    executed_by = Column(String(255), default="admin@aria.internal")
    status = Column(String(50), nullable=False)
    details = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)