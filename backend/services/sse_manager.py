import asyncio
import json
import time
from typing import AsyncGenerator, Dict, List, Set
from uuid import UUID
import structlog

logger = structlog.get_logger()


class SSEManager:
    """
    Server-Sent Events manager — fan-out real.

    Cada canal tiene un CONJUNTO de colas, una por suscriptor (una conexión
    ``stream()`` = un ``EventSource`` del navegador). ``publish()`` copia el
    evento a la cola de CADA suscriptor, así que N clientes en el mismo canal
    reciben todos los eventos (broadcast), no se los reparten.

    Antes había una única cola compartida por canal: ``queue.get()`` es
    consumidor (un evento -> un solo cliente) y, al desconectar cualquiera,
    ``del self._queues[channel]`` dejaba huérfanas las conexiones que seguían
    vivas. Eso rompía el canal broadcast ``incidents_feed`` (badge de Telegram
    sin refresco en vivo). Los canales 1:1 (tokens de chat ``/chat/stream/<id>``,
    análisis de incidente ``/incidents/stream/<id>``) siguen funcionando igual:
    son simplemente un conjunto de tamaño 1.

    Buffer previo a la suscripción: ``_process_chat`` / ``_analyze_incident_async``
    empiezan a emitir en cuanto arranca la tarea de fondo, a veces antes de que
    el navegador abra el ``EventSource``. Si en ese instante no hay suscriptores,
    el evento se guarda en ``_pending`` y se entrega al PRIMER suscriptor que
    llegue (con TTL: un canal con eventos y sin nadie escuchando se descarta a
    los ``_PENDING_TTL`` s, para no acumular basura).
    """

    _MAXSIZE = 100          # eventos en cola por suscriptor
    _PENDING_TTL = 30.0     # s que se conserva un buffer sin suscriptor

    def __init__(self):
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}
        self._pending: Dict[str, List[str]] = {}
        self._pending_ts: Dict[str, float] = {}

    # ─── internos ───────────────────────────────────────────────────────────

    def _offer(self, queue: asyncio.Queue, payload: str, channel_id: str) -> None:
        """Encola sin bloquear. Si la cola de ESE suscriptor está llena
        (cliente colgado o muy lento), se descarta el evento SOLO para él —
        nunca frena al emisor ni afecta a los demás suscriptores."""
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("sse_slow_consumer_dropped", channel_id=channel_id)

    def _gc_pending(self) -> None:
        now = time.monotonic()
        for cid, ts in list(self._pending_ts.items()):
            if now - ts > self._PENDING_TTL:
                self._pending.pop(cid, None)
                self._pending_ts.pop(cid, None)

    # ─── API ────────────────────────────────────────────────────────────────

    async def publish(self, channel_id: str, event: str, data: dict) -> None:
        """Publica un evento a TODOS los suscriptores del canal."""
        payload = json.dumps({"event": event, "data": data})
        subs = self._subscribers.get(channel_id)
        if subs:
            for queue in list(subs):
                self._offer(queue, payload, channel_id)
            return
        # Nadie escuchando aún: bufferizamos para el primer suscriptor.
        self._gc_pending()
        buf = self._pending.setdefault(channel_id, [])
        buf.append(payload)
        if len(buf) > self._MAXSIZE:
            del buf[0]
        self._pending_ts[channel_id] = time.monotonic()

    async def stream(self, channel_id: str) -> AsyncGenerator[str, None]:
        """Stream SSE para un suscriptor. Su cola es propia; al terminar
        (desconexión o evento done/error) se limpia SOLO esa cola."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._MAXSIZE)
        subs = self._subscribers.setdefault(channel_id, set())
        is_first = len(subs) == 0
        subs.add(queue)

        # El primer suscriptor recibe lo que se hubiera publicado antes de
        # que existiera ningún oyente.
        if is_first:
            for payload in self._pending.pop(channel_id, []):
                self._offer(queue, payload, channel_id)
            self._pending_ts.pop(channel_id, None)

        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {payload}\n\n"

                    data = json.loads(payload)
                    if data.get("event") in ("done", "error"):
                        break

                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'event': 'ping', 'data': {}})}\n\n"
        finally:
            self._unsubscribe(channel_id, queue)

    def _unsubscribe(self, channel_id: str, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(channel_id)
        if subs is None:
            return
        subs.discard(queue)
        if not subs:
            del self._subscribers[channel_id]

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
