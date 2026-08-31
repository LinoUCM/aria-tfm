import uuid
import re
from datetime import datetime, timezone

import httpx
import structlog

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db, AsyncSessionLocal
from models.database import Incident, IncidentStatus, IncidentSource, AuditLog, Document, DocumentSource
from models.schemas import DatadogWebhookPayload
from services.sse_manager import sse_manager
from data.datadog_presets import get_preset_with_timestamp, list_presets
from services.remediation import remediation_service

logger = structlog.get_logger()
router = APIRouter(tags=["Webhooks & Simulator"])


# ─── Helper PDF Sanitizer & Renderer ─────────────────────────────────────────

def sanitize_md_for_pdf(text: str) -> str:
    """Sustituye emojis y caracteres Unicode no soportados por la fuente Helvetica."""
    replacements = {
        "🚨": "[ALERT]",
        "✅": "[OK]",
        "⏱️": "[TIME]",
        "📄": "[DOC]",
        "📌": "[INFO]",
        "🔍": "[RCA]",
        "⏳": "[WAIT]",
        "⚡": "[FLASH]",
        "•": "-",
        "–": "-",
        "—": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    
    return text.encode('latin-1', 'replace').decode('latin-1')


# ─── Helper de Notificación a n8n ────────────────────────────────────────────

async def _send_n8n_notification(incident_id: str, payload_dict: dict):
    if not settings.N8N_WEBHOOK_URL:
        logger.warning("n8n_webhook_url_missing")
        return

    n8n_payload = {
        "incident_id": incident_id,
        "severity": payload_dict.get("priority", "P3"),
        "message_text": f"🚨 *Alerta ARIA: {payload_dict.get('title', 'Sin título')}*\n\n*ID Incidente:* `{incident_id}`\n*Estado:* Generado automáticamente por la plataforma.",
        "dashboard_url": f"http://localhost:3000/incidents/{incident_id}",
        "raw_payload": payload_dict,
    }

    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(settings.N8N_WEBHOOK_URL, json=n8n_payload, timeout=5.0)
            logger.info("n8n_webhook_sent", status_code=res.status_code)
        except Exception as e:
            logger.error("n8n_webhook_failed", error=str(e))


# ─── Incidents REST API ──────────────────────────────────────────────────────

@router.get("/incidents")
async def get_incidents(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Obtiene la lista de incidentes almacenados en PostgreSQL."""
    result = await db.execute(
        select(Incident).order_by(Incident.created_at.desc()).limit(limit)
    )
    incidents = result.scalars().all()

    return [
        {
            "id": str(inc.id),
            "title": inc.title,
            "description": inc.description,
            "severity": inc.severity.value if hasattr(inc.severity, "value") else str(inc.severity),
            "status": inc.status.value if hasattr(inc.status, "value") else str(inc.status),
            "service_affected": inc.service_affected,
            "host": inc.host,
            "tags": inc.tags or [],
            "metrics": inc.metrics or {},
            "analysis": getattr(inc, "analysis", None),
            "created_at": f"{inc.created_at.isoformat()}Z" if inc.created_at else None,
        }
        for inc in incidents
    ]


# ─── Real Datadog Webhook ────────────────────────────────────────────────────

@router.post("/webhook/datadog", status_code=200)
async def datadog_webhook(
    payload: DatadogWebhookPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    incident = await _create_incident_from_payload(payload, db)
    payload_dict = payload.model_dump()
    background_tasks.add_task(_analyze_incident_async, str(incident.id), payload_dict)
    background_tasks.add_task(_send_n8n_notification, str(incident.id), payload_dict)

    logger.info("datadog_webhook_received", incident_id=str(incident.id), title=payload.title)
    return {"status": "received", "incident_id": str(incident.id)}


# ─── Simulator Endpoints ─────────────────────────────────────────────────────

@router.get("/simulator/presets")
async def get_presets():
    return list_presets()


@router.post("/simulator/fire/{preset_key}")
async def fire_preset(
    preset_key: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    try:
        payload_dict = get_preset_with_timestamp(preset_key)
    except ValueError as e:
        raise HTTPException(404, str(e))

    payload = DatadogWebhookPayload(**payload_dict)
    incident = await _create_incident_from_payload(payload, db)
    background_tasks.add_task(_analyze_incident_async, str(incident.id), payload_dict)
    background_tasks.add_task(_send_n8n_notification, str(incident.id), payload_dict)

    logger.info("simulator_alert_fired", preset=preset_key, incident_id=str(incident.id))

    return {
        "status": "fired",
        "incident_id": str(incident.id),
        "message": f"Alert '{payload.title}' fired successfully",
        "stream_url": f"/incidents/stream/{incident.id}",
    }


@router.post("/simulator/custom")
async def fire_custom(
    payload: DatadogWebhookPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    incident = await _create_incident_from_payload(payload, db)
    payload_dict = payload.model_dump()
    background_tasks.add_task(_analyze_incident_async, str(incident.id), payload_dict)
    background_tasks.add_task(_send_n8n_notification, str(incident.id), payload_dict)

    return {
        "status": "fired",
        "incident_id": str(incident.id),
        "stream_url": f"/incidents/stream/{incident.id}",
    }


# ─── Incidents Feed (SSE) ────────────────────────────────────────────────────

@router.get("/incidents/stream/feed")
async def incidents_feed():
    return StreamingResponse(
        sse_manager.stream("incidents_feed"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/incidents/stream/{incident_id}")
async def incident_analysis_stream(incident_id: str):
    return StreamingResponse(
        sse_manager.stream(f"incident_{incident_id}"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


class RemediationRequest(BaseModel):
    action_id: str
    parameters: dict = {}


@router.post("/incidents/{incident_id}/remediate")
async def execute_incident_remediation(
    incident_id: str,
    payload: RemediationRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        inc_uuid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de incidente inválido")

    result = await db.execute(select(Incident).where(Incident.id == inc_uuid))
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")

    # 1. Ejecución técnica de la acción
    success, message = await remediation_service.execute_action(
        payload.action_id, payload.parameters
    )

    target_name = payload.parameters.get("container_name") or payload.parameters.get("database_name") or "system"
    audit_entry = AuditLog(
        incident_id=inc_uuid,
        action_id=payload.action_id,
        target=target_name,
        executed_by="admin@aria.internal",
        status="SUCCESS" if success else "FAILED"
    )
    db.add(audit_entry)

    if not success:
        await db.commit()
        raise HTTPException(status_code=400, detail=message)

    # 2. Sintetizar e Indexar automáticamente en la Knowledge Base (RAG)
    service_str = incident.service_affected or "general"
    filename = f"RUNBOOK-AUTO-{service_str.upper()}-{str(incident.id)[:8]}.md"
    auto_notes = f"Remediación automática 1-Click ejecutada con éxito: {payload.action_id} en {target_name}. {message}"

    chunks_count = 0
    try:
        from agents.orchestrator import orchestrator
        runbook_md = await orchestrator.synthesize_runbook(
            incident_title=incident.title,
            incident_id=str(incident.id),
            service=service_str,
            host=incident.host or "N/A",
            description=incident.description or "Sin descripción",
            analysis=getattr(incident, "analysis", "N/A"),
            engineer="ARIA_AUTOMATION",
            notes=auto_notes,
        )

        from services.indexing_service import get_indexing_service
        indexer = get_indexing_service()
        doc_id = f"kb_inc_{str(incident.id)}"

        chunks_count = await indexer.index_text(
            doc_id=doc_id,
            text=runbook_md,
            filename=filename,
            category="runbooks",
            service_tag=service_str,
            source="feedback_loop"
        )
        logger.info("kb_runbook_indexed_via_remediation", filename=filename, chunks=chunks_count)
    except Exception as e:
        logger.error("kb_indexing_failed_via_remediation", error=str(e))

    # 3. Guardar el documento en PostgreSQL para que se vea reflejado en el UI de Knowledge Base
    try:
        auto_doc = Document(
            id=uuid.uuid4(),
            filename=filename,
            title=f"Auto Runbook: {incident.title}",
            category="runbook",
            status="indexed",
            chunks_count=chunks_count or 1,
            source=DocumentSource.AUTO_GENERATED
        )
        db.add(auto_doc)
    except Exception as doc_err:
        logger.error("failed_to_create_document_record_remediation", error=str(doc_err))
        db.expunge_all()

    # 4. Actualizar estado del incidente y enviar evento SSE
    incident = await db.merge(incident)
    incident.status = IncidentStatus.RESOLVED
    await db.commit()

    sev_val = incident.severity.value if hasattr(incident.severity, "value") else str(incident.severity)
    await sse_manager.incident_update("incidents_feed", {
        "id": str(incident.id),
        "title": incident.title,
        "description": incident.description,
        "severity": sev_val.upper(),
        "status": "RESOLVED",
        "service_affected": incident.service_affected,
        "host": incident.host,
        "tags": incident.tags or [],
        "metrics": incident.metrics or {},
        "analysis": getattr(incident, "analysis", None),
        "created_at": f"{incident.created_at.isoformat()}Z" if incident.created_at else None,
    })

    return {
        "status": "success",
        "message": message,
        "incident_status": "RESOLVED",
        "generated_runbook": filename
    }


class StatusUpdate(BaseModel):
    status: str


@router.patch("/incidents/{incident_id}/status")
async def update_incident_status(
    incident_id: str,
    body: StatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        inc_uuid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de incidente inválido")

    result = await db.execute(select(Incident).where(Incident.id == inc_uuid))
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")

    incident.status = body.status.upper()
    await db.commit()

    sev_val = incident.severity.value if hasattr(incident.severity, "value") else str(incident.severity)
    await sse_manager.incident_update("incidents_feed", {
        "id": str(incident.id),
        "title": incident.title,
        "description": incident.description,
        "severity": sev_val.upper(),
        "status": incident.status,
        "service_affected": incident.service_affected,
        "host": incident.host,
        "tags": incident.tags or [],
        "metrics": incident.metrics or {},
        "analysis": getattr(incident, "analysis", None),
        "created_at": f"{incident.created_at.isoformat()}Z" if incident.created_at else None,
    })

    return {"status": "success", "new_status": incident.status}


@router.get("/audit-logs")
async def get_audit_logs(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
    )
    logs = result.scalars().all()
    
    return [
        {
            "id": str(log.id),
            "incident_id": str(log.incident_id),
            "action_id": log.action_id,
            "target": log.target,
            "executed_by": log.executed_by,
            "status": log.status,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        }
        for log in logs
    ]


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _create_incident_from_payload(
    payload: DatadogWebhookPayload,
    db: AsyncSession,
) -> Incident:
    severity_map = {"P1": "P1", "P2": "P2", "P3": "P3", "P4": "P4"}

    incident = Incident(
        id=uuid.uuid4(),
        title=payload.title,
        description=payload.text,
        source=IncidentSource.DATADOG_WEBHOOK,
        severity=severity_map.get(payload.priority, "P3"),
        status=IncidentStatus.OPEN,
        service_affected=payload.extract_service(),
        host=payload.host,
        tags=payload.tags or [],
        metrics=payload.metrics or {},
        datadog_alert_id=payload.id,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(incident)
    await db.commit()

    sev_val = incident.severity.value if hasattr(incident.severity, "value") else str(incident.severity)
    stat_val = incident.status.value if hasattr(incident.status, "value") else str(incident.status)

    await sse_manager.incident_update("incidents_feed", {
        "id": str(incident.id),
        "title": incident.title,
        "description": incident.description,
        "severity": sev_val.upper(),
        "status": stat_val.upper(),
        "service_affected": incident.service_affected,
        "host": incident.host,
        "tags": incident.tags or [],
        "metrics": incident.metrics or {},
        "created_at": f"{incident.created_at.isoformat()}Z" if incident.created_at else None,
    })

    return incident


async def _analyze_incident_async(incident_id: str, payload_dict: dict):
    try:
        from agents.orchestrator import orchestrator
        channel_id = f"incident_{incident_id}"

        analysis_result = await orchestrator.analyze_incident(
            channel_id=channel_id,
            incident_id=incident_id,
            payload=payload_dict,
        )

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Incident).where(Incident.id == uuid.UUID(incident_id)))
            inc = result.scalar_one_or_none()
            if inc:
                inc.analysis = str(analysis_result)
                await db.commit()

    except Exception as e:
        logger.error("incident_analysis_error", incident_id=incident_id, error=str(e))
        await sse_manager.error(f"incident_{incident_id}", str(e))


class ResolveFeedbackRequest(BaseModel):
    resolution_notes: str
    executed_by: str = "admin@aria.internal"


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident_with_feedback(
    incident_id: str,
    payload: ResolveFeedbackRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        inc_uuid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de incidente inválido")

    result = await db.execute(select(Incident).where(Incident.id == inc_uuid))
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")

    service_str = incident.service_affected or "general"
    filename = f"RUNBOOK-AUTO-{service_str.upper()}-{str(incident.id)[:8]}.md"

    from agents.orchestrator import orchestrator
    runbook_md = await orchestrator.synthesize_runbook(
        incident_title=incident.title,
        incident_id=str(incident.id),
        service=service_str,
        host=incident.host or "N/A",
        description=incident.description or "Sin descripción",
        analysis=getattr(incident, "analysis", "N/A"),
        engineer=payload.executed_by,
        notes=payload.resolution_notes,
    )

    chunks_count = 0
    try:
        from services.indexing_service import get_indexing_service
        indexer = get_indexing_service()
        doc_id = f"kb_inc_{str(incident.id)}"

        chunks_count = await indexer.index_text(
            doc_id=doc_id,
            text=runbook_md,
            filename=filename,
            category="runbooks",
            service_tag=service_str,
            source="feedback_loop"
        )
        logger.info("kb_runbook_indexed", filename=filename, chunks=chunks_count)
    except Exception as e:
        logger.error("kb_indexing_failed", error=str(e))

    try:
        auto_doc = Document(
            id=uuid.uuid4(),
            filename=filename,
            title=f"Auto Runbook: {incident.title}",
            category="runbook",
            status="indexed",
            chunks_count=chunks_count or 1,
            source=DocumentSource.AUTO_GENERATED
        )
        db.add(auto_doc)
    except Exception as doc_err:
        logger.error("failed_to_create_document_record", error=str(doc_err))
        db.expunge_all()

    incident = await db.merge(incident)
    incident.status = IncidentStatus.RESOLVED
    await db.commit()

    sev_val = incident.severity.value if hasattr(incident.severity, "value") else str(incident.severity)
    await sse_manager.incident_update("incidents_feed", {
        "id": str(incident.id),
        "title": incident.title,
        "description": incident.description,
        "severity": sev_val.upper(),
        "status": "RESOLVED",
        "service_affected": incident.service_affected,
        "host": incident.host,
        "tags": incident.tags or [],
        "metrics": incident.metrics or {},
        "analysis": getattr(incident, "analysis", None),
        "created_at": f"{incident.created_at.isoformat()}Z" if incident.created_at else None,
    })

    return {
        "status": "success",
        "message": f"Incidente resuelto, sintetizado por LLM e indexado en {chunks_count} chunks.",
        "generated_runbook": filename
    }


# ─── Generación e Indexación de Post-Mortem ─────────────────────────────────

@router.post("/incidents/{incident_id}/postmortem")
async def generate_and_index_postmortem(
    incident_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Sintetiza un Post-Mortem usando Gemini, lo indexa en ChromaDB (RAG) 
    y registra el documento resultante en PostgreSQL.
    """
    try:
        inc_uuid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de incidente inválido")

    result = await db.execute(select(Incident).where(Incident.id == inc_uuid))
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")

    # 1. Recopilar eventos de auditoría para la línea de tiempo
    audit_result = await db.execute(
        select(AuditLog)
        .where(AuditLog.incident_id == inc_uuid)
        .order_by(AuditLog.timestamp.asc())
    )
    audit_logs_db = audit_result.scalars().all()

    timeline_events = [
        {
            "timestamp": incident.created_at.isoformat() if incident.created_at else "N/A",
            "event": "🚨 Alerta disparada y registrada en el sistema (Datadog Webhook)",
            "stage": "DETECTION"
        }
    ]

    for log in audit_logs_db:
        timeline_events.append({
            "timestamp": log.timestamp.isoformat() if log.timestamp else "N/A",
            "event": f"⚡ Acción remediadora ejecutada: '{log.action_id}' en '{log.target}' (Ejecutado por: {log.executed_by})",
            "stage": "REMEDIATION",
            "status": log.status
        })

    if incident.status == IncidentStatus.RESOLVED:
        timeline_events.append({
            "timestamp": incident.updated_at.isoformat() if incident.updated_at else "N/A",
            "event": "✅ Incidente marcado como RESOLVED e indexado en la Knowledge Base",
            "stage": "RESOLUTION"
        })

    audit_logs_summary = [
        {
            "action_id": log.action_id,
            "target": log.target,
            "status": log.status,
            "executed_by": log.executed_by,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None
        }
        for log in audit_logs_db
    ]

    incident_dict = {
        "id": str(incident.id),
        "title": incident.title,
        "severity": incident.severity.value if hasattr(incident.severity, "value") else str(incident.severity),
        "service_affected": incident.service_affected,
        "host": incident.host,
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
        "metrics": incident.metrics or {},
        "tags": incident.tags or [],
        "analysis": getattr(incident, "analysis", "No disponible")
    }

    # 2. Sintetizar Post-Mortem mediante el orquestador
    from agents.orchestrator import orchestrator
    postmortem_md = await orchestrator.synthesize_postmortem(
        incident=incident_dict,
        timeline_events=timeline_events,
        audit_logs=audit_logs_summary
    )

    service_str = incident.service_affected or "general"
    filename = f"POSTMORTEM-{service_str.upper()}-{str(incident.id)[:8]}.md"

    # 3. Indexar en ChromaDB
    chunks_count = 0
    try:
        from services.indexing_service import get_indexing_service
        indexer = get_indexing_service()
        doc_id = f"kb_pm_{str(incident.id)}"

        chunks_count = await indexer.index_text(
            doc_id=doc_id,
            text=postmortem_md,
            filename=filename,
            category="postmortem",
            service_tag=service_str,
            source="auto_postmortem"
        )
        logger.info("kb_postmortem_indexed", filename=filename, chunks=chunks_count)
    except Exception as e:
        logger.error("kb_postmortem_indexing_failed", error=str(e))

    # 4. Registrar en la base de datos documental (PostgreSQL)
    try:
        auto_doc = Document(
            id=uuid.uuid4(),
            filename=filename,
            title=f"Post-Mortem: {incident.title}",
            category="postmortem",
            status="indexed",
            chunks_count=chunks_count or 1,
            source=DocumentSource.AUTO_GENERATED
        )
        db.add(auto_doc)
        await db.commit()
    except Exception as doc_err:
        logger.error("failed_to_create_document_record_postmortem", error=str(doc_err))
        db.expunge_all()

    return {
        "status": "success",
        "message": f"Post-Mortem generado e indexado en {chunks_count} chunks.",
        "filename": filename,
        "postmortem_markdown": postmortem_md
    }


# ─── Endpoint Exportación Post-Mortem ─────────────────────────────────────────

@router.get("/incidents/{incident_id}/post-mortem/export")
async def export_incident_postmortem(
    incident_id: str,
    format: str = "markdown",
    db: AsyncSession = Depends(get_db),
):
    """
    Genera y exporta el informe Post-Mortem del incidente en formato Markdown o PDF.
    """
    try:
        inc_uuid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de incidente inválido")

    result = await db.execute(select(Incident).where(Incident.id == inc_uuid))
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")

    audit_result = await db.execute(
        select(AuditLog)
        .where(AuditLog.incident_id == inc_uuid)
        .order_by(AuditLog.timestamp.asc())
    )
    audit_logs_db = audit_result.scalars().all()

    timeline_events = [
        {
            "timestamp": incident.created_at.isoformat() if incident.created_at else "N/A",
            "event": "🚨 Alerta disparada y registrada en el sistema (Datadog Webhook)",
            "stage": "DETECTION"
        }
    ]

    for log in audit_logs_db:
        timeline_events.append({
            "timestamp": log.timestamp.isoformat() if log.timestamp else "N/A",
            "event": f"⚡ Acción remediadora ejecutada: '{log.action_id}' en '{log.target}' (Ejecutado por: {log.executed_by})",
            "stage": "REMEDIATION",
            "status": log.status
        })

    if incident.status == IncidentStatus.RESOLVED:
        timeline_events.append({
            "timestamp": incident.updated_at.isoformat() if incident.updated_at else "N/A",
            "event": "✅ Incidente marcado como RESOLVED e indexado en la Knowledge Base",
            "stage": "RESOLUTION"
        })

    audit_logs_summary = [
        {
            "action_id": log.action_id,
            "target": log.target,
            "status": log.status,
            "executed_by": log.executed_by,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None
        }
        for log in audit_logs_db
    ]

    incident_dict = {
        "id": str(incident.id),
        "title": incident.title,
        "severity": incident.severity.value if hasattr(incident.severity, "value") else str(incident.severity),
        "service_affected": incident.service_affected,
        "host": incident.host,
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
        "metrics": incident.metrics or {},
        "tags": incident.tags or [],
        "analysis": getattr(incident, "analysis", "No disponible")
    }

    from agents.orchestrator import orchestrator
    postmortem_md = await orchestrator.synthesize_postmortem(
        incident=incident_dict,
        timeline_events=timeline_events,
        audit_logs=audit_logs_summary
    )

    filename_base = f"POSTMORTEM-{incident.service_affected or 'SYSTEM'}-{str(incident.id)[:8]}".upper()

    if format.lower() == "pdf":
        try:
            from fpdf import FPDF

            class PostMortemPDF(FPDF):
                def header(self):
                    self.set_font('Helvetica', 'B', 12)
                    self.cell(0, 8, 'ARIA SRE Platform - Official Post-Mortem Report', border=False, ln=True, align='C')
                    self.set_draw_color(200, 200, 200)
                    self.line(10, 18, 200, 18)
                    self.ln(5)

                def footer(self):
                    self.set_y(-15)
                    self.set_font('Helvetica', 'I', 8)
                    self.cell(0, 10, f'Página {self.page_no()}', align='C')

            pdf = PostMortemPDF()
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.add_page()

            lines = postmortem_md.split('\n')
            for line in lines:
                clean_line = sanitize_md_for_pdf(line).strip()

                pdf.set_x(pdf.l_margin)

                if re.match(r'^\|?\s*:?-+:?\s*(\|?\s*:?-+:?\s*)+$', clean_line):
                    continue

                if clean_line.startswith('# '):
                    pdf.set_font('Helvetica', 'B', 14)
                    pdf.multi_cell(0, 7, txt=clean_line.replace('# ', '').strip())
                    pdf.ln(2)

                elif clean_line.startswith('## '):
                    pdf.set_font('Helvetica', 'B', 12)
                    pdf.ln(2)
                    pdf.multi_cell(0, 6, txt=clean_line.replace('## ', '').strip())
                    pdf.ln(1)

                elif clean_line.startswith('### '):
                    pdf.set_font('Helvetica', 'B', 10)
                    pdf.multi_cell(0, 5, txt=clean_line.replace('### ', '').strip())
                    pdf.ln(1)

                elif clean_line.startswith('* ') or clean_line.startswith('- '):
                    pdf.set_font('Helvetica', '', 9)
                    item_text = clean_line[2:].strip()
                    pdf.multi_cell(0, 5, txt=f"  - {item_text}")

                elif clean_line.startswith('|'):
                    columns = [col.strip() for col in clean_line.split('|') if col.strip()]
                    if columns:
                        row_str = " | ".join(columns)
                        pdf.set_font('Helvetica', '', 8)
                        pdf.multi_cell(0, 5, txt=row_str)

                else:
                    if clean_line:
                        pdf.set_font('Helvetica', '', 9)
                        pdf.multi_cell(0, 5, txt=clean_line)
                    else:
                        pdf.ln(2)

            output_res = pdf.output(dest='S')
            pdf_bytes = output_res.encode('latin-1') if isinstance(output_res, str) else bytes(output_res)

            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f"attachment; filename={filename_base}.pdf"
                }
            )
        except Exception as pdf_err:
            logger.error("pdf_export_failed", error=str(pdf_err))
            raise HTTPException(
                status_code=500,
                detail=f"Error al generar el PDF: {str(pdf_err)}"
            )

    return Response(
        content=postmortem_md,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f"attachment; filename={filename_base}.md"
        }
    )