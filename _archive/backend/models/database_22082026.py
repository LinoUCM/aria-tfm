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
    host = Column(String(255))
    tags = Column(JSON, default=list)            # ["service:payment", "env:prod"]
    metrics = Column(JSON, default=dict)         # métricas del webhook
    datadog_alert_id = Column(String(100))       # ID de la alerta en Datadog
    analysis = Column(JSON)                      # análisis generado por ARIA
    resolution = Column(Text)
    resolution_steps = Column(JSON, default=list)
    resolved_by = Column(String(255))
    resolution_time_minutes = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    conversations = relationship("Conversation", back_populates="incident")


# ─── Conversations ────────────────────────────────────────────────────────────

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True)
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
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relations
    conversation = relationship("Conversation", back_populates="rag_references")
    document = relationship("Document", back_populates="rag_references")
