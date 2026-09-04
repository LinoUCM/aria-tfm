import asyncio
import uuid
import re
from datetime import datetime, timezone

import httpx
import structlog
import os
from pathlib import Path

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Response
from fastapi.responses import StreamingResponse, FileResponse, Response
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
from core.security import require_roles, get_current_user_data, TokenData

logger = structlog.get_logger()
router = APIRouter(tags=["Webhooks & Simulator"])

# Directorio local para guardar los archivos generados
POSTMORTEM_DIR = Path("storage/postmortems")
POSTMORTEM_DIR.mkdir(parents=True, exist_ok=True)

RUNBOOK_DIR = Path("storage/runbooks")
RUNBOOK_DIR.mkdir(parents=True, exist_ok=True)

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
    current_user: TokenData = Depends(get_current_user_data),
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
            "postmortem_filename": getattr(inc, "postmortem_filename", None),
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
    current_user: TokenData = Depends(require_roles(["ADMIN", "ON_CALL"])),
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
        executed_by=current_user.username,
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
            resolved_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
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
        runbook_path = RUNBOOK_DIR / filename
        with open(runbook_path, "w", encoding="utf-8") as f:
            f.write(runbook_md)

        auto_doc = Document(
            id=uuid.uuid4(),
            filename=filename,
            title=f"Auto Runbook: {incident.title}",
            category="runbook",
            status="indexed",
            chunks_count=chunks_count or 1,
            source=DocumentSource.AUTO_GENERATED,
            file_path=str(runbook_path),
            file_type="md",
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
    current_user: TokenData = Depends(require_roles(["ADMIN", "ON_CALL"])),
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
    current_user: TokenData = Depends(get_current_user_data),
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
            "details": log.details,
            "timestamp": f"{log.timestamp.isoformat()}Z" if log.timestamp else None,
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
                # Extraemos los datos calculados por el LLM
                rca_analysis_text = analysis_result.get("analysis", "")
                real_root_cause = analysis_result.get("root_cause")

                # Guardamos solo el texto de análisis en la columna analysis
                inc.analysis = rca_analysis_text
                
                # ✅ ACTUALIZAMOS LA CAUSA RAÍZ REAL EN LA BASE DE DATOS
                if real_root_cause and real_root_cause != "unknown":
                    inc.service_affected = real_root_cause

                await db.commit()

                # Notificamos al frontend por SSE para que actualice la UI dinámicamente
                sev_val = inc.severity.value if hasattr(inc.severity, "value") else str(inc.severity)
                stat_val = inc.status.value if hasattr(inc.status, "value") else str(inc.status)
                
                await sse_manager.incident_update("incidents_feed", {
                    "id": str(inc.id),
                    "title": inc.title,
                    "description": inc.description,
                    "severity": sev_val.upper(),
                    "status": stat_val.upper(),
                    "service_affected": inc.service_affected, # 👈 Ahora enviará 'aria_db'
                    "host": inc.host,
                    "tags": inc.tags or [],
                    "metrics": inc.metrics or {},
                    "analysis": inc.analysis,
                    "created_at": f"{inc.created_at.isoformat()}Z" if inc.created_at else None,
                })

    except Exception as e:
        logger.error("incident_analysis_error", incident_id=incident_id, error=str(e))
        await sse_manager.error(f"incident_{incident_id}", str(e))


class ResolveFeedbackRequest(BaseModel):
    resolution_notes: str


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident_with_feedback(
    incident_id: str,
    payload: ResolveFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(require_roles(["ADMIN", "ON_CALL"])),
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
        resolved_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        description=incident.description or "Sin descripción",
        analysis=getattr(incident, "analysis", "N/A"),
        engineer=current_user.username,
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
        runbook_path = RUNBOOK_DIR / filename
        with open(runbook_path, "w", encoding="utf-8") as f:
            f.write(runbook_md)

        auto_doc = Document(
            id=uuid.uuid4(),
            filename=filename,
            title=f"Auto Runbook: {incident.title}",
            category="runbook",
            status="indexed",
            chunks_count=chunks_count or 1,
            source=DocumentSource.AUTO_GENERATED,
            file_path=str(runbook_path),
            file_type="md",
        )
        db.add(auto_doc)
    except Exception as doc_err:
        logger.error("failed_to_create_document_record", error=str(doc_err))
        db.expunge_all()

    incident = await db.merge(incident)
    incident.status = IncidentStatus.RESOLVED

    # Registro de auditoría de la resolución manual con feedback (mismo patrón
    # de campos que el AuditLog del endpoint /remediate).
    audit_entry = AuditLog(
        incident_id=incident.id,
        action_id="MANUAL_RESOLUTION_WITH_FEEDBACK",
        target=incident.service_affected or "unknown",
        executed_by=current_user.username,
        status="SUCCESS",
        details=payload.resolution_notes,
    )
    db.add(audit_entry)

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


# ─── Endpoint Exportación Post-Mortem ─────────────────────────────────────────

_TABLE_SEPARATOR_RE = re.compile(r'^\|?\s*:?-+:?\s*(\|?\s*:?-+:?\s*)+$')


def _parse_md_table_row(raw: str) -> list[str]:
    """'| a | b | c |'  ->  ['a', 'b', 'c']"""
    return [cell.strip() for cell in raw.strip().strip('|').split('|')]


_SEPARATOR_CELL_RE = re.compile(r'^:?-+:?$')


def _is_separator_row(cells: list[str]) -> bool:
    """True si TODAS las celdas de la fila son separadores tipo '---' o ':---'."""
    return bool(cells) and all(_SEPARATOR_CELL_RE.match(c) for c in cells)


def _render_postmortem_pdf(postmortem_md: str) -> bytes:
    """Renderiza el Markdown del Post-Mortem a PDF y devuelve los bytes.

    ATENCIÓN: es código SÍNCRONO y potencialmente lento (fpdf2). Debe ejecutarse
    SIEMPRE dentro de ``asyncio.to_thread(...)`` envuelto en un ``asyncio.wait_for``
    con timeout — nunca directamente en el hilo de evento de asyncio, porque un
    contenido patológico podría bloquear el proceso entero de FastAPI.

    Las tablas Markdown se dibujan con ``pdf.table()`` (wrapping robusto por celda)
    en lugar de aplanarlas a una sola línea y pasarlas a ``multi_cell`` al ancho
    completo, que dispara un bucle de ajuste de línea degenerado en fpdf2.
    """
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
    i = 0
    total = len(lines)
    while i < total:
        clean_line = sanitize_md_for_pdf(lines[i]).strip()
        pdf.set_x(pdf.l_margin)

        # Línea separadora de tabla Markdown (|---|---|): se ignora (igual que antes)
        if _TABLE_SEPARATOR_RE.match(clean_line):
            i += 1
            continue

        # ── Bloque de tabla Markdown ──────────────────────────────────────────
        # Se acumulan las filas contiguas que empiezan por '|' y se dibujan como
        # tabla real. pdf.table() reparte el ancho de página entre columnas y hace
        # el wrapping por celda, evitando la causa raíz del cuelgue.
        if clean_line.startswith('|'):
            table_rows: list[list[str]] = []
            while i < total:
                row_line = sanitize_md_for_pdf(lines[i]).strip()
                if not row_line.startswith('|'):
                    break
                i += 1
                cells = _parse_md_table_row(row_line)
                if cells and not _is_separator_row(cells):
                    table_rows.append(cells)

            if table_rows:
                ncols = max(len(r) for r in table_rows)
                norm_rows = [r + [""] * (ncols - len(r)) for r in table_rows]
                pdf.set_font('Helvetica', '', 8)
                # Ancho disponible = pdf.w - pdf.l_margin - pdf.r_margin (== pdf.epw),
                # que es justo lo que pdf.table() usa por defecto y reparte entre columnas.
                with pdf.table(
                    first_row_as_headings=True,   # 1ª fila en negrita = cabecera
                    borders_layout="ALL",
                    text_align="LEFT",
                    line_height=5,
                    width=pdf.w - pdf.l_margin - pdf.r_margin,
                ) as table:
                    for r in norm_rows:
                        trow = table.row()
                        for cell_text in r:
                            trow.cell(cell_text)
                pdf.ln(2)
            continue

        # ── Resto de elementos Markdown (sin cambios respecto al comportamiento previo)
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
        else:
            if clean_line:
                pdf.set_font('Helvetica', '', 9)
                pdf.multi_cell(0, 5, txt=clean_line)
            else:
                pdf.ln(2)

        i += 1

    output_res = pdf.output(dest='S')
    pdf_bytes = output_res.encode('latin-1') if isinstance(output_res, str) else bytes(output_res)
    return pdf_bytes


@router.get("/incidents/{incident_id}/post-mortem/export")
async def export_incident_postmortem(
    incident_id: str,
    format: str = "markdown",
    db: AsyncSession = Depends(get_db),
):
    """Genera y exporta el informe Post-Mortem del incidente en formato Markdown o PDF."""
    try:
        inc_uuid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de incidente inválido")

    # 1. Buscar el incidente en la Base de Datos
    result = await db.execute(select(Incident).where(Incident.id == inc_uuid))
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")

    ext = "pdf" if format.lower() == "pdf" else "md"

    # -------------------------------------------------------------------------
    # COMPROBACIÓN BBDD + DISCO:
    # Si la BD ya registra el nombre del archivo y el archivo existe en disco -> Servir
    # -------------------------------------------------------------------------
    if incident.postmortem_filename:
        # Aseguramos extensión correcta por si se solicita en formato distinto
        stored_path = POSTMORTEM_DIR / incident.postmortem_filename
        if stored_path.exists():
            return FileResponse(
                path=stored_path,
                filename=incident.postmortem_filename,
                media_type="application/pdf" if incident.postmortem_filename.endswith(".pdf") else "text/markdown"
            )
    # -------------------------------------------------------------------------

    # 2. SI NO EXISTE EN BD/DISCO -> Generar desde cero con la IA
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
        updated_ts = getattr(incident, "updated_at", None)
        timestamp_str = updated_ts.isoformat() if updated_ts else datetime.now(timezone.utc).isoformat()
        timeline_events.append({
            "timestamp": timestamp_str,
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
    generated_filename = f"{filename_base}.{ext}"
    file_path = POSTMORTEM_DIR / generated_filename

    # 3. Guardar nombre en la BBDD
    incident.postmortem_filename = generated_filename
    await db.commit()
    await db.refresh(incident)

    # 4. Compilar archivo y guardar físicamente en el servidor
    if format.lower() == "pdf":
        # El renderizado de fpdf2 es síncrono y, ante contenido patológico, puede
        # tardar muchísimo. Lo aislamos en un hilo y le ponemos un timeout duro
        # para no congelar el hilo de evento de asyncio.
        try:
            pdf_bytes = await asyncio.wait_for(
                asyncio.to_thread(_render_postmortem_pdf, postmortem_md),
                timeout=20.0
            )
        except asyncio.TimeoutError:
            logger.error("pdf_render_timeout", incident_id=incident_id)
            raise HTTPException(
                status_code=500,
                detail="La generación del PDF tardó demasiado. Intenta de nuevo "
                       "o exporta en formato Markdown."
            )
        except Exception as pdf_err:
            logger.error("pdf_export_failed", error=str(pdf_err))
            raise HTTPException(status_code=500, detail=f"Error al generar el PDF: {str(pdf_err)}")

        # Guardar PDF en disco
        with open(file_path, "wb") as f:
            f.write(pdf_bytes)

        return FileResponse(
            path=file_path,
            filename=generated_filename,
            media_type="application/pdf"
        )

    # Guardar Markdown en disco
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(postmortem_md)

    return FileResponse(
        path=file_path,
        filename=generated_filename,
        media_type="text/markdown"
    )

# ─── Generación, Visualización e Indexación de Post-Mortem ────────────────────

@router.get("/incidents/{incident_id}/postmortem/view")
async def view_incident_postmortem(
    incident_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Genera y devuelve la síntesis del Post-Mortem en JSON para visualización directa en el frontend."""
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
        updated_ts = getattr(incident, "updated_at", None)
        timestamp_str = updated_ts.isoformat() if updated_ts else datetime.now(timezone.utc).isoformat()
        timeline_events.append({
            "timestamp": timestamp_str,
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

    return {
        "incident_id": str(incident.id),
        "postmortem_markdown": postmortem_md
    }