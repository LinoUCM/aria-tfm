"""
ARIA Orchestrator — LangGraph multi-agent pipeline.

LLM Fallback chain (en todas las funciones):
  1. Gemini 2.5 Flash   → Mayor calidad
  2. Ollama (ollama4:cloud) → Fallback secundario
  3. Groq LLaMA 3.3 70B → Fallback terciario
"""

import json
import asyncio
import time
from datetime import datetime, timezone
from collections import deque
from typing import TypedDict, Optional, List, Any
from uuid import UUID

from langgraph.graph import StateGraph, END
from google import genai
from google.genai import types
import structlog

from services.sse_manager import sse_manager
from core.config import settings

logger = structlog.get_logger()


class AgentState(TypedDict):
    channel_id: str
    conversation_id: str
    incident_id: Optional[str]
    original_message: str
    image_base64: Optional[str]
    audio_base64: Optional[str]
    transcribed_text: Optional[str]
    vision_analysis: Optional[str]
    rag_results: Optional[List[dict]]
    web_results: Optional[str]
    similar_incidents: Optional[List]
    needs_vision: bool
    needs_voice: bool
    needs_web_search: bool
    skip_rag: bool
    rag_chunks: int
    agents_used: List[str]
    final_response: Optional[str]
    error: Optional[str]


