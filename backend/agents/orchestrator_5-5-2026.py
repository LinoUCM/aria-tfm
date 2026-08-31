"""
ARIA Orchestrator — LangGraph multi-agent pipeline.

Graph flow:
  input → router → [vision?, voice?] → [rag?] → [search?] → synthesis → output

Features:
  - Smart router: skips RAG for conversational messages
  - Token-aware: adjusts chunk count based on query complexity  
  - Rate limiter: respects Gemini RPM limits automatically
  - Retry with exponential backoff on 429 errors
"""

import json
import asyncio
import time
from collections import deque
from typing import TypedDict, Optional, List
from uuid import UUID

from langgraph.graph import StateGraph, END
from google import genai
from google.genai import types
from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    retry_if_exception_type,
    before_sleep_log,
)
import structlog

from services.sse_manager import sse_manager
from core.config import settings

logger = structlog.get_logger()


# ─── Agent State ──────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # Input
    channel_id: str
    conversation_id: str
    incident_id: Optional[str]
    original_message: str
    image_base64: Optional[str]
    audio_base64: Optional[str]

    # Intermediate results
    transcribed_text: Optional[str]
    vision_analysis: Optional[str]
    rag_results: Optional[List[dict]]
    web_results: Optional[str]
    similar_incidents: Optional[List]

    # Router decisions
    needs_vision: bool
    needs_voice: bool
    needs_web_search: bool
    skip_rag: bool       # True for conversational messages
    rag_chunks: int      # How many chunks to retrieve (1-5)

    # Output
    agents_used: List[str]
    final_response: Optional[str]
    error: Optional[str]


# ─── Orchestrator ─────────────────────────────────────────────────────────────

