"use client";

import { useState, useRef, useEffect } from "react";
import { sendMessage, streamChat } from "@/lib/api";
import toast from "react-hot-toast";
import {
  Send, Mic, Square, Paperclip, Bot, User,
  Loader2, AlertCircle, BookOpen, History
} from "lucide-react";
import { clsx } from "clsx";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  agents_used?: string[];
  rag_sources?: RagSource[];
  similar_incidents?: SimilarIncident[];
  timestamp: Date;
}

interface RagSource {
  filename: string;
  relevance_score: number;
  category: string;
  page?: number;
  source_url?: string;
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

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      content: "Hi, I'm **ARIA** — your Operations Intelligence assistant.\n\nI can help you:\n- Diagnose incidents and alerts\n- Search your knowledge base\n- Analyze screenshots and logs\n- Find similar past incidents\n\nDescribe your issue or paste an error message to get started.",
      timestamp: new Date(),
    },
  ]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [activeAgents, setActiveAgents] = useState<string[]>([]);
  const [conversationId, setConversationId] = useState<string>();
  const [imageBase64, setImageBase64] = useState<string>();
  const [imagePreview, setImagePreview] = useState<string>();
  const [audioBase64, setAudioBase64] = useState<string | undefined>(undefined);
  const [isRecording, setIsRecording] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, activeAgents]);

 const handleSend = async (audioBase64Override?: string) => {
  const audio = audioBase64Override ?? audioBase64;
  if (!input.trim() && !imageBase64 && !audio) return;
  if (isLoading) return;

  // En un mensaje de voz aún no hay texto: mostramos un marcador que luego
  // se reemplaza con la transcripción real vía el evento SSE "transcription".
  const text = audioBase64Override ? "🎤 Mensaje de voz" : input;

  const userMessage: Message = {
    id: Date.now().toString(),
    role: "user",
    content: text,
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
  const similarBuffer = { current: [] as SimilarIncident[] };

  try {
    const { channel_id, conversation_id } = await sendMessage(
      text,
      conversationId,
      imageBase64,
      audio
    );

    setConversationId(conversation_id);
    setImageBase64(undefined);
    setImagePreview(undefined);
    setAudioBase64(undefined);

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
                      similar_incidents: similarBuffer.current,
                    }
                  : m
              )
            );
            setIsLoading(false);
            setActiveAgents([]);
            eventSource.close();
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
          setInput("🎤 Mensaje de voz");
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
        <button
          onClick={() => { setMessages([messages[0]]); setConversationId(undefined); }}
          className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1"
        >
          <History className="w-3 h-3" /> New chat
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}

        {/* Active agents indicator */}
        {activeAgents.length > 0 && (
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <Loader2 className="w-4 h-4 animate-spin text-blue-400" />
            <span>{AGENT_LABELS[activeAgents[activeAgents.length - 1]] || "Processing..."}</span>
          </div>
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
            ? "🔴 Escuchando... pulsa el micrófono de nuevo para terminar"
            : "Enter to send · Shift+Enter for new line · Attach images for visual analysis"}
        </p>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
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
          {message.content ? (
            <MarkdownContent content={message.content} />
          ) : (
            <div className="flex gap-1">
              <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
              <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
              <div className="w-2 h-2 bg-gray-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
          )}
        </div>

        {/* RAG Sources */}
        {message.rag_sources && message.rag_sources.length > 0 && (
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-3 space-y-1.5">
            <div className="flex items-center gap-1.5 text-xs text-gray-400 font-medium">
              <BookOpen className="w-3 h-3" /> Sources consulted
            </div>
            {message.rag_sources.map((source, i) => (
              <div key={i} className="flex items-center justify-between text-xs">
                <span className="text-gray-300 truncate max-w-[200px]">
                  {source.source_url ? (
                    <a href={source.source_url} target="_blank" rel="noopener noreferrer" className="hover:text-blue-400">
                      {source.filename}
                    </a>
                  ) : source.filename}
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
