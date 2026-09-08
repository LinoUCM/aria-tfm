"use client";

import { useState, useEffect } from "react";
import { getPresets, firePreset, fireCustomAlert, errorMessage, Preset, CustomAlertPayload } from "@/lib/api";
import toast from "react-hot-toast";
import { clsx } from "clsx";
import { Zap, Play, Edit3, CheckCircle, Loader2 } from "lucide-react";

const PRIORITY_CONFIG = {
  P1: { color: "text-red-400", bg: "bg-red-900/20", border: "border-red-800", dot: "bg-red-400" },
  P2: { color: "text-orange-400", bg: "bg-orange-900/20", border: "border-orange-800", dot: "bg-orange-400" },
  P3: { color: "text-yellow-400", bg: "bg-yellow-900/20", border: "border-yellow-800", dot: "bg-yellow-400" },
  P4: { color: "text-blue-400", bg: "bg-blue-900/20", border: "border-blue-800", dot: "bg-blue-400" },
};

const DEFAULT_CUSTOM_PAYLOAD = {
  title: "[P2] Custom alert — my-service",
  text: "Custom alert description for testing purposes.",
  alert_type: "error",
  priority: "P2",
  host: "my-service-prod-01",
  tags: ["service:my-service", "env:production"],
  metrics: {
    error_rate: 5.2,
    p99_latency_ms: 2300,
  },
};

