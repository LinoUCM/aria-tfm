from pydantic import BaseModel, HttpUrl
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
from enum import Enum


# ─── Enums ───────────────────────────────────────────────────────────────────

class DocumentCategory(str, Enum):
    RUNBOOK = "runbook"
    POSTMORTEM = "postmortem"
    ARCHITECTURE = "architecture"
    API_DOCS = "api_docs"
    PLAYBOOK = "playbook"
    CONFIGURATION = "configuration"
    OTHER = "other"


class IncidentSeverity(str, Enum):
    CRITICAL = "P1"
    HIGH = "P2"
    MEDIUM = "P3"
    LOW = "P4"


# ─── Chat ────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str                                    # "user" | "assistant"
    content: str
    timestamp: Optional[datetime] = None
    agents_used: Optional[List[str]] = None
    rag_sources: Optional[List[Dict]] = None


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[UUID] = None
    incident_id: Optional[UUID] = None
    image_base64: Optional[str] = None          # imagen adjunta
    audio_base64: Optional[str] = None          # audio a transcribir


class ChatResponse(BaseModel):
    conversation_id: UUID
    message: str
    agents_used: List[str]
    rag_sources: Optional[List[Dict]] = None
    similar_incidents: Optional[List[Dict]] = None


# ─── Documents ───────────────────────────────────────────────────────────────

class DocumentUploadResponse(BaseModel):
    id: UUID
    filename: str
    status: str
    message: str


class DocumentURLRequest(BaseModel):
    url: str
    title: Optional[str] = None
    category: DocumentCategory = DocumentCategory.OTHER
    service_tag: Optional[str] = None


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    title: Optional[str]
    source: str
    source_url: Optional[str]
    file_type: Optional[str]
    category: str
    service_tag: Optional[str]
    status: str
    chunks_count: int
    file_size: Optional[int]
    indexed_at: Optional[datetime]
    created_at: datetime
    error_message: Optional[str]

    class Config:
        from_attributes = True


class KBStatsResponse(BaseModel):
    total_documents: int
    total_chunks: int
    indexed_documents: int
    processing_documents: int
    categories: Dict[str, int]
    last_updated: Optional[datetime]


# ─── Incidents ───────────────────────────────────────────────────────────────

class IncidentResponse(BaseModel):
    id: UUID
    title: str
    description: Optional[str]
    source: str
    severity: str
    status: str
    service_affected: Optional[str]
    host: Optional[str]
    tags: List[str]
    metrics: Dict[str, Any]
    analysis: Optional[Dict]
    resolution: Optional[str]
    resolution_time_minutes: Optional[int]
    created_at: datetime
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True


class ResolveIncidentRequest(BaseModel):
    resolution: str
    resolution_steps: List[str]
    resolved_by: str


# ─── Datadog Webhook ─────────────────────────────────────────────────────────

class DatadogMetrics(BaseModel):
    error_rate: Optional[float] = None
    p99_latency_ms: Optional[float] = None
    p50_latency_ms: Optional[float] = None
    requests_per_second: Optional[float] = None
    error_count: Optional[int] = None
    active_connections: Optional[int] = None
    max_connections: Optional[int] = None
    memory_usage_percent: Optional[float] = None
    timeout_rate: Optional[float] = None


class DatadogWebhookPayload(BaseModel):
    id: Optional[str] = None
    title: str
    text: Optional[str] = None
    alert_type: Optional[str] = "error"                             # "error" | "warning" | "info"  Opcional con valor por defecto
    priority: Optional[str] = "P1"                                # "P1" | "P2" | "P3" | "P4"   Opcional con valor por defecto
    host: Optional[str] = None
    tags: Optional[List[str]] = []
    alert_transition: Optional[str] = None      # "Triggered" | "Recovered"
    date_happened: Optional[int] = None
    metrics: Optional[Dict[str, Any]] = {}
    runbook_url: Optional[str] = None
    dashboard_url: Optional[str] = None

    def extract_service(self) -> Optional[str]:
        """Extract service name from tags like 'service:payment-service'"""
        for tag in (self.tags or []):
            if tag.startswith("service:"):
                return tag.split("service:")[1]
        return None

    def extract_env(self) -> Optional[str]:
        """Extract environment from tags like 'env:production'"""
        for tag in (self.tags or []):
            if tag.startswith("env:"):
                return tag.split("env:")[1]
        return None


# ─── Voice ───────────────────────────────────────────────────────────────────

class TranscriptionResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None


class TTSRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None


# ─── SSE Events ──────────────────────────────────────────────────────────────

class SSEEvent(BaseModel):
    event: str                                   # "agent_start" | "token" | "agent_end" | "done" | "error"
    data: Dict[str, Any]
    incident_id: Optional[str] = None
