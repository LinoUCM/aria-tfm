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

// ─── Incidents ────────────────────────────────────────────────────────────────

export async function getIncidents(): Promise<Incident[]> {
  const res = await fetch(`${API_URL}/incidents/`, {
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
  timestamp: string | null;
}

export async function fetchAuditLogs(): Promise<AuditLog[]> {
  const token = typeof window !== "undefined" ? localStorage.getItem("aria_token") : null;
  const API_BASE_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

  const res = await fetch(`${API_BASE_URL}/audit-logs`, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });

  if (!res.ok) {
    throw new Error("Error al obtener los registros de auditoría");
  }

  return res.json();
}