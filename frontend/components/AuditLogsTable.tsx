"use client";

import React, { useEffect, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { fetchAuditLogs, AuditLog } from "@/lib/api";

export default function AuditLogsTable() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const loadLogs = async () => {
    try {
      setLoading(true);
      const data = await fetchAuditLogs();
      setLogs(data);
    } catch (error) {
      console.error("Error al cargar auditoría:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadLogs();
  }, []);

  const toggleRow = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  return (
    <div className="p-6 bg-gray-900 text-white rounded-xl shadow-lg border border-gray-800">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h2 className="text-xl font-bold tracking-wide">Audit Trail / Historial de Remediación</h2>
          <p className="text-sm text-gray-400">Registro inmutable de acciones automáticas y manuales</p>
        </div>
        <button
          onClick={loadLogs}
          className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-xs font-semibold rounded-lg border border-gray-700 transition flex items-center gap-2"
        >
          🔄 Actualizar
        </button>
      </div>

      {loading ? (
        <div className="text-center py-8 text-gray-500">Cargando registros...</div>
      ) : logs.length === 0 ? (
        <div className="text-center py-8 text-gray-500">No hay registros de auditoría aún.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-gray-300">
            <thead className="bg-gray-800/60 text-gray-400 uppercase text-xs">
              <tr>
                <th className="p-3">Fecha / Hora</th>
                <th className="p-3">ID Incidente</th>
                <th className="p-3">Acción</th>
                <th className="p-3">Objetivo</th>
                <th className="p-3">Ejecutado Por</th>
                <th className="p-3">Estado</th>
                <th className="p-3 w-10"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {logs.map((log) => {
                const isExpanded = expandedId === log.id;
                return (
                  <React.Fragment key={log.id}>
                    <tr
                      onClick={() => toggleRow(log.id)}
                      className="hover:bg-gray-800/30 transition cursor-pointer"
                    >
                      <td className="p-3 text-gray-400">
                        {log.timestamp ? new Date(log.timestamp).toLocaleString() : "-"}
                      </td>
                      <td className="p-3 font-mono text-xs text-blue-400" title={log.incident_id}>
                        {log.incident_id.slice(0, 8)}...
                      </td>
                      <td className="p-3 font-semibold text-gray-200">{log.action_id}</td>
                      <td className="p-3 font-mono text-xs text-amber-300">{log.target}</td>
                      <td className="p-3 text-gray-400">{log.executed_by}</td>
                      <td className="p-3">
                        <span
                          className={`px-2.5 py-1 text-xs font-bold rounded-full ${
                            log.status === "SUCCESS"
                              ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                              : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                          }`}
                        >
                          {log.status}
                        </span>
                      </td>
                      <td className="p-3 text-gray-500">
                        {isExpanded ? (
                          <ChevronUp className="w-4 h-4" />
                        ) : (
                          <ChevronDown className="w-4 h-4" />
                        )}
                      </td>
                    </tr>

                    {isExpanded && (
                      <tr className="bg-gray-950/40">
                        <td colSpan={7} className="p-4">
                          <div className="space-y-3">
                            <div>
                              <div className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                                ID Incidente completo
                              </div>
                              <div className="font-mono text-xs text-blue-300 break-all">
                                {log.incident_id}
                              </div>
                            </div>

                            <div>
                              <div className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">
                                Notas de resolución:
                              </div>
                              {log.details && log.details.trim() ? (
                                <p className="text-sm text-gray-200 whitespace-pre-wrap bg-gray-900 border border-gray-800 rounded-lg p-3">
                                  {log.details}
                                </p>
                              ) : (
                                <p className="text-xs text-gray-600 italic">Sin notas adicionales</p>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
