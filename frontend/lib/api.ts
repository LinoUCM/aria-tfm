// lib/api.ts — Central API client for ARIA backend
import { getAuthToken } from "./auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Helper para adjuntar el Bearer Token a las cabeceras HTTP
function getAuthHeaders(extraHeaders: Record<string, string> = {}): Record<string, string> {
  const token = getAuthToken();
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extraHeaders,
  };
}

// ─── Types ───────────────────────────────────────────────────────────────────

export interface Document {
  id: string;
  filename: string;
  title?: string;
  source: "file" | "url";
  source_url?: string;
  file_type?: string;
  category: string;
  service_tag?: string;
  status: "processing" | "indexed" | "error";
  chunks_count: number;
  file_size?: number;
  indexed_at?: string;
  created_at: string;
  error_message?: string;
}

export interface KBStats {
  total_documents: number;
  total_chunks: number;
  indexed_documents: number;
  processing_documents: number;
  categories: Record<string, number>;
  last_updated?: string;
}

export interface Incident {
  id: string;
  title: string;
  description?: string;
  source: string;
  severity: "P1" | "P2" | "P3" | "P4";
  status: "open" | "investigating" | "resolved";
  service_affected?: string;
  host?: string;
  tags: string[];
  metrics: Record<string, number>;
  analysis?: Record<string, any>;
  resolution?: string;
  resolution_time_minutes?: number;
  created_at: string;
  resolved_at?: string;
}

export interface ChatResponse {
  channel_id: string;
  conversation_id: string;
}

export interface Preset {
  key: string;
  title: string;
  priority: string;
  alert_type: string;
  service: string;
}

// ─── Chat ─────────────────────────────────────────────────────────────────────

export async function sendMessage(
  message: string,
  conversationId?: string,
  imageBase64?: string,
  audioBase64?: string
): Promise<ChatResponse> {
  const res = await fetch(`${API_URL}/chat/`, {
    method: "POST",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
      image_base64: imageBase64,
      audio_base64: audioBase64,
    }),
  });
  if (!res.ok) throw new Error("Failed to send message");
  return res.json();
}

export function streamChat(channelId: string): EventSource {
  return new EventSource(`${API_URL}/chat/stream/${channelId}`);
}

export interface StoredMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

export interface ConversationDetail {
  id: string;
  title: string | null;
  messages: StoredMessage[];
  owner_username: string | null;
  created_at: string;
  updated_at: string;
}

export async function fetchConversation(conversationId: string): Promise<ConversationDetail> {
  const res = await fetch(`${API_URL}/chat/conversations/${conversationId}`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Error ${res.status}: No se pudo cargar la conversación`);
  }
  return res.json();
}

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string | null;
  created_at: string | null;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const res = await fetch(`${API_URL}/chat/conversations`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Error ${res.status}: No se pudieron cargar las conversaciones`);
  }
  return res.json();
}

// Citas RAG estructuradas de una conversación (tabla rag_references). Se usa al
// recargar el historial, porque conversation.messages no guarda las fuentes de
// cada mensaje: el cliente agrupa estas filas por message_index para reconstruir
// el bloque de fuentes de cada burbuja del asistente. Las filas con
// message_index null (previas a esta función) van igual y se muestran aparte.
export interface ConversationCitation {
  document_id: string;
  filename: string;
  title: string | null;
  file_type: string | null;
  relevance_score: number | null;
  chunk_index: number | null;
  chunk_content: string | null;
  message_index: number | null;
  created_at: string | null;
}

export async function listConversationCitations(
  conversationId: string
): Promise<ConversationCitation[]> {
  const res = await fetch(`${API_URL}/chat/conversations/${conversationId}/citations`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Error ${res.status}: Failed to load citations`);
  }
  return res.json();
}

// Abre un documento de la KB en una pestaña nueva mediante un fetch autenticado
// (Bearer token) + blob URL. window.open directo sobre el endpoint no adjunta la
// cabecera Authorization y devuelve 401 — de ahí este rodeo. Sirve igual para
// PDF (FileResponse) que para md/txt (el endpoint devuelve JSON en ese caso, así
// que para esos tipos el llamador debería preferir fetchDocumentContent + modal).
export async function openDocumentInNewTab(docId: string): Promise<void> {
  const res = await fetch(`${API_URL}/admin/documents/${docId}/content`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Error ${res.status}: Failed to open the document`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener,noreferrer");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

// ─── Documents ────────────────────────────────────────────────────────────────

export async function uploadDocument(
  file: File,
  category: string,
  serviceTag?: string
): Promise<{ id: string; filename: string; status: string; message: string }> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("category", category);
  if (serviceTag) formData.append("service_tag", serviceTag);

  const res = await fetch(`${API_URL}/admin/documents/upload`, {
    method: "POST",
    headers: getAuthHeaders(), // No poner Content-Type manual para FormData
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Upload failed");
  }
  return res.json();
}

export async function addDocumentURL(
  url: string,
  title: string,
  category: string,
  serviceTag?: string
): Promise<{ id: string; filename: string; status: string; message: string }> {
  const res = await fetch(`${API_URL}/admin/documents/url`, {
    method: "POST",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ url, title, category, service_tag: serviceTag }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to add URL");
  }
  return res.json();
}

export async function listDocuments(filters?: {
  category?: string;
  status?: string;
  service_tag?: string;
}): Promise<Document[]> {
  const params = new URLSearchParams();
  if (filters?.category) params.append("category", filters.category);
  if (filters?.status) params.append("status", filters.status);
  if (filters?.service_tag) params.append("service_tag", filters.service_tag);

  const res = await fetch(`${API_URL}/admin/documents/?${params}`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) throw new Error("Failed to fetch documents");
  return res.json();
}

export async function deleteDocument(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/admin/documents/${id}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (!res.ok) throw new Error("Failed to delete document");
}

export async function reindexDocument(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/admin/documents/${id}/reindex`, {
    method: "POST",
    headers: getAuthHeaders(),
  });
  if (!res.ok) throw new Error("Failed to reindex document");
}

export async function getKBStats(): Promise<KBStats> {
  const res = await fetch(`${API_URL}/admin/documents/stats/summary`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) throw new Error("Failed to fetch KB stats");
  return res.json();
}

export interface DocumentContentResult {
  isJson: boolean;
  content?: string;
  filename?: string;
  fileType?: string;
  url?: string; // presente cuando isJson es false: URL para abrir en pestaña nueva
}

export async function fetchDocumentContent(docId: string): Promise<DocumentContentResult> {
  const url = `${API_URL}/admin/documents/${docId}/content`;
  const res = await fetch(url, { headers: getAuthHeaders() });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Error ${res.status}: No se pudo abrir el documento`);
  }
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const data = await res.json();
    return { isJson: true, content: data.content, filename: data.filename, fileType: data.file_type };
  }
  return { isJson: false, url };
}

