from typing import List, Optional
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import AsyncSessionLocal
from models.database import Incident, IncidentStatus
import structlog

logger = structlog.get_logger()


class MemoryService:

    async def find_similar_incidents(
        self,
        query: str,
        service: Optional[str] = None,
        limit: int = 3,
    ) -> List[dict]:
        """Find similar resolved incidents using keyword matching."""
        async with AsyncSessionLocal() as db:
            stmt = (
                select(Incident)
                .where(Incident.status == IncidentStatus.RESOLVED)
                .order_by(desc(Incident.created_at))
                .limit(20)
            )
            if service:
                stmt = stmt.where(Incident.service_affected == service)

            result = await db.execute(stmt)
            incidents = result.scalars().all()

            # Simple keyword scoring
            query_words = set(query.lower().split())
            scored = []

            for inc in incidents:
                title_words = set((inc.title or "").lower().split())
                desc_words = set((inc.description or "").lower().split())
                score = len(query_words & (title_words | desc_words))

                if score > 0:
                    scored.append({
                        "id": str(inc.id),
                        "title": inc.title,
                        "service_affected": inc.service_affected,
                        "severity": inc.severity,
                        "resolution": inc.resolution,
                        "resolution_time_minutes": inc.resolution_time_minutes,
                        "created_at": inc.created_at.isoformat() if inc.created_at else None,
                        "score": score,
                    })

            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:limit]

    async def save_resolution(
        self,
        incident_id: str,
        resolution: str,
        resolution_steps: List[str],
        resolved_by: str,
    ) -> None:
        from datetime import datetime
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Incident).where(Incident.id == incident_id)
            )
            incident = result.scalar_one_or_none()
            if incident:
                incident.status = IncidentStatus.RESOLVED
                incident.resolution = resolution
                incident.resolution_steps = resolution_steps
                incident.resolved_by = resolved_by
                incident.resolved_at = datetime.utcnow()
                if incident.created_at:
                    delta = datetime.utcnow() - incident.created_at
                    incident.resolution_time_minutes = int(delta.total_seconds() / 60)
                await db.commit()


memory_service = MemoryService()
