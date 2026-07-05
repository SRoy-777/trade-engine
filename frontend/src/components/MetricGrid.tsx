import React from "react";
import { Network, Database, Cpu, Timer } from "lucide-react";
import type { TelemetryMetrics, ProviderStatus } from "../hooks/useWebSocket";

interface MetricGridProps {
  connectionStatus: "connecting" | "connected" | "disconnected";
  metrics: TelemetryMetrics | null;
  status: ProviderStatus | null;
}

export const MetricGrid: React.FC<MetricGridProps> = ({
  connectionStatus,
  metrics,
  status,
}) => {
  const getStatusColor = (s: string) => {
    switch (s) {
      case "RUNNING":
        return "text-emerald-400 bg-emerald-950/30 border-emerald-900/50";
      case "PAUSED":
        return "text-yellow-400 bg-yellow-950/30 border-yellow-900/50";
      default:
        return "text-slate-400 bg-slate-950/30 border-slate-900/50";
    }
  };

  const getConnStatusColor = (s: string) => {
    switch (s) {
      case "connected":
        return "text-emerald-400 bg-emerald-950/30 border-emerald-900/50";
      case "connecting":
        return "text-yellow-400 bg-yellow-950/30 border-yellow-900/50";
      default:
        return "text-rose-400 bg-rose-950/30 border-rose-900/50";
    }
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
      
      {/* 1. Connection & Provider Card */}
      <div className="bg-slate-900/60 backdrop-blur-md border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col justify-between">
        <div className="flex items-center justify-between mb-4">
          <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Gateway Connection</span>
          <Network className="w-5 h-5 text-cyan-400" />
        </div>
        <div>
          <div className="flex items-center gap-2 mb-2">
            <span className={`px-2.5 py-1 text-xs font-bold rounded-full border ${getConnStatusColor(connectionStatus)}`}>
              WS: {connectionStatus.toUpperCase()}
            </span>
            <span className="text-slate-200 font-medium text-sm">
              Provider: <span className="text-cyan-400 capitalize">{status?.provider_name ?? "None"}</span>
            </span>
          </div>
          <p className="text-xs text-slate-500 font-mono truncate" title={status?.session_id ?? "N/A"}>
            Session: {status?.session_id ? status.session_id.substring(8) : "N/A"}
          </p>
        </div>
      </div>

      {/* 2. Replay Controller Metrics */}
      <div className="bg-slate-900/60 backdrop-blur-md border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col justify-between">
        <div className="flex items-center justify-between mb-4">
          <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Replay Engine Status</span>
          <Timer className="w-5 h-5 text-indigo-400" />
        </div>
        <div>
          <div className="flex items-center gap-2 mb-2">
            <span className={`px-2.5 py-1 text-xs font-bold rounded-full border ${getStatusColor(status?.provider_status ?? "STOPPED")}`}>
              {status?.provider_status ?? "STOPPED"}
            </span>
            <span className="text-slate-400 text-xs">
              Mode: <span className="text-indigo-300 font-medium">{status?.mode ?? "N/A"}</span>
            </span>
          </div>
          <div className="flex justify-between items-center text-xs text-slate-400">
            <span>Elapsed: {status?.elapsed_time_secs ?? 0}s</span>
            <span>Speed: {status?.mode === "MAX" ? "Max" : `${status?.speed ?? 1.0}x`}</span>
          </div>
        </div>
      </div>

      {/* 3. Pipeline Telemetry */}
      <div className="bg-slate-900/60 backdrop-blur-md border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col justify-between">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Pipeline Throughput</span>
          <Cpu className="w-5 h-5 text-emerald-400" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <div className="text-lg font-bold text-emerald-400">{metrics?.packets_per_sec ?? 0.0}</div>
            <div className="text-[10px] text-slate-500 uppercase font-semibold">Packets/sec</div>
          </div>
          <div>
            <div className="text-lg font-bold text-teal-400">{metrics?.events_per_sec ?? 0.0}</div>
            <div className="text-[10px] text-slate-500 uppercase font-semibold">Events/sec</div>
          </div>
        </div>
        <div className="mt-3 pt-2 border-t border-slate-800 flex justify-between text-[11px] text-slate-400 font-mono">
          <span>Parser: {metrics?.avg_parser_time_ms ?? 0.0}ms</span>
          <span>Bus: {metrics?.avg_event_bus_time_ms ?? 0.0}ms</span>
        </div>
      </div>

      {/* 4. Storage Ingestion Counters */}
      <div className="bg-slate-900/60 backdrop-blur-md border border-slate-800 rounded-xl p-5 shadow-lg flex flex-col justify-between">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Database Ingest</span>
          <Database className="w-5 h-5 text-teal-400" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <div className="text-sm font-semibold text-slate-200">{metrics?.total_packets ?? 0}</div>
            <div className="text-[9px] text-slate-500 uppercase font-semibold">Bronze (Raw)</div>
          </div>
          <div>
            <div className="text-sm font-semibold text-cyan-400">{metrics?.total_inserts ?? 0}</div>
            <div className="text-[9px] text-slate-500 uppercase font-semibold">Silver (DB)</div>
          </div>
        </div>
        <div className="mt-2 pt-1 border-t border-slate-850 flex justify-between text-[10px] text-slate-400 font-mono">
          <span>Buf (B/S): {metrics?.bronze_buffer_size ?? 0}/{metrics?.silver_buffer_size ?? 0}</span>
          <span className="text-rose-400">Drift: {metrics?.replay_delay_secs ?? 0}s</span>
        </div>
      </div>

    </div>
  );
};