export default function SimulatorPage() {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [firingId, setFiringId] = useState<string | null>(null);
  const [firedAlerts, setFiredAlerts] = useState<Array<{ id: string; title: string; incident_id: string; time: Date }>>([]);
  const [activeTab, setActiveTab] = useState<"presets" | "custom">("presets");
  const [customPayload, setCustomPayload] = useState(JSON.stringify(DEFAULT_CUSTOM_PAYLOAD, null, 2));
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);

  useEffect(() => {
    getPresets()
      .then(setPresets)
      .catch(() => toast.error("Failed to load presets"))
      .finally(() => setIsLoading(false));
  }, []);

  const handleFirePreset = async (presetKey: string, presetTitle: string) => {
    setFiringId(presetKey);
    try {
      const result = await firePreset(presetKey);
      toast.success(`Alert fired! Incident created.`);
      setFiredAlerts((prev) => [
        { id: presetKey, title: presetTitle, incident_id: result.incident_id, time: new Date() },
        ...prev,
      ]);
    } catch (err) {
      toast.error(errorMessage(err, "Failed to fire alert"));
    } finally {
      setFiringId(null);
    }
  };

  const handleFireCustom = async () => {
    setFiringId("custom");
    try {
      const payload = JSON.parse(customPayload) as CustomAlertPayload;
      const result = await fireCustomAlert(payload);
      toast.success("Custom alert fired!");
      setFiredAlerts((prev) => [
        { id: "custom", title: payload.title, incident_id: result.incident_id, time: new Date() },
        ...prev,
      ]);
    } catch (err) {
      if (err instanceof SyntaxError) {
        toast.error("Invalid JSON payload");
      } else {
        toast.error(errorMessage(err, "Failed to fire alert"));
      }
    } finally {
      setFiringId(null);
    }
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white flex items-center gap-3">
          <Zap className="w-7 h-7 text-yellow-400" />
          Datadog Alert Simulator
        </h1>
        <p className="text-gray-400 text-sm mt-1">
          Simulate Datadog webhook alerts to test ARIA&apos;s incident analysis pipeline
        </p>
      </div>

      {/* How it works */}
      <div className="bg-blue-950/20 border border-blue-900/30 rounded-xl p-4">
        <h3 className="text-blue-400 font-medium text-sm mb-2">How this works</h3>
        <div className="flex items-center gap-2 text-xs text-gray-400">
          {["Select an alert preset", "Click Fire Alert", "ARIA analyzes automatically", "View result in Incidents"].map((step, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="w-5 h-5 bg-blue-900/50 text-blue-400 rounded-full flex items-center justify-center flex-shrink-0 text-xs font-bold">
                {i + 1}
              </span>
              <span>{step}</span>
              {i < 3 && <span className="text-gray-600">→</span>}
            </div>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-900 rounded-lg p-1 w-fit">
        {[
          { key: "presets", label: "Alert Presets" },
          { key: "custom", label: "Custom Payload" },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key as "presets" | "custom")}
            className={clsx(
              "px-4 py-2 rounded-md text-sm transition-colors",
              activeTab === tab.key
                ? "bg-blue-600 text-white"
                : "text-gray-400 hover:text-gray-200"
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "presets" ? (
        <div className="grid grid-cols-2 gap-4">
          {isLoading ? (
            <div className="col-span-2 flex justify-center py-12">
              <Loader2 className="w-6 h-6 animate-spin text-blue-400" />
            </div>
          ) : (
            presets.map((preset) => {
              const pConfig = PRIORITY_CONFIG[preset.priority as keyof typeof PRIORITY_CONFIG] || PRIORITY_CONFIG.P3;
              const isFiring = firingId === preset.key;
              const wasSelected = selectedPreset === preset.key;

              return (
                <div
                  key={preset.key}
                  className={clsx(
                    "border rounded-xl p-4 cursor-pointer transition-all",
                    wasSelected
                      ? `${pConfig.border} ${pConfig.bg}`
                      : "border-gray-800 bg-gray-900 hover:border-gray-600"
                  )}
                  onClick={() => setSelectedPreset(preset.key)}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-2">
                        <div className={clsx("w-2 h-2 rounded-full flex-shrink-0", pConfig.dot)} />
                        <span className={clsx("text-xs font-bold", pConfig.color)}>{preset.priority}</span>
                        <span className="text-xs text-gray-500">{preset.alert_type}</span>
                      </div>
                      <h3 className="text-white text-sm font-medium leading-snug">{preset.title}</h3>
                      <div className="mt-2 flex items-center gap-2">
                        <span className="text-xs px-2 py-0.5 bg-gray-800 text-gray-400 rounded-full">
                          {preset.service}
                        </span>
                      </div>
                    </div>
                  </div>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleFirePreset(preset.key, preset.title);
                    }}
                    disabled={isFiring}
                    className={clsx(
                      "mt-4 w-full flex items-center justify-center gap-2 py-2 rounded-lg text-sm font-medium transition-all",
                      isFiring
                        ? "bg-gray-800 text-gray-500 cursor-not-allowed"
                        : "bg-blue-600 hover:bg-blue-500 text-white"
                    )}
                  >
                    {isFiring ? (
                      <><Loader2 className="w-4 h-4 animate-spin" /> Firing...</>
                    ) : (
                      <><Play className="w-4 h-4" /> Fire Alert</>
                    )}
                  </button>
                </div>
              );
            })
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-3">
              <Edit3 className="w-4 h-4 text-gray-400" />
              <h3 className="text-white text-sm font-medium">Custom Payload (JSON)</h3>
            </div>
            <textarea
              value={customPayload}
              onChange={(e) => setCustomPayload(e.target.value)}
              rows={18}
              className="w-full bg-gray-950 border border-gray-700 rounded-lg p-4 text-sm text-green-400 font-mono outline-none focus:border-blue-500 resize-none"
              spellCheck={false}
            />
          </div>
          <button
            onClick={handleFireCustom}
            disabled={firingId === "custom"}
            className="flex items-center gap-2 px-6 py-3 bg-yellow-600 hover:bg-yellow-500 disabled:opacity-50 text-white rounded-lg font-medium transition-colors"
          >
            {firingId === "custom" ? (
              <><Loader2 className="w-4 h-4 animate-spin" /> Firing...</>
            ) : (
              <><Zap className="w-4 h-4" /> Fire Custom Alert</>
            )}
          </button>
        </div>
      )}

      {/* Fired alerts log */}
      {firedAlerts.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-gray-400">Recent alerts fired</h3>
          {firedAlerts.slice(0, 5).map((alert, i) => (
            <div key={i} className="flex items-center gap-3 bg-green-900/10 border border-green-900/30 rounded-lg px-4 py-2">
              <CheckCircle className="w-4 h-4 text-green-400 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-white truncate">{alert.title}</p>
                <p className="text-xs text-gray-500">
                  Incident: {alert.incident_id.slice(0, 8)}... · {alert.time.toLocaleTimeString()}
                </p>
              </div>
              <a
                href="/incidents"
                className="text-xs text-blue-400 hover:text-blue-300 flex-shrink-0"
              >
                View →
              </a>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
