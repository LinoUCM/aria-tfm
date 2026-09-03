"use client";

import { useState, useEffect } from "react";
import { streamIncidentsFeed, streamIncidentAnalysis } from "@/lib/api_12082026";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";
import {
  AlertTriangle, CheckCircle, Clock, Activity,
  ChevronDown, ChevronUp, Loader2, Zap, Server
} from "lucide-react";

interface Incident {
  id: string;
  title: string;
  severity: "P1" | "P2" | "P3" | "P4";
  status: "open" | "investigating" | "resolved";
  service_affected?: string;
  host?: string;
  tags: string[];
  metrics: Record<string, number>;
  analysis?: any;
  created_at: string;
  resolved_at?: string;
  resolution_time_minutes?: number;
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

  useEffect(() => {
    const es = streamIncidentsFeed();

    es.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.event === "incident_update") {
        setIncidents((prev) => {
          const exists = prev.find((i) => i.id === data.data.id);
          if (exists) {
            return prev.map((i) => i.id === data.data.id ? { ...i, ...data.data } : i);
          }
          return [data.data, ...prev];
        });
      }
    };

    return () => es.close();
  }, []);

  const handleExpand = (incident: Incident) => {
    if (expandedId === incident.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(incident.id);

    // Stream analysis if available
    if (!analysisStreams[incident.id]) {
      const es = streamIncidentAnalysis(incident.id);
      let content = "";

      es.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.event === "token") {
          content += data.data.token;
          setAnalysisStreams((prev) => ({ ...prev, [incident.id]: content }));
        }
        if (data.event === "done" || data.event === "error") {
          es.close();
        }
      };
    }
  };

  const openCount = incidents.filter((i) => i.status === "open").length;
  const p1Count = incidents.filter((i) => i.severity === "P1" && i.status !== "resolved").length;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
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

      {/* Summary Cards */}
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
            <span className="text-gray-400 text-xs">Resolved today</span>
          </div>
          <div className="text-3xl font-bold text-white">
            {incidents.filter((i) => i.status === "resolved").length}
          </div>
        </div>
      </div>

      {/* Incidents List */}
      <div className="space-y-3">
        {incidents.length === 0 ? (
          <div className="text-center py-16 text-gray-500">
            <Zap className="w-12 h-12 mx-auto mb-3 opacity-20" />
            <p>No incidents yet</p>
            <p className="text-xs mt-1">Use the Simulator to fire a test alert</p>
          </div>
        ) : (
          incidents.map((incident) => {
            const sev = SEVERITY_CONFIG[incident.severity];
            const isExpanded = expandedId === incident.id;

            return (
              <div
                key={incident.id}
                className={clsx("border rounded-xl overflow-hidden transition-all", sev.border, sev.bg)}
              >
                {/* Incident Header */}
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
                        {formatDistanceToNow(new Date(incident.created_at), { addSuffix: true })}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-3 flex-shrink-0">
                    <StatusBadge status={incident.status} />
                    {isExpanded ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
                  </div>
                </button>

                {/* Expanded Content */}
                {isExpanded && (
                  <div className="border-t border-gray-800 p-4 space-y-4">
                    {/* Metrics */}
                    {incident.metrics && Object.keys(incident.metrics).length > 0 && (
                      <div>
                        <h4 className="text-xs font-medium text-gray-400 mb-2">Metrics</h4>
                        <div className="grid grid-cols-3 gap-2">
                          {Object.entries(incident.metrics).slice(0, 6).map(([key, value]) => (
                            <div key={key} className="bg-gray-900/50 rounded-lg px-3 py-2">
                              <div className="text-xs text-gray-500">{key.replace(/_/g, " ")}</div>
                              <div className="text-sm font-mono text-white">
                                {typeof value === "number" ? value.toLocaleString() : value}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* ARIA Analysis */}
                    <div>
                      <h4 className="text-xs font-medium text-gray-400 mb-2 flex items-center gap-1">
                        <Activity className="w-3 h-3" /> ARIA Analysis
                      </h4>
                      {analysisStreams[incident.id] ? (
                        <div className="bg-gray-900 rounded-lg p-3 text-sm text-gray-300 leading-relaxed whitespace-pre-wrap">
                          {analysisStreams[incident.id]}
                        </div>
                      ) : (
                        <div className="flex items-center gap-2 text-sm text-gray-500">
                          <Loader2 className="w-4 h-4 animate-spin" />
                          ARIA is analyzing this incident...
                        </div>
                      )}
                    </div>

                    {/* Tags */}
                    {incident.tags?.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {incident.tags.map((tag) => (
                          <span key={tag} className="text-xs px-2 py-0.5 bg-gray-800 text-gray-400 rounded-full">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const config = {
    open: { label: "Open", color: "text-red-400 bg-red-900/30" },
    investigating: { label: "Investigating", color: "text-yellow-400 bg-yellow-900/30" },
    resolved: { label: "Resolved", color: "text-green-400 bg-green-900/30" },
  }[status] || { label: status, color: "text-gray-400 bg-gray-800" };

  return (
    <span className={clsx("text-xs px-2 py-0.5 rounded-full font-medium", config.color)}>
      {config.label}
    </span>
  );
}
