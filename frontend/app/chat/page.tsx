"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  sendMessage, streamChat, fetchConversation, listConversations,
  listConversationCitations, fetchDocumentContent, openDocumentInNewTab,
  ConversationSummary, ConversationDetail,
} from "@/lib/api";
import { formatDistanceToNow } from "date-fns";
import toast from "react-hot-toast";
import {
  Send, Mic, Square, Paperclip, Bot, User,
  Loader2, AlertCircle, BookOpen, History, MessageSquare, Globe, FileText, ExternalLink, X
} from "lucide-react";
import { clsx } from "clsx";
import DocumentViewerModal from "@/components/DocumentViewerModal";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  image?: string; // data URL de la imagen adjunta — solo en la sesión actual, no se persiste
  agents_used?: string[];
  rag_sources?: RagSource[];
  web_sources?: WebSource[];
  similar_incidents?: SimilarIncident[];
  timestamp: Date;
}

interface WebSource {
  title: string;
  url: string;
}

interface RagSource {
  filename: string;
  relevance_score: number;
  category?: string;
  page?: number;
  source_url?: string;
  doc_id?: string;        // en vivo: del evento SSE (backend _rag_node). Histórico: document_id de /citations
  file_type?: string;     // solo histórico (/citations)
  chunk_content?: string; // solo histórico (/citations) — el pasaje exacto que usó el LLM
  chunk_index?: number;   // solo histórico (/citations)
}

interface SimilarIncident {
  id: string;
  title: string;
  resolution?: string;
  resolution_time_minutes?: number;
  created_at: string;
}

const AGENT_LABELS: Record<string, string> = {
  router: "🔀 Routing",
  voice: "🎤 Transcribing",
  vision: "👁️ Analyzing image",
  rag: "📚 Searching KB",
  search: "🌐 Web search",
  synthesis: "✍️ Generating",
};

const WELCOME_MESSAGE: Message = {
  id: "welcome",
  role: "assistant",
  content:
    "Hi, I'm **ARIA** — your Operations Intelligence assistant.\n\nI can help you:\n- Diagnose incidents and alerts\n- Search your knowledge base\n- Analyze screenshots and logs\n- Find similar past incidents\n\nDescribe your issue or paste an error message to get started.",
  timestamp: new Date(),
};

// Mapea los mensajes almacenados de una conversación al tipo Message del chat.
// Reutilizado por la hidratación inicial y por la selección desde el historial.
function mapConversationMessages(conv: ConversationDetail): Message[] {
  return conv.messages.map((m, i) => ({
    id: `${conv.id}-${i}`,
    role: m.role,
    content: m.content,
    timestamp: new Date(m.timestamp),
  }));
}