class ARIAOrchestrator:

    # Gemini model — flash-lite has 30 RPM on free tier (vs 10 for 2.5-flash)
    GEMINI_MODEL = "gemini-2.5-flash"

    # Rate limit — set to 25 to leave margin below the 30 RPM limit
    GEMINI_RPM_LIMIT = 25

    # Keywords indicating technical questions that need RAG
    TECHNICAL_KEYWORDS = [
        # Incidents & ops
        "error", "alert", "incident", "incidencia", "alerta", "fallo", "fail",
        "timeout", "latency", "latencia", "502", "503", "500", "404",
        "cpu", "memory", "memoria", "disk", "disco", "network", "red",
        "deploy", "deployment", "despliegue", "rollback",
        "service", "servicio", "pod", "container", "kubernetes", "k8s",
        "database", "db", "postgres", "redis", "mongo",
        "log", "logs", "trace", "metric", "métrica",
        # Doc questions
        "qué dice", "qué establece", "según", "artículo", "reglamento",
        "normativa", "regulación", "cómo funciona", "explica", "explain",
        "what is", "how to", "cómo", "cuál", "cuáles", "dónde", "por qué",
        # Datadog
        "datadog", "dashboard", "monitor", "alarm",
    ]

    # Conversational patterns — never need RAG
    CONVERSATIONAL_PATTERNS = [
        "hola", "hello", "hi", "hey", "buenas", "buenos días", "buenas tardes",
        "gracias", "thanks", "thank you", "ok", "okay", "vale", "de acuerdo",
        "entendido", "perfecto", "genial", "bien", "adiós", "bye", "hasta luego",
        "cómo estás", "qué tal", "good morning", "good afternoon",
    ]

    def __init__(self):
        self.gemini = genai.Client(api_key=settings.google_api_key)
        # Sliding window rate limiter
        self._request_times: deque = deque()
        self._rate_lock = asyncio.Lock()
        self.graph = self._build_graph()
        logger.info("orchestrator_initialized", model=self.GEMINI_MODEL, rpm_limit=self.GEMINI_RPM_LIMIT)

    # ─── Rate Limiter ─────────────────────────────────────────────────────────

    async def _wait_for_rate_limit(self, channel_id: str = "") -> None:
        """
        Sliding window rate limiter.
        Waits if we've hit GEMINI_RPM_LIMIT requests in the last 60 seconds.
        """
        async with self._rate_lock:
            now = time.time()

            # Remove timestamps older than 60 seconds
            while self._request_times and now - self._request_times[0] > 60:
                self._request_times.popleft()

            # If at limit, wait until oldest request is > 60s ago
            if len(self._request_times) >= self.GEMINI_RPM_LIMIT:
                oldest = self._request_times[0]
                wait_time = 60.0 - (now - oldest) + 0.5  # 0.5s safety margin
                if wait_time > 0:
                    logger.warning(
                        "rate_limit_waiting",
                        wait_seconds=round(wait_time, 1),
                        requests_in_window=len(self._request_times),
                    )
                    if channel_id:
                        await sse_manager.publish(
                            channel_id, "status",
                            {"message": f"⏳ Rate limit — waiting {wait_time:.0f}s..."}
                        )
                    await asyncio.sleep(wait_time)

            # Register this request
            self._request_times.append(time.time())

    # ─── Gemini Calls with Retry ──────────────────────────────────────────────

    async def _gemini_stream(self, prompt: str, channel_id: str) -> str:
        """
        Calls Gemini with streaming + rate limiting + retry on 429.
        Returns the complete response text.
        """
        await self._wait_for_rate_limit(channel_id)

        full_response = ""
        attempt = 0
        max_attempts = 3

        while attempt < max_attempts:
            try:
                response = await self.gemini.aio.models.generate_content(
                    model=self.GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        candidate_count=1,
                    ),
                )
                # Sin streaming por ahora — enviamos todo de golpe
                full_response = response.text
                await sse_manager.token(channel_id, full_response)
                return full_response

            except Exception as e:
                attempt += 1
                error_str = str(e)

                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "503" in error_str or "UNAVAILABLE" in error_str:
                    if attempt < max_attempts:
                        wait = 2 ** attempt * 5  # 10s, 20s, 40s
                        logger.warning(
                            "gemini_rate_limit_retry",
                            attempt=attempt,
                            wait_seconds=wait,
                        )
                        if channel_id:
                            await sse_manager.publish(
                                channel_id, "status",
                                {"message": f"⏳ Rate limit — retrying in {wait}s (attempt {attempt}/{max_attempts})..."}
                            )
                        await asyncio.sleep(wait)
                        full_response = ""  # reset before retry
                    else:
                        raise
                else:
                    raise

        return full_response

    async def _gemini_generate(self, prompt: str, image_bytes: Optional[bytes] = None) -> str:
        """
        Single (non-streaming) Gemini call with rate limiting + retry.
        Used for vision analysis.
        """
        await self._wait_for_rate_limit()

        for attempt in range(3):
            try:
                if image_bytes:
                    response = self.gemini.models.generate_content(
                        model=self.GEMINI_MODEL,
                        contents=[
                            prompt,
                            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                        ],
                    )
                else:
                    response = self.gemini.models.generate_content(
                        model=self.GEMINI_MODEL,
                        contents=prompt,
                    )
                return response.text

            except Exception as e:
                if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and attempt < 2:
                    wait = 2 ** attempt * 5
                    logger.warning("gemini_vision_retry", attempt=attempt + 1, wait=wait)
                    await asyncio.sleep(wait)
                else:
                    raise

    # ─── Router Logic ─────────────────────────────────────────────────────────

    def _classify_message(self, message: str) -> tuple[bool, int]:
        """
        Returns (skip_rag, rag_chunks).
        skip_rag=True  → conversational, go direct to synthesis
        rag_chunks     → how many KB chunks to retrieve (saves tokens)
        """
        msg = message.lower().strip()
        words = msg.split()

        # Very short + conversational pattern → skip RAG
        if len(words) <= 4:
            for pattern in self.CONVERSATIONAL_PATTERNS:
                if pattern in msg:
                    return True, 0

        # Count technical keywords
        technical_score = sum(1 for kw in self.TECHNICAL_KEYWORDS if kw in msg)

        if technical_score == 0 and len(words) <= 5:
            return True, 0          # short, non-technical → skip RAG
        elif technical_score >= 3 or len(words) > 20:
            return False, 5         # very technical → full RAG
        elif technical_score >= 1:
            return False, min(3, technical_score + 1)  # moderate → partial RAG
        else:
            return False, 2         # general question → minimal RAG

    def _route_after_router(self, state: AgentState) -> str:
        if state["needs_voice"] and state["audio_base64"]:
            return "voice"
        elif state["needs_vision"] and state["image_base64"]:
            return "vision"
        elif state["skip_rag"]:
            return "synthesis"
        return "rag"

    # ─── Graph Builder ────────────────────────────────────────────────────────

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(AgentState)

        workflow.add_node("router", self._router_node)
        workflow.add_node("voice", self._voice_node)
        workflow.add_node("vision", self._vision_node)
        workflow.add_node("rag", self._rag_node)
        workflow.add_node("search", self._search_node)
        workflow.add_node("synthesis", self._synthesis_node)

        workflow.set_entry_point("router")

        workflow.add_conditional_edges(
            "router",
            self._route_after_router,
            {
                "voice": "voice",
                "vision": "vision",
                "rag": "rag",
                "synthesis": "synthesis",
            }
        )
        workflow.add_conditional_edges(
            "voice",
            lambda s: "vision" if s["needs_vision"] else ("synthesis" if s["skip_rag"] else "rag"),
            {"vision": "vision", "rag": "rag", "synthesis": "synthesis"}
        )
        workflow.add_edge("vision", "rag")
        workflow.add_conditional_edges(
            "rag",
            lambda s: "search" if s["needs_web_search"] else "synthesis",
            {"search": "search", "synthesis": "synthesis"}
        )
        workflow.add_edge("search", "synthesis")
        workflow.add_edge("synthesis", END)

        return workflow.compile()

    # ─── Nodes ────────────────────────────────────────────────────────────────

    async def _router_node(self, state: AgentState) -> AgentState:
        await sse_manager.agent_start(state["channel_id"], "router")

        state["needs_voice"] = bool(state.get("audio_base64"))
        state["needs_vision"] = bool(state.get("image_base64"))
        state["needs_web_search"] = False
        state["agents_used"] = ["router"]

        skip_rag, rag_chunks = self._classify_message(state.get("original_message", ""))
        state["skip_rag"] = skip_rag
        state["rag_chunks"] = rag_chunks

        logger.info(
            "router_decision",
            message=state.get("original_message", "")[:50],
            skip_rag=skip_rag,
            rag_chunks=rag_chunks,
        )

        await sse_manager.agent_end(state["channel_id"], "router")
        return state

    async def _voice_node(self, state: AgentState) -> AgentState:
        await sse_manager.agent_start(state["channel_id"], "voice")
        state["agents_used"].append("voice")

        try:
            import base64
            from groq import Groq

            client = Groq(api_key=settings.groq_api_key)
            audio_bytes = base64.b64decode(state["audio_base64"])
            transcription = client.audio.transcriptions.create(
                file=("audio.webm", audio_bytes, "audio/webm"),
                model="whisper-large-v3",
                language="es",
            )
            state["transcribed_text"] = transcription.text

            # Re-classify with actual transcribed text
            skip_rag, rag_chunks = self._classify_message(transcription.text)
            state["skip_rag"] = skip_rag
            state["rag_chunks"] = rag_chunks

            logger.info("voice_transcribed", text=transcription.text[:100])
        except Exception as e:
            logger.error("voice_node_error", error=str(e))
            state["transcribed_text"] = state["original_message"]

        await sse_manager.agent_end(state["channel_id"], "voice")
        return state

    async def _vision_node(self, state: AgentState) -> AgentState:
        await sse_manager.agent_start(state["channel_id"], "vision")
        state["agents_used"].append("vision")

        try:
            import base64

            text_input = state.get("transcribed_text") or state["original_message"]
            prompt = f"""You are an expert SRE analyzing a screenshot or image from an operations context.

User message: {text_input}

Analyze this image and extract:
1. Type of content (Datadog alert, error log, dashboard, architecture diagram, etc.)
2. Key technical details (service names, error messages, metrics, values)
3. Apparent severity/urgency
4. Most relevant info for troubleshooting

Be concise and technical. Max 150 words."""

            image_bytes = base64.b64decode(state["image_base64"])
            state["vision_analysis"] = await self._gemini_generate(prompt, image_bytes)

            # Images always need RAG context
            state["skip_rag"] = False
            state["rag_chunks"] = 3

            logger.info("vision_analyzed", length=len(state["vision_analysis"]))
        except Exception as e:
            logger.error("vision_node_error", error=str(e))
            state["vision_analysis"] = "Could not analyze image."

        await sse_manager.agent_end(state["channel_id"], "vision")
        return state

    async def _rag_node(self, state: AgentState) -> AgentState:
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

            # Trigger web search only if KB has poor results
            if not results or (results and results[0]["relevance_score"] < 70):
                state["needs_web_search"] = True

            if results:
                await sse_manager.rag_sources(state["channel_id"], [
                    {
                        "filename": r["filename"],
                        "relevance_score": r["relevance_score"],
                        "category": r["category"],
                        "page": r.get("page"),
                        "source_url": r.get("source_url"),
                    }
                    for r in results
                ])

            if similar:
                await sse_manager.similar_incidents(state["channel_id"], similar)

        except Exception as e:
            logger.error("rag_node_error", error=str(e))
            state["rag_results"] = []
            state["similar_incidents"] = []

        await sse_manager.agent_end(state["channel_id"], "rag")
        return state

    async def _search_node(self, state: AgentState) -> AgentState:
        await sse_manager.agent_start(state["channel_id"], "search")
        state["agents_used"].append("search")

        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=settings.tavily_api_key)
            query = state.get("transcribed_text") or state["original_message"]
            query = query[:400].strip()  # Tavily max 400 chars
            prefix = "SRE operations "
            truncated_query = query[:400 - len(prefix)].strip()
            response = client.search(
                query=f"{prefix}{truncated_query}",
                search_depth="basic",
                max_results=3,
            )
            state["web_results"] = "\n\n".join([
                f"**{r['title']}** ({r['url']})\n{r['content']}"
                for r in response.get("results", [])
            ])
        except Exception as e:
            logger.error("search_node_error", error=str(e))
            state["web_results"] = ""

        await sse_manager.agent_end(state["channel_id"], "search")
        return state

    async def _synthesis_node(self, state: AgentState) -> AgentState:
        await sse_manager.agent_start(state["channel_id"], "synthesis")
        state["agents_used"].append("synthesis")

        try:
            context_parts = []

            if state.get("vision_analysis"):
                context_parts.append(f"## Image Analysis\n{state['vision_analysis']}")

            if state.get("rag_results"):
                kb_context = "\n\n".join([
                    f"**[{r['filename']} — {r['relevance_score']}% relevance]**\n{r['content']}"
                    for r in state["rag_results"][:3]
                ])
                context_parts.append(f"## Knowledge Base\n{kb_context}")

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
Never guess critical values."""
            else:
                system = """You are ARIA, an expert AI assistant for Operations teams.
