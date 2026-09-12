// app/incidents/page.tsx
"use client";

import { TopologyMap } from "@/components/TopologyMap";
import { useState, useEffect, useRef, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { getIncidents, exportPostmortem, streamIncidentsFeed, streamIncidentAnalysis, errorMessage } from "@/lib/api";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";
import ReactMarkdown from "react-markdown";
import { RemediationCard } from "@/components/RemediationCard";
import { ResolveIncidentModal } from "@/components/ResolveIncidentModal";
import {
  AlertTriangle, CheckCircle, Clock, Activity,
  ChevronDown, ChevronUp, Loader2, Zap, Server, Filter, Check, AlertCircle,
  FileText, Copy, X as XIcon
} from "lucide-react";

interface Incident {
  id: string;
  title: string;
  description?: string;
  severity: "P1" | "P2" | "P3" | "P4";
  status: string;
  service_affected?: string;
  root_cause?: string;
  host?: string;
  tags: string[];
  metrics: Record<string, number>;
  analysis?: string;
  suggested_action?: string | null;
  has_postmortem?: boolean;
  postmortem_filename?: string | null; // <--- Añadir esta línea
  notification_status?: "sent" | "skipped" | "failed" | null; // envío alerta Telegram/n8n; null = no intentado
  notification_sent_at?: string | null;
  created_at: string;
}

const SEVERITY_CONFIG = {
  P1: { label: "P1 Critical", color: "text-red-400", bg: "bg-red-900/20", border: "border-red-800" },
  P2: { label: "P2 High", color: "text-orange-400", bg: "bg-orange-900/20", border: "border-orange-800" },
  P3: { label: "P3 Medium", color: "text-yellow-400", bg: "bg-yellow-900/20", border: "border-yellow-800" },
  P4: { label: "P4 Low", color: "text-blue-400", bg: "bg-blue-900/20", border: "border-blue-800" },
};

/**
 * Componente de acción Post-Mortem: Genera la 1ª vez y abre/descarga el PDF persistido las siguientes
 */
function PostMortemButton({
  incident,
  onPostmortemGenerated,
}: {
  incident: Incident;
  onPostmortemGenerated: () => void;
}) {
  const [loading, setLoading] = useState(false);
  // Considera que existe si `has_postmortem` es verdadero O si `postmortem_filename` trae valor
  const hasPostmortem = Boolean(incident.has_postmortem || incident.postmortem_filename);

  const handleAction = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setLoading(true);

    try {
      const blob = await exportPostmortem(incident.id);

      // Descarga automática del PDF generado
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `POSTMORTEM-${incident.id.slice(0, 8)}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);

      onPostmortemGenerated();
    } catch (err) {
      console.error(err);
      alert(errorMessage(err, "Failed to process the Post-Mortem"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={handleAction}
      disabled={loading}
      className={clsx(
        "flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg font-medium transition border disabled:opacity-50",
        hasPostmortem
          ? "bg-emerald-900/40 hover:bg-emerald-800/60 text-emerald-300 border-emerald-700/50"
          : "bg-indigo-900/40 hover:bg-indigo-800/60 text-indigo-300 border-indigo-700/50"
      )}
      title={hasPostmortem ? "Download Post-Mortem PDF" : "Generate and download Post-Mortem report"}
    >
      {loading ? (
        <Loader2 className="w-3.5 h-3.5 animate-spin text-indigo-400" />
      ) : hasPostmortem ? (
        <FileText className="w-3.5 h-3.5 text-emerald-400" />
      ) : (
        <Zap className="w-3.5 h-3.5 text-indigo-400" />
      )}
      <span>
        {loading
          ? "Generating..."
          : hasPostmortem
          ? "View Post-Mortem"
          : "Generate Post-Mortem"}
      </span>
    </button>
  );
}

// Cuerpo real de la página. Se separa del export por defecto porque usa
// useSearchParams() (deep-link `?incident=<id>` desde Telegram/n8n), que
// `next build` exige envolver en un límite de Suspense para poder generar la
// ruta (mismo patrón ya usado en app/chat/page.tsx). No cambia ninguna lógica.
function IncidentsPageInner() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [analysisStreams, setAnalysisStreams] = useState<Record<string, string>>({});
  const [streamStatus, setStreamStatus] = useState<Record<string, "streaming" | "done" | "error">>({});

  const [filterSeverity, setFilterSeverity] = useState<string>("ALL");
  const [filterStatus, setFilterStatus] = useState<string>("ALL");

  const [resolveModalData, setResolveModalData] = useState<{ id: string; title: string } | null>(null);

  // Deep-link `/incidents?incident=<id>` (enlace de las notificaciones de
  // Telegram, ver backend `_send_n8n_notification`): al cargar, si el ID viene
  // en la URL y aparece en la lista, se despliega automáticamente y se hace
  // scroll hasta esa fila. `deepLinkHandledRef` evita repetir el auto-scroll
  // en cada actualización de `incidents` (llegan por SSE); `deepLinkNotFound`
  // cubre el caso de un incidente más antiguo que los últimos 50 que trae
  // `getIncidents()` (limitación ya existente de la lista, no introducida por
  // esto) — se avisa en vez de fallar en silencio.
  const searchParams = useSearchParams();
  const deepLinkId = searchParams.get("incident");
  const deepLinkHandledRef = useRef(false);
  const [deepLinkNotFound, setDeepLinkNotFound] = useState<string | null>(null);

  // Copiar el ID del incidente (lista o detalle) con un clic — para que el
  // equipo de guardia pueda localizar/confirmar el incidente exacto recibido
  // por Telegram/email dentro de la propia interfaz.
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const handleCopyId = async (id: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      await navigator.clipboard.writeText(id);
      setCopiedId(id);
      setTimeout(() => setCopiedId((prev) => (prev === id ? null : prev)), 1500);
    } catch {
      // Clipboard API puede no estar disponible (permisos, contexto no
      // seguro); no es crítico, el ID sigue visible para copiarlo a mano.
    }
  };

  useEffect(() => {
    let isMounted = true;

    const fetchIncidents = async () => {
      try {
        const data = await getIncidents();
        if (isMounted) {
          setIncidents(data);
        }
      } catch (err) {
        console.error("Error cargando incidentes:", err);
      }
    };

    fetchIncidents();

    const es = streamIncidentsFeed();
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.event === "incident_update" && isMounted) {
          setIncidents((prev) => {
            const exists = prev.find((i) => i.id === data.data.id);
            if (exists) {
              return prev.map((i) => (i.id === data.data.id ? { ...i, ...data.data } : i));
            }
            return [data.data, ...prev];
          });
        }
      } catch (err) {
        console.error("Error decodificando SSE:", err);
      }
    };

    return () => {
      isMounted = false;
      es.close();
    };
  }, []);

  const handleExpand = (incident: Incident) => {
    if (expandedId === incident.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(incident.id);

    const isResolved = (incident.status || "").toUpperCase() === "RESOLVED";

    if (!analysisStreams[incident.id] && !incident.analysis && !isResolved) {
      setStreamStatus((prev) => ({ ...prev, [incident.id]: "streaming" }));

      let content = "";
      let receivedTokens = false;
      const es = streamIncidentAnalysis(incident.id);

      // El análisis se dispara una sola vez, al crearse el incidente, y llega por
      // el canal SSE general. Si el incidente es reciente (<2 min) casi seguro
      // sigue en curso — el pipeline completo (routing + RAG + posible búsqueda
      // web + LLM con fallback Gemini→Ollama→Groq) puede tardar bastante más de
      // unos segundos —, así que esperamos hasta 90s antes de rendirnos. Si ya
      // tiene 2 min o más y sigue sin análisis, no hay ninguna tarea de fondo
      // corriendo: bastan 3s para dar tiempo a que el EventSource conecte antes
      // de mostrar "no report".
      const ageMs = Date.now() - new Date(incident.created_at).getTime();
      const giveUpAfterMs = ageMs < 120_000 ? 90_000 : 3_000;

      const timeout = setTimeout(() => {
        if (!receivedTokens) {
          es.close();
          setStreamStatus((prev) => ({ ...prev, [incident.id]: "done" }));
        }
      }, giveUpAfterMs);

      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.event === "token") {
            receivedTokens = true;
            content += data.data.token;
            setAnalysisStreams((prev) => ({ ...prev, [incident.id]: content }));
          }
          if (data.event === "done") {
            clearTimeout(timeout);
            setStreamStatus((prev) => ({ ...prev, [incident.id]: "done" }));
            es.close();
          }
          if (data.event === "error") {
            clearTimeout(timeout);
            setStreamStatus((prev) => ({ ...prev, [incident.id]: "error" }));
            es.close();
          }
        } catch (err) {
          console.error("Error decodificando análisis SSE:", err);
        }
      };

      es.onerror = () => {
        clearTimeout(timeout);
        setStreamStatus((prev) => ({ ...prev, [incident.id]: "done" }));
        es.close();
      };
    }
  };

  useEffect(() => {
    if (!deepLinkId || deepLinkHandledRef.current || incidents.length === 0) return;
    const match = incidents.find((i) => i.id === deepLinkId);
    if (match) {
      deepLinkHandledRef.current = true;
      setDeepLinkNotFound(null);
      handleExpand(match);
      // Espera al siguiente frame para que la fila ya esté en el DOM
      // (el acordeón se acaba de desplegar) antes de hacer scroll.
      requestAnimationFrame(() => {
        document
          .getElementById(`incident-${deepLinkId}`)
          ?.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    } else {
      // No está en los últimos `limit` incidentes cargados. Se avisa en vez
      // de dejar la página en blanco sin explicación; sigue reintentando en
      // cada actualización de `incidents` (p. ej. si llega justo después por
      // el feed SSE) hasta encontrarlo.
      setDeepLinkNotFound(deepLinkId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidents, deepLinkId]);

  const handleOpenResolve = (incident: Incident, e: React.MouseEvent) => {
    e.stopPropagation();
    setResolveModalData({ id: incident.id, title: incident.title });
  };

  const filteredIncidents = incidents.filter((inc) => {
    const sev = (inc.severity || "").toUpperCase();
    const stat = (inc.status || "").toUpperCase();

    const matchSev = filterSeverity === "ALL" || sev === filterSeverity;
    let matchStat = true;
    if (filterStatus === "OPEN") {
      matchStat = stat === "OPEN" || stat === "IN_PROGRESS" || stat === "INVESTIGATING";
    } else if (filterStatus === "RESOLVED") {
      matchStat = stat === "RESOLVED";
    }

    return matchSev && matchStat;
  });

  const openCount = incidents.filter((i) => {
    const s = (i.status || "").toUpperCase();
    return s === "OPEN" || s === "IN_PROGRESS" || s === "INVESTIGATING";
  }).length;

  const p1Count = incidents.filter((i) => {
    const s = (i.status || "").toUpperCase();
    return (i.severity || "").toUpperCase() === "P1" && s !== "RESOLVED";
  }).length;

  const resolvedCount = incidents.filter((i) => (i.status || "").toUpperCase() === "RESOLVED").length;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Incidents</h1>
          <p className="text-gray-400 text-sm mt-1">Real-time incident feed</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
          <span className="text-xs text-gray-400">Live</span>
        </div>
      </div>

      {deepLinkNotFound && (
        <div className="flex items-center justify-between gap-3 bg-yellow-900/20 border border-yellow-800 text-yellow-300 text-xs rounded-lg px-4 py-2.5">
          <span>
            Incident <code className="font-mono">{deepLinkNotFound.slice(0, 8)}</code> from the link isn&apos;t in the
            list currently shown (it may be older than the most recent incidents loaded here).
          </span>
          <button
            onClick={() => setDeepLinkNotFound(null)}
            className="text-yellow-400 hover:text-yellow-200 flex-shrink-0"
            aria-label="Dismiss"
          >
            <XIcon className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      <div className="grid grid-cols-3 gap-4">
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-1">
            <Activity className="w-4 h-4 text-blue-400" />
            <span className="text-gray-400 text-xs">Active</span>
          </div>
          <div className="text-3xl font-bold text-white">{openCount}</div>
        </div>
        <div className={clsx("border rounded-xl p-4", p1Count > 0 ? "bg-red-900/10 border-red-800" : "bg-gray-900 border-gray-800")}>
          <div className="flex items-center gap-2 mb-1">
            <AlertTriangle className={clsx("w-4 h-4", p1Count > 0 ? "text-red-400" : "text-gray-500")} />
            <span className="text-gray-400 text-xs">P1 Critical</span>
          </div>
          <div className={clsx("text-3xl font-bold", p1Count > 0 ? "text-red-400" : "text-white")}>{p1Count}</div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-1">
            <CheckCircle className="w-4 h-4 text-green-400" />
            <span className="text-gray-400 text-xs">Resolved</span>
          </div>
          <div className="text-3xl font-bold text-white">{resolvedCount}</div>
        </div>
      </div>

      <div className="flex items-center gap-4 bg-gray-900 p-4 rounded-xl border border-gray-800">
        <div className="flex items-center gap-2 text-gray-400 text-xs font-semibold uppercase tracking-wider">
          <Filter className="w-4 h-4 text-blue-400" />
          Filters:
        </div>

        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-400">Severity:</label>
          <select
            value={filterSeverity}
            onChange={(e) => setFilterSeverity(e.target.value)}
            className="bg-gray-800 text-xs text-white rounded-lg px-3 py-1.5 border border-gray-700 outline-none cursor-pointer"
          >
            <option value="ALL">All</option>
            <option value="P1">P1 — Critical</option>
            <option value="P2">P2 — High</option>
            <option value="P3">P3 — Medium</option>
            <option value="P4">P4 — Low</option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-400">Status:</label>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="bg-gray-800 text-xs text-white rounded-lg px-3 py-1.5 border border-gray-700 outline-none cursor-pointer"
          >
            <option value="ALL">All</option>
            <option value="OPEN">Open / Investigating</option>
            <option value="RESOLVED">Resolved</option>
          </select>
        </div>
      </div>

      <div className="space-y-3">
        {filteredIncidents.length === 0 ? (
          <div className="text-center py-16 text-gray-500 bg-gray-900/50 rounded-xl border border-gray-800">
            <Zap className="w-12 h-12 mx-auto mb-3 opacity-20" />
            <p className="text-sm font-medium">No incidents found</p>
          </div>
        ) : (
          filteredIncidents.map((incident) => {
            const sevKey = (incident.severity || "P3").toUpperCase() as keyof typeof SEVERITY_CONFIG;
            const sev = SEVERITY_CONFIG[sevKey] || SEVERITY_CONFIG.P3;
            const isExpanded = expandedId === incident.id;
            const isResolved = (incident.status || "").toUpperCase() === "RESOLVED";

            const analysisText = incident.analysis || analysisStreams[incident.id];
            const currentStatus = streamStatus[incident.id];

            return (
              <div
                key={incident.id}
                id={`incident-${incident.id}`}
                className={clsx(
                  "relative border rounded-xl transition-all",
                  isExpanded ? "z-20" : "z-0 hover:z-30 focus-within:z-40",
                  sev.border,
                  sev.bg
                )}
              >
                <div
                  onClick={() => handleExpand(incident)}
                  className="w-full flex items-center gap-4 px-4 py-3 cursor-pointer hover:bg-white/5 transition-colors rounded-xl"
                >
                  <div className={clsx("text-xs font-bold px-2 py-1 rounded flex-shrink-0", sev.color, sev.bg, "border", sev.border)}>
                    {incident.severity}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="text-white text-sm font-medium truncate">{incident.title}</div>
                    <div className="flex items-center gap-3 mt-0.5">
                      {incident.service_affected && (
                        <span className="text-xs text-gray-400 flex items-center gap-1">
                          <Server className="w-3 h-3" /> {incident.service_affected}
                        </span>
                      )}
                      <span className="text-xs text-gray-500 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {incident.created_at ? formatDistanceToNow(new Date(incident.created_at), { addSuffix: true }) : "recently"}
                      </span>
                      {/* ID corto, copiable con un clic: el mensaje de Telegram/email solo
                          da el ID -- esto permite localizar y confirmar visualmente que es
                          el incidente correcto sin tener que abrir el detalle. */}
                      <button
                        onClick={(e) => handleCopyId(incident.id, e)}
                        title={`Copy full ID: ${incident.id}`}
                        className="text-xs text-gray-500 hover:text-gray-300 flex items-center gap-1 font-mono transition"
                      >
                        {copiedId === incident.id ? (
                          <>
                            <Check className="w-3 h-3 text-emerald-400" />
                            <span className="text-emerald-400">Copied</span>
                          </>
                        ) : (
                          <>
                            <Copy className="w-3 h-3" />#{incident.id.slice(0, 8)}
                          </>
                        )}
                      </button>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                    {!isResolved && (
                      <button
                        onClick={(e) => handleOpenResolve(incident, e)}
                        className="flex items-center gap-1.5 bg-emerald-700/80 hover:bg-emerald-600 text-white text-xs px-3 py-1.5 rounded-lg font-medium transition border border-emerald-600"
                      >
                        <Check className="w-3.5 h-3.5" />
                        Mark Resolved
                      </button>
                    )}

                    {/* Botón único Post-Mortem */}
                    <PostMortemButton
                      incident={incident}
                      onPostmortemGenerated={() => {
                        setIncidents((prev) =>
                          prev.map((i) =>
                            i.id === incident.id ? { ...i, has_postmortem: true, postmortem_filename: `POSTMORTEM-${incident.id.slice(0, 8)}.pdf`, } : i
                          )
                        );
                      }}
                    />

                    <StatusBadge status={incident.status} />
                    <NotificationBadge status={incident.notification_status} />
                    <button onClick={() => handleExpand(incident)} className="p-1 hover:text-white text-gray-500 transition">
                      {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                {isExpanded && (
                  <div className="border-t border-gray-800 p-4 space-y-4">
                    <div className="flex items-center gap-2 text-xs text-gray-500">
                      <span className="text-gray-400">Incident ID:</span>
                      <button
                        onClick={(e) => handleCopyId(incident.id, e)}
                        title="Copy full ID"
                        className="flex items-center gap-1.5 font-mono text-gray-300 hover:text-white bg-gray-900/50 border border-gray-800 rounded px-2 py-1 transition"
                      >
                        {incident.id}
                        {copiedId === incident.id ? (
                          <Check className="w-3 h-3 text-emerald-400 flex-shrink-0" />
                        ) : (
                          <Copy className="w-3 h-3 flex-shrink-0" />
                        )}
                      </button>
                      {copiedId === incident.id && <span className="text-emerald-400">Copied</span>}
                    </div>

                    {incident.metrics && Object.keys(incident.metrics).length > 0 && (
                      <div>
                        <h4 className="text-xs font-medium text-gray-400 mb-2">Metrics</h4>
                        <div className="grid grid-cols-3 gap-2">
                          {Object.entries(incident.metrics).slice(0, 6).map(([key, value]) => (
                            <div key={key} className="bg-gray-900/50 rounded-lg px-3 py-2 border border-gray-800">
                              <div className="text-xs text-gray-500">{key.replace(/_/g, " ")}</div>
                              <div className="text-sm font-mono text-white">
                                {typeof value === "number" ? value.toLocaleString() : value}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    <div>
                      <h4 className="text-xs font-medium text-gray-400 mb-2">Service Topology</h4>
                      <TopologyMap 
                        affectedService={incident.service_affected || "checkout-service"} 
                        rootCause={incident.root_cause || incident.service_affected}
                      />
                    </div>  

                    <div>
                      <h4 className="text-xs font-medium text-gray-400 mb-2 flex items-center gap-1">
                        <Activity className="w-3 h-3" /> ARIA Analysis
                      </h4>
                      {analysisText ? (
                        <div className="bg-gray-900 rounded-lg p-4 text-sm text-gray-300 leading-relaxed border border-gray-800">
                          <ReactMarkdown
                            components={{
                              p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                              ul: ({ children }) => <ul className="list-disc pl-4 space-y-1 my-2">{children}</ul>,
                              ol: ({ children }) => <ol className="list-decimal pl-4 space-y-1 my-2">{children}</ol>,
                              li: ({ children }) => <li className="text-gray-300">{children}</li>,
                              code: ({ children }) => (
                                <code className="bg-gray-950 px-1.5 py-0.5 rounded text-xs font-mono text-emerald-400 border border-gray-800">
                                  {children}
                                </code>
                              ),
                            }}
                          >
                            {analysisText}
                          </ReactMarkdown>
                          {!isResolved && (
                            <RemediationCard
                              incidentId={incident.id}
                              suggestedAction={incident.suggested_action}
                              onResolved={() => {
                                setIncidents((prev) =>
                                  prev.map((i) => (i.id === incident.id ? { ...i, status: "RESOLVED" } : i))
                                );
                              }}
                            />
                          )}
                        </div>
                      ) : currentStatus === "streaming" ? (
                        <div className="flex items-center gap-2 text-sm text-gray-500 py-2">
                          <Loader2 className="w-4 h-4 animate-spin text-blue-400" />
                          ARIA is analyzing this incident...
                        </div>
                      ) : (
                        <div className="flex items-center gap-2 text-sm text-gray-500 py-2 italic">
                          <AlertCircle className="w-4 h-4 text-gray-600" />
                          No stored report for this past incident. New alerts will show their saved analysis.
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      <ResolveIncidentModal
        isOpen={!!resolveModalData}
        incidentId={resolveModalData?.id || null}
        incidentTitle={resolveModalData?.title}
        onClose={() => setResolveModalData(null)}
        onSuccess={(resolvedId) => {
          setIncidents((prev) =>
            prev.map((i) => (i.id === resolvedId ? { ...i, status: "RESOLVED" } : i))
          );
        }}
      />
    </div>
  );
}

export default function IncidentsPage() {
  return (
    <Suspense fallback={null}>
      <IncidentsPageInner />
    </Suspense>
  );
}

function StatusBadge({ status }: { status: string }) {
  const s = (status || "").toUpperCase();
  let label = status;
  let color = "text-gray-400 bg-gray-800";

  if (s === "OPEN") {
    label = "Open";
    color = "text-red-400 bg-red-900/30 border border-red-800/50";
  } else if (s === "IN_PROGRESS" || s === "INVESTIGATING") {
    label = "Investigating";
    color = "text-yellow-400 bg-yellow-900/30 border border-yellow-800/50";
  } else if (s === "RESOLVED") {
    label = "Resolved";
    color = "text-green-400 bg-green-900/30 border border-green-800/50";
  }

  return <span className={clsx("text-xs px-2.5 py-1 rounded-full font-medium", color)}>{label}</span>;
}

// Estado del envío de la alerta a Telegram/n8n (persistido en el incidente,
// ver backend `_send_n8n_notification`). Mismo patrón visual que StatusBadge.
// Sin badge si nunca se intentó el envío (null / N8N_WEBHOOK_URL no configurado).
//
// "skipped" != "failed": el workflow de n8n solo escala a Telegram en P1 (y
// tiene un nodo de email desactivado); en un no-P1 decide NO escalar y eso
// no es un error -- por eso lleva su propio badge neutro ("No escalation"),
// no el rojo de fallo real de envío.
function NotificationBadge({ status }: { status?: "sent" | "skipped" | "failed" | null }) {
  if (status !== "sent" && status !== "skipped" && status !== "failed") return null;
  let label: string;
  let color: string;
  if (status === "sent") {
    label = "🔔 Telegram: Sent";
    color = "text-green-400 bg-green-900/30 border border-green-800/50";
  } else if (status === "skipped") {
    label = "No escalation";
    color = "text-gray-400 bg-gray-800 border border-gray-700/50";
  } else {
    label = "🔔 Telegram: Failed";
    color = "text-red-400 bg-red-900/30 border border-red-800/50";
  }
  return <span className={clsx("text-xs px-2.5 py-1 rounded-full font-medium", color)}>{label}</span>;
}