// El backend serializa updated_at con datetime.utcnow().isoformat(), sin sufijo
// de zona horaria; lo normalizamos a UTC para que el navegador no lo interprete
// como hora local (mismo criterio que admin/page.tsx).
function formatRelative(iso: string | null): string {
  if (!iso) return "";
  const hasTimezone = /Z$|[+-]\d{2}:\d{2}$/.test(iso);
  return formatDistanceToNow(new Date(hasTimezone ? iso : `${iso}Z`), { addSuffix: true });
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([WELCOME_MESSAGE]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [activeAgents, setActiveAgents] = useState<string[]>([]);
  const [conversationId, setConversationId] = useState<string>();
  const [imageBase64, setImageBase64] = useState<string>();
  const [imagePreview, setImagePreview] = useState<string>();
  const [audioBase64, setAudioBase64] = useState<string | undefined>(undefined);
  const [isRecording, setIsRecording] = useState(false);

  const router = useRouter();
  const searchParams = useSearchParams();
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);

  // Citas RAG persistidas sin message_index (filas previas a esa columna): se
  // muestran en un bloque único al final, no colgando de ninguna burbuja.
  const [legacyCitations, setLegacyCitations] = useState<RagSource[]>([]);
  // Fuente sobre la que se ha hecho clic → ficha de cita (filename, relevancia,
  // chunk_content). Desde ahí se abre el documento completo.
  const [activeCitation, setActiveCitation] = useState<RagSource | null>(null);
  // Documento .md/.txt renderizado en el visor reutilizado de la pantalla admin.
  const [viewerDoc, setViewerDoc] = useState<{ title: string; content: string } | null>(null);
  const [openingDoc, setOpeningDoc] = useState(false);

  // Descarga las filas rag_references de la conversación y las reparte por
  // message_index: cada burbuja de asistente (posición i del array de mensajes,
  // que tras mapConversationMessages coincide con el índice de backend) recibe
  // sus fuentes; las de message_index null se acumulan en legacyCitations.
  const attachCitations = async (id: string, msgs: Message[]): Promise<Message[]> => {
    try {
      const cites = await listConversationCitations(id);
      const byIdx = new Map<number, RagSource[]>();
      const legacy: RagSource[] = [];
      for (const c of cites) {
        const src: RagSource = {
          filename: c.filename,
          relevance_score: c.relevance_score ?? 0,
          doc_id: c.document_id,
          file_type: c.file_type ?? undefined,
          chunk_content: c.chunk_content ?? undefined,
          chunk_index: c.chunk_index ?? undefined,
        };
        if (c.message_index == null) { legacy.push(src); continue; }
        const arr = byIdx.get(c.message_index) ?? [];
        arr.push(src);
        byIdx.set(c.message_index, arr);
      }
      setLegacyCitations(legacy);
      return msgs.map((m, i) =>
        m.role === "assistant" && byIdx.has(i) ? { ...m, rag_sources: byIdx.get(i) } : m
      );
    } catch (err) {
      console.error("No se pudieron cargar las citas de la conversación:", err);
      setLegacyCitations([]);
      return msgs;
    }
  };

  // Abre el documento de una cita: .md/.txt en el visor markdown reutilizado;
  // cualquier otro tipo (PDF incluido) vía fetch autenticado + blob en pestaña
  // nueva (evita el 401 de window.open sin cabecera Authorization).
  const openCitationDocument = async (src: RagSource) => {
    if (!src.doc_id) return;
    const ext = (src.file_type || src.filename.split(".").pop() || "").toLowerCase();
    setOpeningDoc(true);
    try {
      if (ext === "md" || ext === "markdown" || ext === "txt") {
        const result = await fetchDocumentContent(src.doc_id);
        if (result.isJson) {
          setViewerDoc({ title: result.filename || src.filename, content: result.content || "" });
          setActiveCitation(null);
        } else {
          await openDocumentInNewTab(src.doc_id);
        }
      } else {
        await openDocumentInNewTab(src.doc_id);
      }
    } catch (err: any) {
      toast.error(err.message || "No se pudo abrir el documento");
    } finally {
      setOpeningDoc(false);
    }
  };

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  // Recarga la lista de conversaciones del usuario (silencioso ante error,
  // igual que el resto de fetches de este archivo). Devuelve la lista para
  // que la hidratación inicial pueda usarla sin una segunda petición.
  const refreshConversations = async (): Promise<ConversationSummary[]> => {
    try {
      const list = await listConversations();
      setConversations(list);
      return list;
    } catch (err) {
      console.error("No se pudo cargar el historial de conversaciones:", err);
      return [];
    }
  };

  const handleSelectConversation = async (id: string) => {
    setIsLoadingHistory(true);
    setLegacyCitations([]);
    try {
      const conv = await fetchConversation(id);
      if (conv.messages.length > 0) {
        setMessages(await attachCitations(id, mapConversationMessages(conv)));
      } else {
        setMessages([WELCOME_MESSAGE]);
      }
      setConversationId(id);
      router.replace(`/chat?c=${id}`, { scroll: false });
      setIsHistoryOpen(false);
    } catch (err) {
      console.error("No se pudo cargar la conversación seleccionada:", err);
    } finally {
      setIsLoadingHistory(false);
    }
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, activeAgents]);

  useEffect(() => {
    async function hydrate() {
      const urlConversationId = searchParams.get("c");
      let conversationIdToLoad = urlConversationId;

      // Puebla el estado `conversations` para el panel de historial, y de paso
      // nos da la lista para elegir la más reciente si no hay id en la URL.
      const list = await refreshConversations();
      if (!conversationIdToLoad && list.length > 0) {
        conversationIdToLoad = list[0].id;
      }

      if (!conversationIdToLoad) return;

      setIsLoadingHistory(true);
      try {
        const conv = await fetchConversation(conversationIdToLoad);
        const hydrated = mapConversationMessages(conv);
        if (hydrated.length > 0) {
          setMessages(await attachCitations(conv.id, hydrated));
        }
        setConversationId(conv.id);
        if (!urlConversationId) {
          router.replace(`/chat?c=${conv.id}`, { scroll: false });
        }
      } catch (err) {
        console.error("No se pudo cargar el historial de la conversación:", err);
      } finally {
        setIsLoadingHistory(false);
      }
    }

    hydrate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

 const handleSend = async (audioBase64Override?: string) => {
  const audio = audioBase64Override ?? audioBase64;
  if (!input.trim() && !imageBase64 && !audio) return;
  if (isLoading) return;

  // En un mensaje de voz aún no hay texto: mostramos un marcador que luego
  // se reemplaza con la transcripción real vía el evento SSE "transcription".
  const text = audioBase64Override ? "🎤 Voice message" : input;

  const userMessage: Message = {
    id: Date.now().toString(),
    role: "user",
    content: text,
    // Capturamos el preview ANTES de que se limpie el estado tras enviar.
    image: imagePreview,
    timestamp: new Date(),
  };

  setMessages((prev) => [...prev, userMessage]);
  setInput("");
  setIsLoading(true);
  setActiveAgents([]);

  const assistantId = (Date.now() + 1).toString();
  setMessages((prev) => [
    ...prev,
    { id: assistantId, role: "assistant", content: "", timestamp: new Date() },
  ]);

  // Buffer ref para acumular tokens sin depender del estado
  const contentBuffer = { current: "" };
  const ragSourcesBuffer = { current: [] as RagSource[] };
  const webSourcesBuffer = { current: [] as WebSource[] };
  const similarBuffer = { current: [] as SimilarIncident[] };

  try {
    const { channel_id, conversation_id } = await sendMessage(
      text,
      conversationId,
      imageBase64,
      audio
    );

    const isNewConversation = !conversationId;
    setConversationId(conversation_id);
    setImageBase64(undefined);
    setImagePreview(undefined);
    setAudioBase64(undefined);

    if (isNewConversation) {
      router.replace(`/chat?c=${conversation_id}`, { scroll: false });
    }

    const eventSource = new EventSource(
      `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/chat/stream/${channel_id}`
    );

    eventSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);

        switch (data.event) {
          case "agent_start":
            setActiveAgents((prev) =>
              prev.includes(data.data.agent) ? prev : [...prev, data.data.agent]
            );
            break;

          case "agent_end":
            setActiveAgents((prev) =>
              prev.filter((a) => a !== data.data.agent)
            );
            break;

          case "transcription":
            setMessages((prev) =>
              prev.map((m) =>
                m.id === userMessage.id
                  ? { ...m, content: data.data.text }
                  : m
              )
            );
            break;

          case "token":
            contentBuffer.current += data.data.token;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: contentBuffer.current }
                  : m
              )
            );
            break;

          case "rag_sources":
            ragSourcesBuffer.current = data.data.sources;
            break;

          case "web_results":
            webSourcesBuffer.current = data.data.sources;
            break;

          case "similar_incidents":
            similarBuffer.current = data.data.incidents;
            break;

          case "done":
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      content: contentBuffer.current,
                      rag_sources: ragSourcesBuffer.current,
                      web_sources: webSourcesBuffer.current,
                      similar_incidents: similarBuffer.current,
                    }
                  : m
              )
            );
            setIsLoading(false);
            setActiveAgents([]);
            eventSource.close();
            // El backend fija/actualiza el título y updated_at de forma
            // asíncrona tras el intercambio; refrescamos para reflejarlo.
            refreshConversations();
            break;

          case "error":
            toast.error(data.data.message || "Error from ARIA");
            setIsLoading(false);
            setActiveAgents([]);
            eventSource.close();
            break;

          case "ping":
            // keepalive, ignorar
            break;
        }
      } catch (parseError) {
        // Ignorar eventos mal formados
        console.warn("SSE parse error:", parseError);
      }
    };

    eventSource.onerror = (err) => {
      console.error("SSE error:", err);
      eventSource.close();
      setIsLoading(false);
      setActiveAgents([]);
      if (!contentBuffer.current) {
        toast.error("Connection error — please try again");
      }
    };

  } catch (err) {
    toast.error("Failed to send message");
    setIsLoading(false);
    setActiveAgents([]);
    // Eliminar el mensaje vacío del assistant
    setMessages((prev) => prev.filter((m) => m.id !== assistantId));
  }
};

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      setImagePreview(result);
      setImageBase64(result.split(",")[1]);
    };
    reader.readAsDataURL(file);
  };

  const handleVoice = async () => {
    if (isRecording) {
      mediaRecorderRef.current?.stop();
      setIsRecording(false);
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        audioChunksRef.current.push(e.data);
      };

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        const reader = new FileReader();
        reader.onload = () => {
          const base64 = (reader.result as string).split(",")[1];
          setInput("🎤 Voice message");
          setAudioBase64(base64);
          // Disparamos el envío desde aquí con el base64 local (el estado
          // audioBase64 aún no está actualizado por ser asíncrono).
          handleSend(base64);
        };
        reader.readAsDataURL(audioBlob);
        stream.getTracks().forEach((t) => t.stop());
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch {
      toast.error("Microphone access denied");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-screen bg-gray-950">
      {/* Header */}
      <div className="border-b border-gray-800 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
            <Bot className="w-4 h-4 text-white" />
          </div>
          <div>
            <h1 className="text-white font-semibold">ARIA Chat</h1>
            <p className="text-gray-400 text-xs">
              {conversationId ? `Session: ${conversationId.slice(0, 8)}...` : "New session"}
            </p>
          </div>
        </div>
        <div className="relative flex items-center gap-3">
          <button
            onClick={() => setIsHistoryOpen((v) => !v)}
            className={clsx(
              "text-xs flex items-center gap-1 transition-colors",
              isHistoryOpen ? "text-blue-400" : "text-gray-500 hover:text-gray-300"
            )}
            title="Conversation history"
          >
            <MessageSquare className="w-3 h-3" /> History
          </button>

          <button
            onClick={() => {
              setMessages([WELCOME_MESSAGE]);
              setConversationId(undefined);
              setLegacyCitations([]);
              setIsHistoryOpen(false);
              router.replace("/chat", { scroll: false });
            }}
            className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1"
          >
            <History className="w-3 h-3" /> New chat
          </button>

          {isHistoryOpen && (
            <div className="absolute right-0 top-full mt-3 w-80 max-h-96 overflow-y-auto bg-gray-900 border border-gray-800 rounded-xl shadow-2xl z-50 py-1">
              {conversations.length === 0 ? (
                <div className="px-4 py-3 text-xs text-gray-500">
                  No conversations yet.
                </div>
              ) : (
                conversations.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => handleSelectConversation(c.id)}
                    className={clsx(
                      "w-full text-left px-4 py-2.5 flex flex-col gap-0.5 hover:bg-gray-800 transition-colors",
                      c.id === conversationId && "bg-blue-600/10 border-l-2 border-blue-500"
                    )}
                  >
                    <span className="text-sm text-gray-200 truncate">
                      {c.title || "New conversation"}
                    </span>
                    <span className="text-xs text-gray-500">
                      {formatRelative(c.updated_at)}
                    </span>
                  </button>
                ))
              )}
            </div>
          )}
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {isLoadingHistory ? (
          <div className="h-full flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-blue-400" />
          </div>
        ) : (
          <>
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} onCiteClick={setActiveCitation} />
            ))}

            {/* Fuentes de mensajes anteriores a la trazabilidad por mensaje
                (filas rag_references con message_index null). No cuelgan de
                ninguna burbuja: bloque único al final de la conversación. */}
            {legacyCitations.length > 0 && (
              <div className="ml-11 max-w-[75%] bg-gray-900/50 border border-gray-800 rounded-lg p-3 space-y-1.5">
                <div className="flex items-center gap-1.5 text-xs text-gray-400 font-medium">
                  <BookOpen className="w-3 h-3" /> Fuentes de mensajes anteriores a esta actualización
                </div>
                {legacyCitations.map((source, i) => (
                  <button
                    key={i}
                    onClick={() => setActiveCitation(source)}
                    className="w-full flex items-center justify-between text-xs group"
                  >
                    <span className="text-gray-300 group-hover:text-blue-400 truncate max-w-[220px] text-left" title={source.filename}>
                      {source.filename}
                    </span>
                    <span className="ml-2 px-1.5 py-0.5 rounded text-xs font-mono bg-gray-800 text-gray-400">
                      {source.relevance_score}%
                    </span>
                  </button>
                ))}
              </div>
            )}

            {/* Active agents indicator */}
            {activeAgents.length > 0 && (
              <div className="flex items-center gap-2 text-sm text-gray-400">
                <Loader2 className="w-4 h-4 animate-spin text-blue-400" />
                <span>{AGENT_LABELS[activeAgents[activeAgents.length - 1]] || "Processing..."}</span>
              </div>
            )}
          </>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Image preview */}
      {imagePreview && (
        <div className="px-6 py-2 border-t border-gray-800">
          <div className="relative inline-block">
            <img src={imagePreview} alt="Attached" className="h-16 rounded-lg border border-gray-700" />
            <button
              onClick={() => { setImageBase64(undefined); setImagePreview(undefined); }}
              className="absolute -top-2 -right-2 w-5 h-5 bg-red-500 rounded-full text-white text-xs flex items-center justify-center"
            >×</button>
          </div>
        </div>
      )}

      {/* Input */}
      <div className="border-t border-gray-800 p-4">
        <div className="flex items-end gap-3 bg-gray-900 rounded-xl border border-gray-700 p-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Describe the incident, paste an error, or ask a question..."
            rows={1}
            className="flex-1 bg-transparent text-gray-100 placeholder-gray-500 resize-none outline-none text-sm max-h-32"
            style={{ minHeight: "24px" }}
          />
          <div className="flex items-center gap-2">
            <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleImageUpload} />
            <button
              onClick={() => fileInputRef.current?.click()}
              className="p-1.5 text-gray-500 hover:text-gray-300 transition-colors"
              title="Attach image"
            >
              <Paperclip className="w-4 h-4" />
            </button>
            <button
              onClick={handleVoice}
              className={clsx("p-1.5 transition-colors", isRecording ? "text-red-400 animate-pulse" : "text-gray-500 hover:text-gray-300")}
              title="Voice input"
            >
              {isRecording ? <Square className="w-4 h-4 fill-current" /> : <Mic className="w-4 h-4" />}
            </button>
            <button
              onClick={() => handleSend()}
              disabled={isLoading || (!input.trim() && !imageBase64)}
              className="p-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors"
            >
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            </button>
          </div>
        </div>
        <p className={clsx(
          "text-xs mt-2 text-center transition-colors",
          isRecording ? "text-red-400 font-medium animate-pulse" : "text-gray-600"
        )}>
          {isRecording
            ? "🔴 Listening... tap the mic again to stop"
            : "Enter to send · Shift+Enter for new line · Attach images for visual analysis"}
        </p>
      </div>

      {/* Ficha de cita: filename + relevancia + el chunk_content exacto que usó
          el LLM (solo disponible en el histórico recargado), con acción para
          abrir el documento completo. */}
      {activeCitation && (
        <CitationCard
          source={activeCitation}
          opening={openingDoc}
          onClose={() => setActiveCitation(null)}
          onOpenFull={() => openCitationDocument(activeCitation)}
        />
      )}

      <DocumentViewerModal
        isOpen={viewerDoc !== null}
        onClose={() => setViewerDoc(null)}
        title={viewerDoc?.title || ""}
        content={viewerDoc?.content || ""}
      />
    </div>
  );
}