class ARIAOrchestrator:

    GEMINI_MODEL = "gemini-2.5-flash"
    OLLAMA_MODEL = "gemma4:cloud"
    GROQ_MODEL = "llama-3.3-70b-versatile"
    GEMINI_RPM_LIMIT = 14  # Límite seguro por minuto
    GEMINI_TIMEOUT = 45.0   # Timeout extendido a 45s

    TECHNICAL_KEYWORDS = [
        "error", "alert", "incident", "incidencia", "alerta", "fallo", "fail",
        "timeout", "latency", "latencia", "502", "503", "500", "404",
        "cpu", "memory", "memoria", "disk", "disco", "network", "red",
        "deploy", "deployment", "despliegue", "rollback",
        "service", "servicio", "pod", "container", "kubernetes", "k8s",
        "database", "db", "postgres", "redis", "mongo",
        "log", "logs", "trace", "metric", "métrica",
        "qué dice", "qué establece", "según", "artículo", "reglamento",
        "normativa", "regulación", "cómo funciona", "explica", "explain",
        "what is", "how to", "cómo", "cuál", "cuáles", "dónde", "por qué",
        "datadog", "dashboard", "monitor", "alarm",
    ]

    CONVERSATIONAL_PATTERNS = [
        "hola", "hello", "hi", "hey", "buenas", "buenos días", "buenas tardes",
        "gracias", "thanks", "thank you", "ok", "okay", "vale", "de acuerdo",
        "entendido", "perfecto", "genial", "bien", "adiós", "bye", "hasta luego",
        "cómo estás", "qué tal", "good morning", "good afternoon",
    ]

    def __init__(self):
        self.gemini = genai.Client(api_key=settings.google_api_key)
        self._request_times: deque = deque()
        self._rate_lock = asyncio.Lock()
        self.graph = self._build_graph()
        logger.info("orchestrator_initialized")

    # ─── Rate Limiter ─────────────────────────────────────────────────────────

    async def _wait_for_rate_limit(self, tokens: int = 0) -> None:
        """Rate limiter con protección anti-deadlock."""
        async with self._rate_lock:
            now = time.time()
            # Limpiar timestamps antiguos (mayores a 60s)
            while self._request_times and (now - self._request_times[0]) > 60:
                self._request_times.popleft()

            # Verificar si alcanzamos el límite
            if len(self._request_times) >= self.GEMINI_RPM_LIMIT:
                oldest = self._request_times[0]
                wait_time = max(0.5, 60.0 - (now - oldest) + 0.1)
                logger.warning("rate_limit_waiting", wait_seconds=round(wait_time, 1))
            else:
                wait_time = 0

            # Registrar la petición actual
            self._request_times.append(time.time() + wait_time)

        if wait_time > 0:
            await asyncio.sleep(wait_time)

    # ─── LLM Calls with Universal 3-Tier Fallback ──────────────────────────────

    async def _llm_stream(self, prompt: str, channel_id: str) -> str:
        """Stream response using: 1. Gemini -> 2. Ollama -> 3. Groq"""
        # 1. Gemini
        try:
            await self._wait_for_rate_limit(channel_id)
            response = await self.gemini.aio.models.generate_content(
                model=self.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(candidate_count=1),
            )
            full_response = response.text
            if channel_id:
                await sse_manager.token(channel_id, full_response)
            return full_response
        except Exception as e:
            logger.warning("gemini_stream_failed_fallback_ollama", error=str(e))
            if channel_id:
                await sse_manager.publish(
                    channel_id, "status", {"message": f"⏳ Cambiando a Ollama ({self.OLLAMA_MODEL})..."}
                )

        # 2. Ollama (Segundo Fallback)
        try:
            return await self._ollama_stream(prompt, channel_id)
        except Exception as e:
            logger.warning("ollama_failed_fallback_groq", error=str(e))
            if channel_id:
                await sse_manager.publish(channel_id, "status", {"message": "⚡ Cambiando a Groq..."})

        # 3. Groq (Tercer Fallback)
        try:
            return await self._groq_generate(prompt, channel_id)
        except Exception as e:
            logger.error("all_llms_failed_for_stream", error=str(e))
            error_msg = "❌ Error: Fallaron todos los proveedores de IA (Gemini, Ollama y Groq)."
            if channel_id:
                await sse_manager.error(channel_id, error_msg)
            return error_msg

    # ─── LLM Calls with Universal 3-Tier Fallback ──────────────────────────────

    async def _llm_generate(self, prompt: str, image_bytes: Optional[bytes] = None) -> str:
        # 1. Intentar Gemini con Timeout ajustado para generaciones largas (Post-Mortems/Runbooks)
        try:
            await self._wait_for_rate_limit()
            logger.info("calling_gemini_api")

            if image_bytes:
                contents = [prompt, types.Part.from_bytes(data=image_bytes, mime_type="image/png")]
            else:
                contents = prompt

            response = await asyncio.wait_for(
                self.gemini.aio.models.generate_content(
                    model=self.GEMINI_MODEL,
                    contents=contents,
                ),
                timeout=40.0  # 40s da margen suficiente para respuestas extensas de Gemini
            )
            return response.text

        except Exception as e:
            logger.warning("gemini_failed_switching_to_ollama", error=str(e), error_type=type(e).__name__)

        # 2. Fallback a Ollama
        try:
            logger.info("executing_ollama_fallback")
            return await self._ollama_generate(prompt)
        except Exception as e:
            logger.error("ollama_failed_detail", error=str(e))

        # 3. Fallback a Groq
        try:
            logger.info("executing_groq_fallback")
            return await self._groq_generate(prompt, channel_id="")
        except Exception as e:
            logger.error("groq_failed_detail", error=str(e))

        return "## Error al generar respuesta\n\nNo se pudo obtener respuesta de ningún modelo."
    
    
    def _gemini_generate_internal_sync(self, prompt: str, image_bytes: Optional[bytes] = None) -> str:
        """Ejecución síncrona en hilo secundario."""
        if image_bytes:
            response = self.gemini.models.generate_content(
                model=self.GEMINI_MODEL,
                contents=[prompt, types.Part.from_bytes(data=image_bytes, mime_type="image/png")],
            )
        else:
            response = self.gemini.models.generate_content(
                model=self.GEMINI_MODEL, contents=prompt
            )
        return response.text

    async def _ollama_stream(self, prompt: str, channel_id: str) -> str:
        """Stream response desde Ollama."""
        import httpx
        full_response = ""
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{settings.ollama_base_url}/api/generate",
                json={"model": self.OLLAMA_MODEL, "prompt": prompt, "stream": True},
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            token = data.get("response", "")
                            if token:
                                if channel_id:
                                    await sse_manager.token(channel_id, token)
                                full_response += token
                            if data.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue
        return full_response

    async def _ollama_generate(self, prompt: str) -> str:
        """Generación directa (no-stream) desde Ollama."""
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={"model": self.OLLAMA_MODEL, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")

    async def _groq_generate(self, prompt: str, channel_id: str = "") -> str:
        """Generación desde Groq."""
        from groq import AsyncGroq
        client = AsyncGroq(api_key=settings.groq_api_key)
        full_response = ""
        
        stream = await client.chat.completions.create(
            model=self.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            stream=True,
        )

        async for chunk in stream:
            token = chunk.choices[0].delta.content or ""
            if token:
                if channel_id:
                    await sse_manager.token(channel_id, token)
                full_response += token
                
        return full_response

    # ─── Router ───────────────────────────────────────────────────────────────

    def _classify_message(self, message: str) -> tuple[bool, int]:
        msg = message.lower().strip()
        words = msg.split()
        if len(words) <= 4:
            for pattern in self.CONVERSATIONAL_PATTERNS:
                if pattern in msg:
                    return True, 0
        technical_score = sum(1 for kw in self.TECHNICAL_KEYWORDS if kw in msg)
        if technical_score == 0 and len(words) <= 5:
            return True, 0
        elif technical_score >= 3 or len(words) > 20:
            return False, 5
        elif technical_score >= 1:
            return False, min(3, technical_score + 1)
        else:
            return False, 2

    def _route_after_router(self, state: AgentState) -> str:
        if state["needs_voice"] and state["audio_base64"]:
            return "voice"
        elif state["needs_vision"] and state["image_base64"]:
            return "vision"
        elif state["skip_rag"]:
            return "synthesis"
        return "rag"

    # ─── Graph ────────────────────────────────────────────────────────────────

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(AgentState)
        workflow.add_node("router", self._router_node)
        workflow.add_node("voice", self._voice_node)
        workflow.add_node("vision", self._vision_node)
        workflow.add_node("rag", self._rag_node)
        workflow.add_node("search", self._search_node)
        workflow.add_node("synthesis", self._synthesis_node)
        workflow.set_entry_point("router")
        workflow.add_conditional_edges("router", self._route_after_router,
            {"voice": "voice", "vision": "vision", "rag": "rag", "synthesis": "synthesis"})
        workflow.add_conditional_edges("voice",
            lambda s: "vision" if s["needs_vision"] else ("synthesis" if s["skip_rag"] else "rag"),
            {"vision": "vision", "rag": "rag", "synthesis": "synthesis"})
        workflow.add_edge("vision", "rag")
        workflow.add_conditional_edges("rag",
            lambda s: "search" if s["needs_web_search"] else "synthesis",
            {"search": "search", "synthesis": "synthesis"})
        workflow.add_edge("search", "synthesis")
        workflow.add_edge("synthesis", END)
        return workflow.compile()

    # ─── Nodes ────────────────────────────────────────────────────────────────

    async def _router_node(self, state: AgentState) -> AgentState:
        if state.get("channel_id"):
            await sse_manager.agent_start(state["channel_id"], "router")
        state["needs_voice"] = bool(state.get("audio_base64"))
        state["needs_vision"] = bool(state.get("image_base64"))
        state["needs_web_search"] = False
        state["agents_used"] = ["router"]
        skip_rag, rag_chunks = self._classify_message(state.get("original_message", ""))
        state["skip_rag"] = skip_rag
        state["rag_chunks"] = rag_chunks
        logger.info("router_decision", message=state.get("original_message", "")[:50],
            skip_rag=skip_rag, rag_chunks=rag_chunks)
        if state.get("channel_id"):
            await sse_manager.agent_end(state["channel_id"], "router")
        return state

    async def _voice_node(self, state: AgentState) -> AgentState:
        if state.get("channel_id"):
            await sse_manager.agent_start(state["channel_id"], "voice")
        state["agents_used"].append("voice")
        try:
            import base64
            from groq import AsyncGroq
            client = AsyncGroq(api_key=settings.groq_api_key)
            audio_bytes = base64.b64decode(state["audio_base64"])
            transcription = await client.audio.transcriptions.create(
                file=("audio.webm", audio_bytes, "audio/webm"),
                model="whisper-large-v3", language="es")
            state["transcribed_text"] = transcription.text
            if state.get("channel_id"):
                await sse_manager.publish(
                    state["channel_id"], "transcription", {"text": transcription.text}
                )
            skip_rag, rag_chunks = self._classify_message(transcription.text)
            state["skip_rag"] = skip_rag
            state["rag_chunks"] = rag_chunks
        except Exception as e:
            logger.error("voice_node_error", error=str(e))
            state["transcribed_text"] = state["original_message"]
        if state.get("channel_id"):
            await sse_manager.agent_end(state["channel_id"], "voice")
        return state

    async def _vision_node(self, state: AgentState) -> AgentState:
        if state.get("channel_id"):
            await sse_manager.agent_start(state["channel_id"], "vision")
        state["agents_used"].append("vision")
        try:
            import base64
            text_input = state.get("transcribed_text") or state["original_message"]
            prompt = f"""You are an expert SRE analyzing a screenshot from an operations context.
User message: {text_input}
Extract: content type, key technical details, severity, relevant troubleshooting info.
Be concise. Max 150 words."""
            image_bytes = base64.b64decode(state["image_base64"])
            state["vision_analysis"] = await self._llm_generate(prompt, image_bytes)
            state["skip_rag"] = False
            state["rag_chunks"] = 3
        except Exception as e:
            logger.error("vision_node_error", error=str(e))
            state["vision_analysis"] = "Could not analyze image."
        if state.get("channel_id"):
            await sse_manager.agent_end(state["channel_id"], "vision")
        return state

    async def _rag_node(self, state: AgentState) -> AgentState:
        if state.get("channel_id"):
            await sse_manager.agent_start(state["channel_id"], "rag")
        state["agents_used"].append("rag")
        try:
            from services.indexing_service import get_indexing_service
            from services.memory_service import memory_service
            query_parts = [state["original_message"]]
            if state.get("transcribed_text"):
                query_parts.append(state["transcribed_text"])
            if state.get("vision_analysis"):
                query_parts.append(state["vision_analysis"])
            query = " ".join(query_parts)
            n_results = state.get("rag_chunks", 3)
            results = get_indexing_service().search(query=query, n_results=n_results)
            state["rag_results"] = results
            similar = await memory_service.find_similar_incidents(query=query)
            state["similar_incidents"] = similar
            if not results or (results and results[0]["relevance_score"] < 70):
                state["needs_web_search"] = True
            # Solo emitimos "Sources consulted" si la KB tenía cobertura suficiente
            # (needs_web_search False). Si hubo que caer a búsqueda web, la respuesta
            # ya dice que no encontró contexto útil: mostrar esos documentos de baja
            # relevancia contradiría al propio texto.
            if results and not state["needs_web_search"] and state.get("channel_id"):
                # Fuentes citadas agrupadas por documento de origen: si el mismo
                # archivo aparece en varios chunks, se muestra una sola vez con la
                # relevancia más alta. `results` ya viene ordenado desc (sorted en
                # indexing_service.py), así que basta con quedarse con la primera
                # aparición de cada `filename`. Solo afecta a lo que ve el usuario:
                # el contexto que recibe el LLM (state["rag_results"]) sigue con
                # todos los chunks.
                seen_files = set()
                unique_sources = []
                for r in results:
                    if r["filename"] in seen_files:
                        continue
                    seen_files.add(r["filename"])
                    unique_sources.append({
                        "filename": r["filename"], "relevance_score": r["relevance_score"],
                        "category": r["category"], "page": r.get("page"), "source_url": r.get("source_url"),
                    })
                await sse_manager.rag_sources(state["channel_id"], unique_sources)
            if similar and state.get("channel_id"):
                await sse_manager.similar_incidents(state["channel_id"], similar)
        except Exception as e:
            logger.error("rag_node_error", error=str(e))
            state["rag_results"] = []
            state["similar_incidents"] = []
        if state.get("channel_id"):
            await sse_manager.agent_end(state["channel_id"], "rag")
        return state

    async def _search_node(self, state: AgentState) -> AgentState:
        if state.get("channel_id"):
            await sse_manager.agent_start(state["channel_id"], "search")
        state["agents_used"].append("search")
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=settings.tavily_api_key)
            query = state.get("transcribed_text") or state["original_message"]
            prefix = "SRE operations "
            truncated = query[:400 - len(prefix)].strip()
            response = client.search(query=f"{prefix}{truncated}", search_depth="basic", max_results=3)
            # Tavily es una API externa: no asumimos que cada resultado traiga
            # siempre title/url/content — usamos .get() con fallback para que un
            # resultado malformado no tire abajo el nodo entero por un KeyError.
            web_items = response.get("results", [])
            state["web_results"] = "\n\n".join([
                f"**{r.get('title', 'Untitled')}** ({r.get('url', '')})\n{r.get('content', '')}"
                for r in web_items
            ])
            if web_items and state.get("channel_id"):
                await sse_manager.web_results(state["channel_id"], [
                    {"title": r.get("title", "Untitled"), "url": r.get("url", "")}
                    for r in web_items if r.get("url")
                ])
        except Exception as e:
            logger.error("search_node_error", error=str(e))
            state["web_results"] = ""
        if state.get("channel_id"):
            await sse_manager.agent_end(state["channel_id"], "search")
        return state

    async def _synthesis_node(self, state: AgentState) -> AgentState:
        if state.get("channel_id"):
            await sse_manager.agent_start(state["channel_id"], "synthesis")
        state["agents_used"].append("synthesis")
        try:
            context_parts = []
            if state.get("vision_analysis"):
                context_parts.append(f"## Image Analysis\n{state['vision_analysis']}")
            if state.get("rag_results"):
                # Mismo umbral (70) que _rag_node ya usa para decidir needs_web_search:
                # antes, cualquier chunk recuperado entraba al contexto del LLM aunque
                # tuviera relevancia baja, contaminando las citas generadas (limitación
                # 5.1 de la evaluación). Ahora solo pasa a síntesis lo que ya supera el
                # umbral que el propio sistema usa para considerar la KB suficiente.
                relevant_results = [r for r in state["rag_results"] if r["relevance_score"] >= 70]
                if relevant_results:
                    included_chunks = relevant_results[:3]
                    kb_context = "\n\n".join([
                        f"**[{r['filename']} — {r['relevance_score']}% relevance]**\n{r['content']}"
                        for r in included_chunks
                    ])
                    context_parts.append(f"## Knowledge Base\n{kb_context}")
                    # Trazabilidad estructurada: una fila rag_references por cada
                    # chunk que REALMENTE entra en el prompt (included_chunks), no
                    # por cada resultado que devolvió la búsqueda semántica. Es una
                    # capa adicional, no sustituye la extracción de citas por regex
                    # sobre la respuesta. Best-effort: sesión y try/except propios.
                    await self._persist_rag_references(
                        state.get("conversation_id"), included_chunks
                    )
            if state.get("similar_incidents"):
                incidents_text = "\n".join([
                    f"- {inc['title']} ({inc['created_at']}) → {inc.get('resolution', 'Unresolved')}"
                    for inc in state["similar_incidents"][:3]
                ])
                context_parts.append(f"## Similar Past Incidents\n{incidents_text}")
            if state.get("web_results"):
                context_parts.append(f"## Web Search Results\n{state['web_results']}")

            user_query = state.get("transcribed_text") or state["original_message"]
            if context_parts:
                system = """You are ARIA, an expert AI assistant for Operations teams.
Be concise, technical, and actionable. Structure your response with:
1. Quick diagnosis
2. Recommended steps (numbered)
3. Relevant context from KB (cite sources)
4. If the context contains a "## Web Search Results" section, add a separate
   "Web sources" section at the end listing the title and URL of each web
   result you actually used — keep these clearly distinct from KB sources and
   do not merge web-sourced facts into your own knowledge without attribution.
Never guess critical values."""
            else:
                system = "You are ARIA, an expert AI assistant for Operations teams. Be brief and friendly."

            full_prompt = f"{system}\n\n{chr(10).join(context_parts)}\n\n## User Query\n{user_query}"
            full_response = await self._llm_stream(full_prompt, state["channel_id"])
            state["final_response"] = full_response
            if state.get("channel_id"):
                await sse_manager.done(state["channel_id"], full_response)
        except Exception as e:
            logger.error("synthesis_node_error", error=str(e))
            if state.get("channel_id"):
                await sse_manager.error(state["channel_id"], f"Error: {str(e)}")
            state["error"] = str(e)
        if state.get("channel_id"):
            await sse_manager.agent_end(state["channel_id"], "synthesis")
        return state

    async def _persist_rag_references(self, conversation_id: Optional[str], chunks: List[dict]) -> None:
        """Deja en la tabla rag_references una fila por cada chunk documental que
        de verdad entró en el prompt de síntesis.

        Capa de trazabilidad estructurada: NO reemplaza la extracción de citas por
        regex sobre la respuesta del LLM ni cambia el texto que ve el usuario. Es
        best-effort — cualquier fallo se registra y se traga para que nunca rompa
        ni ralentice de forma visible la respuesta (mismo criterio que la creación
        de Document en webhooks.py).

        Se saltan (sin abortar el resto) los chunks cuyo doc_id no sea un UUID
        válido o que no tengan todavía fila en documents: ambas columnas son FK
        obligatorias a nivel de constraint (conversation_id -> conversations.id,
        document_id -> documents.id).
        """
        if not conversation_id or not chunks:
            return
        try:
            from sqlalchemy import select as _select
            from core.database import AsyncSessionLocal
            from models.database import Conversation, Document, RagReference

            try:
                conv_uuid = UUID(str(conversation_id))
            except (ValueError, TypeError):
                logger.warning("rag_ref_skip_bad_conversation_id", value=str(conversation_id))
                return

            async with AsyncSessionLocal() as db:
                conv_exists = await db.execute(
                    _select(Conversation.id).where(Conversation.id == conv_uuid)
                )
                if conv_exists.scalar_one_or_none() is None:
                    logger.warning("rag_ref_skip_no_conversation", conversation_id=str(conv_uuid))
                    return

                inserted = 0
                for r in chunks:
                    raw_doc_id = (r.get("doc_id") or "").strip()
                    try:
                        doc_uuid = UUID(raw_doc_id)
                    except (ValueError, TypeError):
                        logger.warning("rag_ref_skip_bad_doc_id",
                                       doc_id=raw_doc_id, filename=r.get("filename"))
                        continue

                    doc_exists = await db.execute(
                        _select(Document.id).where(Document.id == doc_uuid)
                    )
                    if doc_exists.scalar_one_or_none() is None:
                        logger.warning("rag_ref_skip_orphan_doc",
                                       doc_id=raw_doc_id, filename=r.get("filename"))
                        continue

                    page = r.get("page")
                    db.add(RagReference(
                        conversation_id=conv_uuid,
                        document_id=doc_uuid,
                        chunk_content=r.get("content"),
                        relevance_score=r.get("relevance_score"),
                        chunk_index=r.get("chunk_index"),
                        page_number=page if page else None,
                    ))
                    inserted += 1

                if inserted:
                    await db.commit()
                    logger.info("rag_references_persisted",
                                conversation_id=str(conv_uuid), count=inserted)
        except Exception as e:
            logger.error("rag_references_persist_failed", error=str(e))

    # ─── Public Interface ──────────────────────────────────────────────────────

    async def run(self, channel_id, conversation_id, message, image_base64=None, audio_base64=None):
        initial_state = AgentState(
            channel_id=channel_id, conversation_id=str(conversation_id),
            incident_id=None, original_message=message,
            image_base64=image_base64, audio_base64=audio_base64,
            transcribed_text=None, vision_analysis=None, rag_results=None,
            web_results=None, similar_incidents=None,
            needs_vision=False, needs_voice=False, needs_web_search=False,
            skip_rag=False, rag_chunks=3, agents_used=[],
            final_response=None, error=None,
        )
        final_state = await self.graph.ainvoke(initial_state)
        return final_state.get("final_response", "")
    
    # ─── Analyze Incident ──────────────────────────────────────────────────────


    async def analyze_incident(self, channel_id: str, incident_id: str, payload: dict) -> dict:
        title = payload.get("title", "")
        text = payload.get("text", "")
        metrics = payload.get("metrics", {})
        tags = payload.get("tags", [])
        
        service = next(
            (t.split("service:")[1] for t in tags if t.startswith("service:")),
            "unknown"
        )

        message = f"""Datadog Alert Received:
Title: {title}
Description: {text}
Reported Service: {service}
Metrics: {json.dumps(metrics, indent=2)}

SYSTEM TOPOLOGY NODES:
- api-gateway
- payment-service
- checkout-service
- aria_db
- redis-cache
- notification-service

Please analyze this alert and provide:
1. Likely root cause
2. Immediate actions to take
3. Relevant runbooks or past incidents

CRITICAL REQUIREMENT:
At the very end of your response, you MUST append a JSON code block identifying
the root cause and affected service.

If, and ONLY if, the root cause clearly belongs to one of the SYSTEM TOPOLOGY
NODES listed above, use that exact node_id for both fields.

If the root cause is external to our system (e.g., a third-party vendor, CDN,
DNS provider, external API, or any dependency NOT listed in SYSTEM TOPOLOGY
NODES), you MUST use the literal value "external" for both fields instead of
guessing or forcing an internal node. Do not force a match to an internal node
when the evidence points outside our system.

Additionally, if — and ONLY if — one of the following pre-approved automated
remediation actions would directly and safely resolve this specific incident,
include its exact action_id as "suggested_action". If no listed action
clearly applies, or you are not confident it is the correct fix, use the
literal value null. Do not force a match.

- "RESTART_CONTAINER": restarting a specific hung or crashed Docker container.
- "TERMINATE_IDLE_CONNECTIONS": terminating idle/stale database connections
  that are exhausting the connection pool.
- "FLUSH_REDIS_CACHE": purging a corrupted or bloated Redis cache.

```json
{{
  "root_cause": "<node_id_or_external>",
  "service_affected": "<node_id_or_external>",
  "suggested_action": "<RESTART_CONTAINER|TERMINATE_IDLE_CONNECTIONS|FLUSH_REDIS_CACHE|null>"
}}
```"""

        # El pipeline de RAG deja trazabilidad en rag_references, cuya FK
        # conversation_id apunta a conversations.id. El análisis de un incidente no
        # nace de una conversación de chat, así que reutilizamos (o creamos una
        # sola vez) una Conversation "sintética" ligada a este incident_id:
        #  - get-or-create por incident_id => un re-análisis del mismo incidente no
        #    inserta una fila nueva, reutiliza la existente.
        #  - owner_username=None => queda fuera del historial de chat del usuario
        #    (GET /chat/conversations filtra por owner_username) y no colisiona con
        #    un eventual chat de usuario ligado al mismo incidente.
        # Si algo falla aquí, seguimos con el incident_id: la escritura de
        # rag_references es best-effort y simplemente se omitirá.
        conversation_id = incident_id
        try:
            from sqlalchemy import select as _select
            from core.database import AsyncSessionLocal
            from models.database import Conversation

            inc_uuid = UUID(incident_id)
            async with AsyncSessionLocal() as db:
                existing = await db.execute(
                    _select(Conversation).where(
                        Conversation.incident_id == inc_uuid,
                        Conversation.owner_username.is_(None),
                    )
                )
                conv = existing.scalars().first()
                if conv is None:
                    conv = Conversation(
                        incident_id=inc_uuid,
                        owner_username=None,
                        title=f"[Análisis ARIA] {title or incident_id}"[:500],
                        messages=[],
                        agents_used=[],
                        input_modalities=[],
                    )
                    db.add(conv)
                    await db.commit()
                    await db.refresh(conv)
                conversation_id = str(conv.id)
        except Exception as e:
            logger.error("incident_conversation_get_or_create_failed",
                         incident_id=incident_id, error=str(e))

        # Ejecutamos el flujo multinodo completo de ARIA (RAG + memoria de incidentes)
        full_response = await self.run(
            channel_id=channel_id,
            conversation_id=conversation_id,
            message=message
        )

        # Extraemos el JSON estructurado del final del análisis para el backend/frontend
        root_cause = service
        service_affected = service
        suggested_action = None
        ALLOWED_SUGGESTED_ACTIONS = {"RESTART_CONTAINER", "TERMINATE_IDLE_CONNECTIONS", "FLUSH_REDIS_CACHE"}

        try:
            if "```json" in full_response:
                json_str = full_response.split("```json")[-1].split("```")[0].strip()
                data = json.loads(json_str)
                root_cause = data.get("root_cause", service)
                service_affected = data.get("service_affected", service)
                candidate_action = data.get("suggested_action")
                if candidate_action in ALLOWED_SUGGESTED_ACTIONS:
                    suggested_action = candidate_action
        except Exception as e:
            logger.warning("rca_json_parsing_failed", error=str(e))

        return {
            "analysis": full_response,
            "root_cause": root_cause,
            "service_affected": service_affected,
            "suggested_action": suggested_action
        }

    # ─── Runbook & Post-Mortem Synthesis ────────────────────────────────────────

    async def synthesize_runbook(
        self,
        incident_title: str,
        incident_id: str,
        service: str,
        host: str,
        resolved_at: str,
        description: str,
        analysis: str,
        engineer: str,
        notes: str,
    ) -> str:
        prompt = f"""Eres ARIA, un asistente IA experto en SRE y Operaciones.
Tu tarea es transformar los datos de un incidente resuelto y las notas tomadas por el ingeniero en un documento Runbook en formato Markdown profesional y limpio para la Base de Conocimiento.

INFORMACIÓN DEL INCIDENTE:
- Título: {incident_title}
- ID Incidente: {incident_id}
- Servicio Afectado: {service}
- Host: {host}
- Fecha/Hora de Resolución: {resolved_at}
- Descripción: {description}
- Análisis previo: {analysis}

NOTAS DE RESOLUCIÓN Y ACCIONES TOMADAS POR EL INGENIERO ({engineer}):
{notes}

INSTRUCCIONES DE FORMATO:
Genera un documento Markdown bien estructurado con las siguientes secciones:
# Runbook: [Título optimizado y claro]

## 1. Contexto del Incidente
- Incluye metadatos clave (ID, Servicio, Host, Ingeniero). Para la
  Fecha/Hora de Resolución, usa EXACTAMENTE el valor proporcionado en
  "Fecha/Hora de Resolución" arriba — no inventes una fecha ni uses un
  formato de ejemplo tipo YYYY-MM-DD, copia el valor real tal cual.

## 2. Descripción y Causa Raíz
- Resume qué falló y la causa raíz identificada.

## 3. Procedimiento de Solución Aplicado
- Expande, formaliza y estructura técnicamente las notas que proporcionó el ingeniero en pasos claros de remediación.

## 4. Acciones Preventivas / Verificación
- Ofrece recomendaciones técnicas breves para evitar que vuelva a suceder.

IMPORTANTE: Responde ÚNICAMENTE con el contenido Markdown final, sin saludos ni texto explicativo adicional fuera del documento."""

        try:
            logger.info("synthesizing_runbook_with_llm", incident_id=incident_id)
            return await self._llm_generate(prompt)
        except Exception as e:
            logger.error("runbook_synthesis_failed_fallback_to_static", error=str(e))
            timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            return f"""# Runbook: {incident_title}

## 1. Contexto del Incidente
- **ID Incidente:** {incident_id}
- **Servicio Afectado:** {service}
- **Host:** {host}
- **Fecha:** {timestamp_str}
- **Ingeniero:** {engineer}

## 2. Descripción / Alerta
{description}

## 3. Solución Aplicada (Feedback del Ingeniero)
{notes}
"""

    async def synthesize_postmortem(
        self,
        incident: Any,
        audit_logs: Optional[List[Any]] = None,
        timeline_events: Optional[List[Any]] = None,
    ) -> str:
        def get_attr(obj: Any, key: str, default: Any = "N/A"):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        inc_id = get_attr(incident, 'id')
        inc_title = get_attr(incident, 'title')
        inc_sev = get_attr(incident, 'severity')
        inc_svc = get_attr(incident, 'service_affected')
        inc_host = get_attr(incident, 'host')
        inc_created = get_attr(incident, 'created_at')
        inc_metrics = get_attr(incident, 'metrics')
        inc_tags = get_attr(incident, 'tags')
        inc_analysis = get_attr(incident, 'analysis')

        formatted_logs = []
        if audit_logs:
            for log in audit_logs:
                ts = get_attr(log, 'timestamp', '')
                action = get_attr(log, 'action_id', '')
                target = get_attr(log, 'target', '')
                by = get_attr(log, 'executed_by', '')
                status = get_attr(log, 'status', '')
                formatted_logs.append(f"- [{ts}] Action: {action} | Target: {target} | Executed By: {by} | Status: {status}")
        
        audit_str = "\n".join(formatted_logs) if formatted_logs else "Sin registros de auditoría"
        timeline_str = json.dumps(timeline_events, indent=2) if timeline_events else "No timeline specified"

        prompt = f"""
Eres ARIA, una IA avanzada de Operaciones e Ingeniería de Confiabilidad de Sitio (SRE).
Genera un informe Post-Mortem exhaustivo y highly profesional en Markdown para el siguiente incidente.

## DATOS DEL INCIDENTE:
- ID: {inc_id}
- Título: {inc_title}
- Severidad: {inc_sev}
- Servicio Afectado: {inc_svc}
- Host: {inc_host}
- Inicio / Detección: {inc_created}
- Métricas Registradas: {inc_metrics}
- Tags: {inc_tags}
- Análisis Inicial de ARIA: {inc_analysis}

## CRONOLOGÍA DE EVENTOS (TIMELINE):
{timeline_str}

## REGISTROS DE AUDITORÍA Y ACCIONES DE REMEDIACIÓN:
{audit_str}

---

## INSTRUCCIONES DE ESTRUCTURA DEL POST-MORTEM (Markdown):
El informe debe contener exactamente las siguientes secciones estructuradas con precisión:

1. **Resumen Ejecutivo (Executive Summary)**: Breve descripción del impacto, duración total y resolución.
2. **Impacto en el Negocio y Servicios**: Lista detallada de servicios, APIs y SLA comprometidos.
3. **Línea de Tiempo Precisa (Timeline)**: Formato cronológico tabla o lista (Detección, Diagnóstico, Remediación y Cierre).
4. **Métricas Afectadas durante la Crisis**: Análisis numérico de picos de CPU, latencia, tasa de errores (HTTP 5xx, memoria, etc.).
5. **Análisis de Causa Raíz (Root Cause Analysis - RCA)**: Identificación técnica detallada basada en el análisis de ARIA.
6. **Acciones de Remediación Ejecutadas**: Detalle de las acciones tomadas (p. ej. reinicio de contenedores, escalado, flushing de memoria).
7. **Acciones Preventivas y Lecciones Aprendidas (Action Items)**: 
   - Tabla con columnas: `[ID | Acción Preventiva | Prioridad | Asignado | Estado]` con tareas concretas para evitar la reincidencia.

## REGLAS STRICTAS DE FORMATO Y ESTILO:
- NO utilices sintaxis de LaTeX ni símbolos de ecuaciones (como $, $$, \\frac, \\matrix, etc.).
- Formatea variables, métricas, nombres de contenedores, herramientas y comandos usando únicamente comillas invertidas / código inline de Markdown (ejemplo: `maxmemory`, `4800 ms`, `18.4%`, `aria-redis-1`).
- Asegúrate de renderizar las tablas usando únicamente la sintaxis estándar de Markdown (`| Columna |`).
- Sé riguroso, técnico, directo y utiliza un tono profesional SRE. 
- Responde ÚNICAMENTE con el contenido Markdown sin introducciones ni saludos.
"""

        try:
            logger.info("synthesizing_postmortem_with_llm", incident_id=str(inc_id))
            return await self._llm_generate(prompt)
        except Exception as e:
            logger.error("postmortem_synthesis_failed", error=str(e))
            return f"""# 📄 Post-Mortem Report: {inc_title}

## 📌 Metadatos del Incidente
- **ID:** {inc_id}
- **Severidad:** {inc_sev}
- **Servicio Afectado:** {inc_svc}
- **Host:** {inc_host}
- **Fecha de Detección:** {inc_created}

## 🔍 Análisis de Causa Raíz (RCA)
{inc_analysis}

## ⏱️ Historial de Auditoría
{audit_str}
"""


orchestrator = ARIAOrchestrator()