Answer conversationally and helpfully. Be brief and friendly."""

            full_prompt = f"""{system}

{chr(10).join(context_parts)}

## User Query
{user_query}"""

            full_response = await self._gemini_stream(full_prompt, state["channel_id"])
            state["final_response"] = full_response
            await sse_manager.done(state["channel_id"], full_response)

        except Exception as e:
            logger.error("synthesis_node_error", error=str(e))
            await sse_manager.error(state["channel_id"], f"Error: {str(e)}")
            state["error"] = str(e)

        await sse_manager.agent_end(state["channel_id"], "synthesis")
        return state

    # ─── Public Interface ──────────────────────────────────────────────────────

    async def run(
        self,
        channel_id: str,
        conversation_id: UUID,
        message: str,
        image_base64: Optional[str] = None,
        audio_base64: Optional[str] = None,
    ) -> str:
        initial_state = AgentState(
            channel_id=channel_id,
            conversation_id=str(conversation_id),
            incident_id=None,
            original_message=message,
            image_base64=image_base64,
            audio_base64=audio_base64,
            transcribed_text=None,
            vision_analysis=None,
            rag_results=None,
            web_results=None,
            similar_incidents=None,
            needs_vision=False,
            needs_voice=False,
            needs_web_search=False,
            skip_rag=False,
            rag_chunks=3,
            agents_used=[],
            final_response=None,
            error=None,
        )
        final_state = await self.graph.ainvoke(initial_state)
        return final_state.get("final_response", "")

    async def analyze_incident(
        self,
        channel_id: str,
        incident_id: str,
        payload: dict,
    ) -> str:
        """Analyze a Datadog alert — always uses full RAG."""
        title = payload.get("title", "")
        text = payload.get("text", "")
        metrics = payload.get("metrics", {})
        service = next(
            (t.split("service:")[1] for t in payload.get("tags", []) if t.startswith("service:")),
            "unknown"
        )
        message = f"""Datadog Alert Received:
Title: {title}
Description: {text}
Service: {service}
Metrics: {json.dumps(metrics, indent=2)}

Please analyze this alert and provide:
1. Likely root cause
2. Immediate actions to take
3. Relevant runbooks or past incidents"""

        return await self.run(
            channel_id=channel_id,
            conversation_id=incident_id,
            message=message,
        )


# Singleton
orchestrator = ARIAOrchestrator()