function CitationCard({
  source,
  opening,
  onClose,
  onOpenFull,
}: {
  source: RagSource;
  opening: boolean;
  onClose: () => void;
  onOpenFull: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg bg-gray-900 border border-gray-800 rounded-xl p-5 shadow-2xl space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-gray-800 pb-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-white font-semibold text-sm">
              <FileText className="w-4 h-4 flex-shrink-0 text-blue-400" />
              <span className="truncate" title={source.filename}>{source.filename}</span>
            </div>
            <div className="text-xs text-gray-500 mt-1 font-mono">
              {source.relevance_score}% relevance
              {typeof source.chunk_index === "number" ? ` · chunk #${source.chunk_index}` : ""}
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white p-1 flex-shrink-0">
            <X className="w-4 h-4" />
          </button>
        </div>

        {source.chunk_content ? (
          <div className="max-h-[45vh] overflow-y-auto rounded-lg bg-gray-950 border border-gray-800 p-3 text-xs text-gray-300 whitespace-pre-wrap leading-relaxed">
            {source.chunk_content}
          </div>
        ) : (
          <p className="text-xs text-gray-500 italic">
            El fragmento exacto no está disponible para este mensaje en vivo; recarga la
            conversación para verlo. Puedes abrir el documento completo igualmente.
          </p>
        )}

        <button
          onClick={onOpenFull}
          disabled={opening || !source.doc_id}
          className="w-full flex items-center justify-center gap-2 text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg py-2 transition-colors"
        >
          {opening ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ExternalLink className="w-3.5 h-3.5" />}
          Abrir documento completo
        </button>
      </div>
    </div>
  );
}

