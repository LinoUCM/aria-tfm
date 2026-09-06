import asyncio
import json
from typing import AsyncGenerator, Dict, Set
from uuid import UUID
import structlog

logger = structlog.get_logger()


class SSEManager:
    """
    Server-Sent Events manager.
    Handles real-time streaming of agent events and tokens to connected clients.
    """

    def __init__(self):
        self._queues: Dict[str, asyncio.Queue] = {}

    def get_or_create_queue(self, channel_id: str) -> asyncio.Queue:
        if channel_id not in self._queues:
            self._queues[channel_id] = asyncio.Queue(maxsize=100)
        return self._queues[channel_id]

    async def publish(self, channel_id: str, event: str, data: dict) -> None:
        """Publish an event to a channel."""
        queue = self.get_or_create_queue(channel_id)
        payload = json.dumps({"event": event, "data": data})
        try:
            await queue.put(payload)
        except asyncio.QueueFull:
            logger.warning("sse_queue_full", channel_id=channel_id)

    async def stream(self, channel_id: str) -> AsyncGenerator[str, None]:
        """Stream events for a channel as SSE format."""
        queue = self.get_or_create_queue(channel_id)
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {payload}\n\n"

                    # Check if stream is done
                    data = json.loads(payload)
                    if data.get("event") in ["done", "error"]:
                        break

                except asyncio.TimeoutError:
                    # Send keepalive ping
                    yield f"data: {json.dumps({'event': 'ping', 'data': {}})}\n\n"

        finally:
            self._cleanup_queue(channel_id)

    def _cleanup_queue(self, channel_id: str) -> None:
        if channel_id in self._queues:
            del self._queues[channel_id]

    # ─── Convenience Methods for Agent Events ────────────────────────────────

    async def agent_start(self, channel_id: str, agent_name: str) -> None:
        await self.publish(channel_id, "agent_start", {"agent": agent_name})

    async def agent_end(self, channel_id: str, agent_name: str) -> None:
        await self.publish(channel_id, "agent_end", {"agent": agent_name})

    async def token(self, channel_id: str, token: str) -> None:
        await self.publish(channel_id, "token", {"token": token})

    async def rag_sources(self, channel_id: str, sources: list) -> None:
        await self.publish(channel_id, "rag_sources", {"sources": sources})

    async def web_results(self, channel_id: str, sources: list) -> None:
        await self.publish(channel_id, "web_results", {"sources": sources})

    async def similar_incidents(self, channel_id: str, incidents: list) -> None:
        await self.publish(channel_id, "similar_incidents", {"incidents": incidents})

    async def done(self, channel_id: str, full_response: str) -> None:
        await self.publish(channel_id, "done", {"full_response": full_response})

    async def error(self, channel_id: str, message: str) -> None:
        await self.publish(channel_id, "error", {"message": message})

    async def incident_update(self, channel_id: str, incident: dict) -> None:
        """Broadcast incident update to all clients on the incidents channel."""
        await self.publish("incidents_feed", "incident_update", incident)


# Global singleton
sse_manager = SSEManager()
