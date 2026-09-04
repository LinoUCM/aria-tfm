"use client";

import { useState } from "react";
import { Check, Loader2, X, Sparkles } from "lucide-react";
import { resolveIncident } from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";

interface ResolveIncidentModalProps {
  isOpen: boolean;
  incidentId: string | null;
  incidentTitle?: string;
  onClose: () => void;
  onSuccess: (incidentId: string) => void;
}

export function ResolveIncidentModal({
  isOpen,
  incidentId,
  incidentTitle,
  onClose,
  onSuccess,
}: ResolveIncidentModalProps) {
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen || !incidentId) return null;

  const currentUser = getCurrentUser();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!notes.trim()) return;

    setLoading(true);
    setError(null);

    try {
      await resolveIncident(incidentId, notes);

      setNotes("");
      onSuccess(incidentId);
      onClose();
    } catch (err: any) {
      setError(err.message || "Error conectando con ARIA");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg bg-gray-900 border border-gray-800 rounded-xl p-6 shadow-2xl space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-800 pb-3">
          <div className="flex items-center gap-2 text-white font-semibold text-base">
            <Sparkles className="w-4 h-4 text-blue-400" />
            <span>Resolver Incidente & Feedback RAG</span>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white p-1">
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-xs text-gray-400">
          Incidente: <span className="font-mono text-gray-200">{incidentTitle || incidentId}</span>
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-300 mb-1.5">
              ¿Cómo resolviste el problema? (Notas para el auto-aprendizaje de ARIA)
            </label>
            <textarea
              required
              rows={4}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Ej: Se reinició el contenedor de Redis, se eliminaron claves corruptas y se ajustó el límite de memoria a 2GB."
              className="w-full bg-gray-950 border border-gray-800 rounded-lg p-3 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-300 mb-1.5">
              Ingeniero / Operador
            </label>
            <div className="w-full bg-gray-950 border border-gray-800 rounded-lg p-2.5 text-xs text-gray-400">
              {currentUser?.full_name || currentUser?.username || "Usuario no identificado"}
            </div>
          </div>

          {error && (
            <div className="p-2.5 rounded bg-red-900/30 border border-red-800/50 text-xs text-red-300">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 rounded-lg text-xs font-medium text-gray-400 hover:bg-gray-800 transition"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white transition disabled:opacity-50"
            >
              {loading ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" /> Sintetizando & Indexando...
                </>
              ) : (
                <>
                  <Check className="w-3.5 h-3.5" /> Resolver y Vectorizar
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}