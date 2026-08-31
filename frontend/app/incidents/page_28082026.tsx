// app/incidents/page.tsx
"use client";

import { useState, useEffect } from "react";
import { streamIncidentsFeed, streamIncidentAnalysis } from "@/lib/api";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";
import ReactMarkdown from "react-markdown";
import { RemediationCard } from "@/components/RemediationCard";
import { ResolveIncidentModal } from "@/components/ResolveIncidentModal";
import {
  AlertTriangle, CheckCircle, Clock, Activity,
  ChevronDown, ChevronUp, Loader2, Zap, Server, Filter, Check, AlertCircle
} from "lucide-react";

interface Incident {
  id: string;
  title: string;
  description?: string;
  severity: "P1" | "P2" | "P3" | "P4";
  status: string;
  service_affected?: string;
  host?: string;
  tags: string[];
  metrics: Record<string, number>;
  analysis?: any;
  created_at: string;
}

const SEVERITY_CONFIG = {
  P1: { label: "P1 Critical", color: "text-red-400", bg: "bg-red-900/20", border: "border-red-800" },
  P2: { label: "P2 High", color: "text-orange-400", bg: "bg-orange-900/20", border: "border-orange-800" },
  P3: { label: "P3 Medium", color: "text-yellow-400", bg: "bg-yellow-900/20", border: "border-yellow-800" },
  P4: { label: "P4 Low", color: "text-blue-400", bg: "bg-blue-900/20", border: "border-blue-800" },
};

export default function IncidentsPage() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [analysisStreams, setAnalysisStreams] = useState<Record<string, string>>({});
  const [streamStatus, setStreamStatus] = useState<Record<string, "streaming" | "done" | "error">>({});

  const [filterSeverity, setFilterSeverity] = useState<string>("ALL");
  const [filterStatus, setFilterStatus] = useState<string>("ALL");

  // Estado para controlar el modal de resolución
  const [resolveModalData, setResolveModalData] = useState<{ id: string; title: string } | null>(null);

  useEffect(() => {
    let isMounted = true;

    const fetchIncidents = async () => {
      try {
        const match = document.cookie.match(new RegExp("(^| )aria_token=([^;]+)"));
        const token = match ? match[2] : localStorage.getItem("aria_token") || "";

        const res = await fetch("http://localhost:8000/incidents", {
          headers: {
            "Authorization": `Bearer ${token}`,
            "Content-Type": "application/json"
          },
        });

        if (res.ok) {
          const data = await res.json();
          if (isMounted) {
            setIncidents(Array.isArray(data) ? data : data.incidents || []);
          }
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

      const timeout = setTimeout(() => {
        if (!receivedTokens) {
          es.close();
          setStreamStatus((prev) => ({ ...prev, [incident.id]: "done" }));
        }
      }, 6000);

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
          Filtros:
        </div>

        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-400">Severidad:</label>
          <select
            value={filterSeverity}
            onChange={(e) => setFilterSeverity(e.target.value)}
            className="bg-gray-800 text-xs text-white rounded-lg px-3 py-1.5 border border-gray-700 outline-none cursor-pointer"
          >
            <option value="ALL">Todas</option>
            <option value="P1">P1 — Critical</option>
            <option value="P2">P2 — High</option>
            <option value="P3">P3 — Medium</option>
            <option value="P4">P4 — Low</option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-400">Estado:</label>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="bg-gray-800 text-xs text-white rounded-lg px-3 py-1.5 border border-gray-700 outline-none cursor-pointer"
          >
            <option value="ALL">Todos</option>
            <option value="OPEN">Abiertos / En investigación</option>
            <option value="RESOLVED">Resueltos</option>
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
              <div key={incident.id} className={clsx("border rounded-xl overflow-hidden transition-all", sev.border, sev.bg)}>
                <button
                  onClick={() => handleExpand(incident)}
                  className="w-full flex items-center gap-4 px-4 py-3 text-left hover:bg-white/5 transition-colors"
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
                    </div>
                  </div>

                  <div className="flex items-center gap-3 flex-shrink-0">
                    {!isResolved && (
                      <button
                        onClick={(e) => handleOpenResolve(incident, e)}
                        className="flex items-center gap-1.5 bg-emerald-700/80 hover:bg-emerald-600 text-white text-xs px-3 py-1.5 rounded-lg font-medium transition border border-emerald-600"
                      >
                        <Check className="w-3.5 h-3.5" />
                        Marcar Resuelto
                      </button>
                    )}

                    <StatusBadge status={incident.status} />
                    {isExpanded ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
                  </div>
                </button>

                {isExpanded && (
                  <div className="border-t border-gray-800 p-4 space-y-4">
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
                              analysisText={analysisText}
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
                          No hay informe persistido para este incidente pasado. Los nuevos alertas mostrarán su análisis guardado.
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

      {/* Modal para ingresar notas de resolución */}
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