function MessageBubble({ message, onCiteClick }: { message: Message; onCiteClick: (s: RagSource) => void }) {
  const isUser = message.role === "user";

  return (
    <div className={clsx("flex gap-3", isUser ? "flex-row-reverse" : "flex-row")}>
      <div className={clsx(
        "w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 mt-1",
        isUser ? "bg-gray-700" : "bg-blue-600"
      )}>
        {isUser ? <User className="w-4 h-4 text-gray-300" /> : <Bot className="w-4 h-4 text-white" />}
      </div>

      <div className={clsx("max-w-[75%] space-y-2", isUser ? "items-end" : "items-start")}>
        <div className={clsx(
          "rounded-xl px-4 py-3 text-sm leading-relaxed",
          isUser ? "bg-blue-600 text-white" : "bg-gray-900 text-gray-100 border border-gray-800"
        )}>
          {message.image && (
            <img
              src={message.image}
              alt="Attached"
              className={clsx(
                "rounded-lg max-h-48 w-auto border border-gray-700",
                message.content && "mb-2"
              )}
            />
          )}
          {message.content ? (
            <MarkdownContent content={message.content} />
          ) : !isUser ? (
            <div className="flex gap-1">
              <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
              <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
              <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
          ) : null}
        </div>

        {/* RAG Sources */}
        {message.rag_sources && message.rag_sources.length > 0 && (
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-3 space-y-1.5">
            <div className="flex items-center gap-1.5 text-xs text-gray-400 font-medium">
              <BookOpen className="w-3 h-3" /> Sources consulted
            </div>
            {message.rag_sources.map((source, i) => (
              <div key={i} className="flex items-center justify-between text-xs">
                <span className="truncate max-w-[200px]" title={source.filename}>
                  {source.doc_id ? (
                    <button
                      onClick={() => onCiteClick(source)}
                      className="text-gray-300 hover:text-blue-400 underline decoration-dotted underline-offset-2 text-left"
                    >
                      {source.filename}
                    </button>
                  ) : source.source_url ? (
                    <a href={source.source_url} target="_blank" rel="noopener noreferrer" className="text-gray-300 hover:text-blue-400">
                      {source.filename}
                    </a>
                  ) : (
                    <span className="text-gray-300">{source.filename}</span>
                  )}
                </span>
                <span className={clsx(
                  "ml-2 px-1.5 py-0.5 rounded text-xs font-mono",
                  source.relevance_score >= 90 ? "bg-green-900/50 text-green-400" :
                  source.relevance_score >= 75 ? "bg-yellow-900/50 text-yellow-400" :
                  "bg-gray-800 text-gray-400"
                )}>
                  {source.relevance_score}%
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Web Sources */}
        {message.web_sources && message.web_sources.length > 0 && (
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-3 space-y-1.5">
            <div className="flex items-center gap-1.5 text-xs text-gray-400 font-medium">
              <Globe className="w-3 h-3" /> Web sources
            </div>
            {message.web_sources.map((source, i) => (
              <div key={i} className="flex items-center text-xs">
                <a
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-gray-300 hover:text-blue-400 truncate"
                  title={source.url}
                >
                  {source.title}
                </a>
              </div>
            ))}
          </div>
        )}

        {/* Similar Incidents */}
        {message.similar_incidents && message.similar_incidents.length > 0 && (
          <div className="bg-orange-950/20 border border-orange-900/30 rounded-lg p-3 space-y-1.5">
            <div className="flex items-center gap-1.5 text-xs text-orange-400 font-medium">
              <AlertCircle className="w-3 h-3" /> Similar past incidents
            </div>
            {message.similar_incidents.map((inc) => (
              <div key={inc.id} className="text-xs text-gray-300">
                <div className="font-medium truncate">{inc.title}</div>
                {inc.resolution_time_minutes && (
                  <div className="text-gray-500">Resolved in {inc.resolution_time_minutes} min</div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MarkdownContent({ content }: { content: string }) {
  // Simple markdown renderer
  const lines = content.split("\n");
  return (
    <div className="space-y-1">
      {lines.map((line, i) => {
        if (line.startsWith("## ")) return <h2 key={i} className="font-bold text-base mt-2">{line.slice(3)}</h2>;
        if (line.startsWith("# ")) return <h1 key={i} className="font-bold text-lg mt-2">{line.slice(2)}</h1>;
        if (line.startsWith("**") && line.endsWith("**")) return <p key={i} className="font-semibold">{line.slice(2, -2)}</p>;
        if (line.startsWith("- ") || line.startsWith("* ")) return <p key={i} className="pl-3 before:content-['•'] before:mr-2 before:text-blue-400">{line.slice(2)}</p>;
        if (/^\d+\./.test(line)) return <p key={i} className="pl-3">{line}</p>;
        if (line === "") return <br key={i} />;
        // Inline bold
        const parts = line.split(/\*\*(.*?)\*\*/g);
        return (
          <p key={i}>
            {parts.map((part, j) => j % 2 === 1 ? <strong key={j}>{part}</strong> : part)}
          </p>
        );
      })}
    </div>
  );
}
