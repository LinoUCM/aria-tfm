import uuid
from datetime import datetime
from core.config import settings

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from models.database import Incident, IncidentStatus, IncidentSource
from models.schemas import DatadogWebhookPayload, IncidentResponse
from services.sse_manager import sse_manager
from data.datadog_presets import get_preset_with_timestamp, list_presets
import structlog
import os
import httpx

logger = structlog.get_logger()
router = APIRouter(tags=["Webhooks & Simulator"])

N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")

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
    
# ─── Real Datadog Webhook ────────────────────────────────────────────────────

@router.post("/webhook/datadog", status_code=200)
async def datadog_webhook(
    payload: DatadogWebhookPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Receives Datadog alert webhooks.
    In production: configure this URL in Datadog → Integrations → Webhooks.
    In development: use the simulator endpoint below.
    """
    incident = await _create_incident_from_payload(payload, db)
    background_tasks.add_task(_analyze_incident_async, str(incident.id), payload.dict())
    background_tasks.add_task(_send_n8n_notification, str(incident.id), payload.dict())

    logger.info("datadog_webhook_received", incident_id=str(incident.id), title=payload.title)
    return {"status": "received", "incident_id": str(incident.id)}


# ─── Simulator Endpoints ─────────────────────────────────────────────────────

@router.get("/simulator/presets")
async def get_presets():
    """List all available Datadog alert presets for the simulator."""
    return list_presets()


@router.post("/simulator/fire/{preset_key}")
async def fire_preset(
    preset_key: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Fire a simulated Datadog alert using a preset.
    Replicates the exact payload Datadog would send in production.
    """
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
    """Fire a custom alert with a user-defined payload."""
    incident = await _create_incident_from_payload(payload, db)
    background_tasks.add_task(_analyze_incident_async, str(incident.id), payload.dict())
    background_tasks.add_task(_send_n8n_notification, str(incident.id), payload.dict())

    return {
        "status": "fired",
        "incident_id": str(incident.id),
        "stream_url": f"/incidents/stream/{incident.id}",
    }


# ─── Incidents Feed (SSE) ────────────────────────────────────────────────────

@router.get("/incidents/stream/feed")
async def incidents_feed():
    """SSE stream for real-time incident updates on the dashboard."""
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        sse_manager.stream("incidents_feed"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/incidents/stream/{incident_id}")
async def incident_analysis_stream(incident_id: str):
    """SSE stream for real-time ARIA analysis of a specific incident."""
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        sse_manager.stream(f"incident_{incident_id}"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


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
        created_at=datetime.utcnow(),
    )
    db.add(incident)
    await db.commit()

    # Broadcast to incidents feed
    await sse_manager.incident_update("incidents_feed", {
        "id": str(incident.id),
        "title": incident.title,
        "severity": incident.severity,
        "status": incident.status,
        "service_affected": incident.service_affected,
        "created_at": incident.created_at.isoformat(),
    })

    return incident


async def _analyze_incident_async(incident_id: str, payload_dict: dict):
    """Background task: runs ARIA analysis and streams results."""
    try:
        from agents.orchestrator import orchestrator
        channel_id = f"incident_{incident_id}"

        await orchestrator.analyze_incident(
            channel_id=channel_id,
            incident_id=incident_id,
            payload=payload_dict,
        )
    except Exception as e:
        logger.error("incident_analysis_error", incident_id=incident_id, error=str(e))
        await sse_manager.error(f"incident_{incident_id}", str(e))
