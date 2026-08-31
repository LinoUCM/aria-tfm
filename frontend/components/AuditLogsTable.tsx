"use client";

import React, { useEffect, useState } from "react";
import { fetchAuditLogs, AuditLog } from "@/lib/api";

export default function AuditLogsTable() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState<boolean>(true);

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
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {logs.map((log) => (
                <tr key={log.id} className="hover:bg-gray-800/30 transition">
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}