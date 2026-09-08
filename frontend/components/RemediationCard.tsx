import React, { useState } from 'react';
import { executeRemediation, errorMessage } from '@/lib/api';

interface RemediationProps {
  incidentId: string;
  suggestedAction?: string | null;
  onResolved: () => void;
}

// Playbooks autorizados, ahora seleccionados por el campo estructurado
// suggested_action que devuelve el backend (ver orchestrator.py), no por
// coincidencia de texto libre (pendiente 8.1).
const PLAYBOOKS: Record<string, { actionId: string; title: string; target: string; risk: string; params: Record<string, string> }> = {
  FLUSH_REDIS_CACHE: {
    actionId: "FLUSH_REDIS_CACHE",
    title: "Flush Redis Cache",
    target: "aria-redis-1",
    risk: "LOW",
    params: { container_name: "aria-redis-1" }
  },
  RESTART_CONTAINER: {
    actionId: "RESTART_CONTAINER",
    title: "Restart PostgreSQL",
    target: "aria-postgres-1",
    risk: "MEDIUM",
    params: { container_name: "aria-postgres-1" }
  },
  TERMINATE_IDLE_CONNECTIONS: {
    actionId: "TERMINATE_IDLE_CONNECTIONS",
    title: "Terminate Idle Connections",
    target: "aria_db",
    risk: "LOW",
    params: { database_name: "aria_db" }
  },
};

export const RemediationCard: React.FC<RemediationProps> = ({ incidentId, suggestedAction, onResolved }) => {
  const playbook = suggestedAction ? PLAYBOOKS[suggestedAction] : undefined;
  const [loading, setLoading] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [resultMessage, setResultMessage] = useState<string | null>(null);

  if (!playbook) return null;

  const handleExecute = async () => {
    setLoading(true);
    try {
      const data = await executeRemediation(
        incidentId,
        playbook.actionId,
        playbook.params
      );
      setResultMessage(`✅ ${data.message}`);
      setTimeout(() => {
        setShowModal(false);
        onResolved();
      }, 1500);
    } catch (err) {
      if (err instanceof TypeError) {
        setResultMessage("❌ Failed to connect to the backend.");
      } else {
        setResultMessage(`❌ Error: ${errorMessage(err)}`);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mt-4 p-4 rounded-lg bg-emerald-950/30 border border-emerald-500/40 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
      <div>
        <div className="flex items-center gap-2">
          <span className="text-emerald-400 font-bold text-sm">⚡ Suggested Remediation</span>
          <span className="text-xs px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">
            Risk: {playbook.risk}
          </span>
        </div>
        <p className="text-gray-300 text-sm mt-1">
          Action: <span className="font-mono text-emerald-300">{playbook.title}</span> on target <code className="text-gray-200 bg-gray-800 px-1 py-0.5 rounded">{playbook.target}</code>
        </p>
      </div>

      <button
        onClick={() => setShowModal(true)}
        className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold rounded-md shadow-lg transition-all flex items-center gap-2"
      >
        <span>⚡ Run 1-Click Remediation</span>
      </button>

      {/* Modal Human-in-the-Loop */}
      {showModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 max-w-md w-full shadow-2xl">
            <h3 className="text-lg font-bold text-white mb-2">Confirm Remediation</h3>
            <p className="text-gray-300 text-sm mb-4">
              Do you want to automatically run the action <strong className="text-emerald-400">{playbook.title}</strong> on the container <code className="bg-gray-800 px-1.5 py-0.5 rounded text-emerald-300">{playbook.target}</code>?
            </p>

            {resultMessage && (
              <div className="mb-4 p-3 bg-gray-800 rounded border border-gray-700 text-xs font-mono text-gray-200">
                {resultMessage}
              </div>
            )}

            <div className="flex justify-end gap-3">
              <button
                disabled={loading}
                onClick={() => setShowModal(false)}
                className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-md"
              >
                Cancel
              </button>
              <button
                disabled={loading}
                onClick={handleExecute}
                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-semibold rounded-md flex items-center gap-2"
              >
                {loading ? "Running..." : "Confirm & Run"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};