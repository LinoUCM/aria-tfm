"""
ARIA Orchestrator — LangGraph multi-agent pipeline.

Graph flow:
  input → router → [vision?, voice?] → rag → [search?] → synthesis → output
"""

import json
import asyncio
from typing import TypedDict, Optional, List
from uuid import UUID

from langgraph.graph import StateGraph, END
from google import genai
from google.genai import types
import structlog

from services.sse_manager import sse_manager
from core.config import settings

logger = structlog.get_logger()


# ─── Agent State ─────────────────────────────────────────────────────────────

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
    agents_used: List[str]
    final_response: Optional[str]
    error: Optional[str]


# ─── Orchestrator ─────────────────────────────────────────────────────────────

class ARIAOrchestrator:

    def __init__(self):
        self.gemini = genai.Client(api_key=settings.google_api_key)
        self.graph = self._build_graph()

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
            {"voice": "voice", "vision": "vision", "rag": "rag"}
        )
        workflow.add_conditional_edges(
            "voice",
            lambda s: "vision" if s["needs_vision"] else "rag",
            {"vision": "vision", "rag": "rag"}
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

    def _route_after_router(self, state: AgentState) -> str:
        if state["needs_voice"] and state["audio_base64"]:
            return "voice"
        elif state["needs_vision"] and state["image_base64"]:
            return "vision"
        return "rag"

    # ─── Nodes ───────────────────────────────────────────────────────────────

    async def _router_node(self, state: AgentState) -> AgentState:
        await sse_manager.agent_start(state["channel_id"], "router")
        state["needs_voice"] = bool(state.get("audio_base64"))
        state["needs_vision"] = bool(state.get("image_base64"))
        state["needs_web_search"] = False
        state["agents_used"] = ["router"]
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
2. Key technical details visible (service names, error messages, metrics, values)
3. Apparent severity/urgency
4. What specific information is most relevant for troubleshooting

Be concise and technical."""

            image_bytes = base64.b64decode(state["image_base64"])
            response = self.gemini.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    prompt,
                    types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                ],
            )
            state["vision_analysis"] = response.text
            logger.info("vision_analyzed", result_length=len(response.text))
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

            results = get_indexing_service().search(query=query, n_results=5)
            state["rag_results"] = results

            similar = await memory_service.find_similar_incidents(query=query)
            state["similar_incidents"] = similar

            if not results or (results and results[0]["relevance_score"] < 75):
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
            response = client.search(
                query=f"SRE operations {query}",
                search_depth="basic",
                max_results=3,
            )
            results_text = "\n\n".join([
                f"**{r['title']}** ({r['url']})\n{r['content']}"
                for r in response.get("results", [])
            ])
            state["web_results"] = results_text
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

            full_prompt = f"""You are ARIA, an expert AI assistant for Operations teams.
You help engineers diagnose and resolve incidents quickly.
Be concise, technical, and actionable. Structure your response with:
1. Quick diagnosis
2. Recommended steps (numbered)
3. Relevant context from KB (cite sources)
If you don't know something, say so clearly. Never guess critical values.

{chr(10).join(context_parts)}

## User Query
{user_query}

Provide a structured, actionable response for the operations engineer."""

           # 1. Primero ejecutamos la llamada con 'await' para obtener el generador (stream)
            response_stream = await self.gemini.aio.models.generate_content_stream(
                model="gemini-2.0-flash", # Nota: Asegúrate de que el nombre del modelo es correcto
                contents=full_prompt,
            )

            # 2. Ahora sí recorremos el stream de forma asíncrona
            async for chunk in response_stream:
                if chunk.text:
                    await sse_manager.token(state["channel_id"], chunk.text)
                    full_response += chunk.text

            state["final_response"] = full_response
            await sse_manager.done(state["channel_id"], full_response)

        except Exception as e:
            logger.error("synthesis_node_error", error=str(e))
            await sse_manager.error(state["channel_id"], f"Error generating response: {str(e)}")
            state["error"] = str(e)

        await sse_manager.agent_end(state["channel_id"], "synthesis")
        return state

    # ─── Public Interface ─────────────────────────────────────────────────────

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