// ─── Incidents ────────────────────────────────────────────────────────────────

export async function getIncidents(): Promise<Incident[]> {
  const res = await fetch(`${API_URL}/incidents`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) return [];
  return res.json();
}

// ─── Simulator ────────────────────────────────────────────────────────────────

export async function getPresets(): Promise<Preset[]> {
  const res = await fetch(`${API_URL}/simulator/presets`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) throw new Error("Failed to fetch presets");
  return res.json();
}

export async function firePreset(
  presetKey: string
): Promise<{ status: string; incident_id: string; message: string }> {
  const res = await fetch(`${API_URL}/simulator/fire/${presetKey}`, {
    method: "POST",
    headers: getAuthHeaders(),
  });
  if (!res.ok) throw new Error("Failed to fire preset");
  return res.json();
}

export async function fireCustomAlert(payload: any): Promise<{
  status: string;
  incident_id: string;
}> {
  const res = await fetch(`${API_URL}/simulator/custom`, {
    method: "POST",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("Failed to fire custom alert");
  return res.json();
}

export function streamIncidentsFeed(): EventSource {
  return new EventSource(`${API_URL}/incidents/stream/feed`);
}

export function streamIncidentAnalysis(incidentId: string): EventSource {
  return new EventSource(`${API_URL}/incidents/stream/${incidentId}`);
}

// ─── Health ───────────────────────────────────────────────────────────────────

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/health`);
    return res.ok;
  } catch {
    return false;
  }
}
export interface AuditLog {
  id: string;
  incident_id: string;
  action_id: string;
  target: string;
  executed_by: string;
  status: string;
  details?: string | null;
  timestamp: string | null;
}

export async function fetchAuditLogs(): Promise<AuditLog[]> {
  const res = await fetch(`${API_URL}/audit-logs`, {
    cache: "no-store",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
  });

  if (!res.ok) {
    throw new Error("Error al obtener los registros de auditoría");
  }

  return res.json();
}
// En lib/api.ts

export async function resolveIncident(
  id: string,
  resolutionNotes: string
): Promise<void> {
  const res = await fetch(`${API_URL}/incidents/${id}/resolve`, {
    method: "POST",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      resolution_notes: resolutionNotes,
    }),
  });
  if (!res.ok) throw new Error(`Error ${res.status}: No se pudo procesar la resolución`);
}

export async function updateIncidentStatus(id: string, status: string): Promise<void> {
  const res = await fetch(`${API_URL}/incidents/${id}/status`, {
    method: "PATCH",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ status }),
  });
  if (!res.ok) throw new Error("Error al actualizar el estado");
}

export async function executeRemediation(
  incidentId: string,
  actionId: string,
  parameters: Record<string, any>
): Promise<any> {
  const res = await fetch(`${API_URL}/incidents/${incidentId}/remediate`, {
    method: "POST",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ action_id: actionId, parameters }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Error ${res.status}: No se pudo ejecutar la remediación`);
  }
  return res.json();
}

export async function exportPostmortem(incidentId: string): Promise<Blob> {
  const res = await fetch(`${API_URL}/incidents/${incidentId}/post-mortem/export?format=pdf`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Error ${res.status}: No se pudo generar el Post-Mortem`);
  }
  return res.blob();
}

// ─── Users ────────────────────────────────────────────────────────────────────

export interface UserItem {
  id: string;
  username: string;
  email: string;
  role: string;
  full_name?: string;
  is_active: boolean;
  last_login?: string;
}

export async function listUsers(): Promise<UserItem[]> {
  const res = await fetch(`${API_URL}/api/v1/users`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) throw new Error(`Error ${res.status}: No se pudo obtener la lista de usuarios`);
  return res.json();
}

export async function createUser(payload: {
  username: string;
  email: string;
  password: string;
  full_name: string;
  role: string;
}): Promise<UserItem> {
  const res = await fetch(`${API_URL}/api/v1/users`, {
    method: "POST",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    if (Array.isArray(errorData.detail)) {
      const msg = errorData.detail
        .map((item: any) => `${item.loc[item.loc.length - 1]}: ${item.msg}`)
        .join(" | ");
      throw new Error(msg);
    }
    throw new Error(errorData.detail || `Error ${res.status}: No se pudo crear el usuario`);
  }
  return res.json();
}

export async function updateUser(
  userId: string,
  payload: { role?: string; is_active?: boolean }
): Promise<UserItem> {
  const res = await fetch(`${API_URL}/api/v1/users/${userId}`, {
    method: "PATCH",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Error ${res.status}: No se pudo actualizar el usuario`);
  return res.json();
}