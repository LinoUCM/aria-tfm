import React from 'react';

interface TopologyMapProps {
  affectedService: string;
  rootCause?: string;
}

interface NodeConfig {
  id: string;
  label: string;
  sublabel: string;
  x: number;
  y: number;
}

export const TopologyMap: React.FC<TopologyMapProps> = ({
  affectedService = 'redis-cache',
  rootCause
}) => {
  // Coordenadas fijas para dibujar el árbol de dependencias
  const nodes: NodeConfig[] = [
    { id: 'web-frontend', label: 'Web Frontend', sublabel: 'React App', x: 40, y: 85 },
    { id: 'api-gateway', label: 'API Gateway', sublabel: 'FastAPI / Envoy', x: 240, y: 85 },
    { id: 'payment-service', label: 'payment-service', sublabel: 'Microservice', x: 460, y: 35 },
    { id: 'notification-service', label: 'notification-service', sublabel: 'Microservice', x: 460, y: 85 },
    { id: 'checkout-service', label: 'checkout-service', sublabel: 'Microservice', x: 460, y: 135 },
    { id: 'aria_db', label: 'aria_db', sublabel: 'PostgreSQL DB', x: 680, y: 35 },
    { id: 'redis-cache', label: 'redis-cache', sublabel: 'Redis Cluster', x: 680, y: 135 },
  ];

  // Conexiones del grafo (origen -> destino)
  const connections = [
    { from: 'web-frontend', to: 'api-gateway' },
    { from: 'api-gateway', to: 'payment-service' },
    { from: 'api-gateway', to: 'notification-service' },
    { from: 'api-gateway', to: 'checkout-service' },
    { from: 'payment-service', to: 'aria_db' },
    { from: 'checkout-service', to: 'redis-cache' },
  ];

  // Función para determinar el estado visual del nodo
  const getNodeState = (id: string) => {
    const normalizedId = id.toLowerCase();
    const normalizedAffected = (affectedService || '').toLowerCase();
    const normalizedRoot = (rootCause || '').toLowerCase();

    // Comprobaciones flexibles según coincidencias en el nombre
    if (normalizedRoot && (normalizedId === normalizedRoot || normalizedRoot.includes(normalizedId) || normalizedId.includes(normalizedRoot))) {
      return 'root_cause';
    }
    if (normalizedAffected && (normalizedId === normalizedAffected || normalizedAffected.includes(normalizedId) || normalizedId.includes(normalizedAffected))) {
      return 'affected';
    }
    return 'healthy';
  };

  return (
    <div className="w-full bg-slate-950/90 border border-slate-800/80 rounded-xl p-4 my-3 overflow-hidden shadow-inner">
      {/* Cabecera y Leyenda */}
      <div className="flex items-center justify-between mb-3 border-b border-slate-800/60 pb-2">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-cyan-500 animate-pulse" />
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-300">
            System Dependency Topology
          </h4>
        </div>
        <div className="flex items-center gap-4 text-[11px] text-slate-400">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-slate-600" /> Normal
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-amber-500" /> Affected
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-red-500 animate-ping" /> Root Cause
          </span>
        </div>
      </div>

      {/* Gráfico SVG interactivo y adaptativo */}
      <svg viewBox="0 0 840 200" className="w-full h-auto min-w-[650px] overflow-visible">
        <defs>
          <marker id="arrow-default" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569" />
          </marker>
          <marker id="arrow-critical" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#ef4444" />
          </marker>
        </defs>

        {/* Líneas de Conexión */}
        {connections.map((conn, idx) => {
          const source = nodes.find(n => n.id === conn.from)!;
          const target = nodes.find(n => n.id === conn.to)!;
          
          const sourceState = getNodeState(source.id);
          const targetState = getNodeState(target.id);
          const isCritical = sourceState !== 'healthy' || targetState !== 'healthy';

          return (
            <line
              key={idx}
              x1={source.x + 120}
              y1={source.y + 20}
              x2={target.x}
              y2={target.y + 20}
              stroke={isCritical ? '#ef4444' : '#334155'}
              strokeWidth={isCritical ? 2.5 : 1.5}
              strokeDasharray={isCritical ? '4,4' : 'none'}
              markerEnd={isCritical ? 'url(#arrow-critical)' : 'url(#arrow-default)'}
              className={isCritical ? 'animate-pulse' : ''}
            />
          );
        })}

        {/* Nodos de la Red */}
        {nodes.map((node) => {
          const state = getNodeState(node.id);

          let strokeColor = '#334155'; // slate-700
          let fillColor = '#0f172a';   // slate-900
          let textColor = '#cbd5e1';   // slate-300
          let badge = null;

          if (state === 'root_cause') {
            strokeColor = '#ef4444'; // red-500
            fillColor = '#450a0a';   // red-950
            textColor = '#fca5a5';   // red-300
            badge = 'ROOT CAUSE';
          } else if (state === 'affected') {
            strokeColor = '#f59e0b'; // amber-500
            fillColor = '#451a03';   // amber-950
            textColor = '#fde68a';   // amber-200
            badge = 'AFFECTED';
          }

          return (
            <g key={node.id} transform={`translate(${node.x}, ${node.y})`}>
              {/* Animación de halo exterior en Causa Raíz */}
              {state === 'root_cause' && (
                <rect x="-2" y="-2" width="124" height="44" rx="10" fill="none" stroke="#ef4444" strokeWidth="2" className="animate-ping opacity-75" />
              )}

              {/* Caja principal del nodo */}
              <rect x="0" y="0" width="120" height="40" rx="8" fill={fillColor} stroke={strokeColor} strokeWidth="1.5" />

              {/* Etiqueta del Servicio */}
              <text x="10" y="18" fill={textColor} fontSize="11" fontWeight="600" fontFamily="sans-serif">
                {node.label}
              </text>

              {/* Subtipo de Infraestructura */}
              <text x="10" y="31" fill="#64748b" fontSize="9" fontFamily="sans-serif">
                {node.sublabel}
              </text>

              {/* Tag / Badge flotante */}
              {badge && (
                <g transform="translate(60, -8)">
                  <rect x="0" y="0" width="55" height="14" rx="4" fill={state === 'root_cause' ? '#dc2626' : '#d97706'} />
                  <text x="27.5" y="10" fill="#ffffff" fontSize="7" fontWeight="bold" textAnchor="middle" fontFamily="sans-serif">
                    {badge}
                  </text>
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
};