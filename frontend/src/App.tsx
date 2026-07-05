import React from "react";
import { useWebSocket } from "./hooks/useWebSocket";
import { ControlPanel } from "./components/ControlPanel";
import { MetricGrid } from "./components/MetricGrid";
import { ScrollingLog } from "./components/ScrollingLog";
import { Activity, ShieldAlert, BookOpen } from "lucide-react";

const App: React.FC = () => {
  const {
    connectionStatus,
    metrics,
    status,
    eventLog,
    startReplay,
    pauseReplay,
    stopReplay,
    stepReplay,
    setReplaySpeed,
  } = useWebSocket("ws://localhost:8000/ws");

  return (
    <div className="min-h-screen bg-[#07090e] bg-gradient-to-br from-[#07090e] via-[#0c0f1d] to-[#0a0c14] text-slate-100 p-6 font-sans">
      {/* 1. Header Section */}
      <header className="max-w-7xl mx-auto flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 mb-6 border-b border-slate-900">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-lg bg-cyan-500/10 border border-cyan-500/20 text-cyan-400">
            <Activity className="w-6 h-6 animate-pulse" />
          </div>
          <div>
            <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
              Trade Engine <span className="text-xs px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 uppercase font-semibold">Phase 1</span>
            </h1>
            <p className="text-xs text-slate-500">Market Data Pipeline Ingestion Dashboard</p>
          </div>
        </div>
        
        {/* Status panel */}
        <div className="flex items-center gap-2 text-xs">
          {connectionStatus !== "connected" && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-950/20 border border-rose-900/30 text-rose-400 font-medium">
              <ShieldAlert className="w-3.5 h-3.5" /> Disconnected from server (Retrying...)
            </div>
          )}
          <a
            href="file:///C:/Users/ROY/.gemini/antigravity-ide/brain/94ccaefa-b930-4872-b0bf-1ce737573d11/implementation_plan.md"
            target="_blank"
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-850 hover:bg-slate-800 transition text-slate-400"
          >
            <BookOpen className="w-3.5 h-3.5" /> Implementation Plan
          </a>
        </div>
      </header>

      <main className="max-w-7xl mx-auto flex flex-col gap-6">
        
        {/* 2. System metrics Grid */}
        <MetricGrid 
          connectionStatus={connectionStatus} 
          metrics={metrics} 
          status={status} 
        />

        {/* 3. Replay Controllers */}
        <ControlPanel
          status={status}
          startReplay={startReplay}
          pauseReplay={pauseReplay}
          stopReplay={stopReplay}
          stepReplay={stepReplay}
          setReplaySpeed={setReplaySpeed}
        />

        {/* 4. Live Event Log Section */}
        <div className="flex flex-col lg:flex-row gap-6">
          
          {/* Main event logger table */}
          <ScrollingLog events={eventLog} />
          
          {/* Sidebar / Quick status */}
          <div className="lg:w-80 flex flex-col gap-6">
            
            {/* Active Symbol Display */}
            <div className="bg-slate-900/60 backdrop-blur-md border border-slate-800 rounded-xl p-5 shadow-lg">
              <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Ingress Ticker Snapshot</h4>
              {status?.last_symbol ? (
                <div className="flex flex-col gap-4">
                  <div>
                    <div className="text-[10px] text-slate-500 font-semibold uppercase">Active Symbol</div>
                    <div className="text-3xl font-extrabold text-cyan-400 tracking-tight">{status.last_symbol}</div>
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <div className="text-[10px] text-slate-500 font-semibold uppercase">Last Price (LTP)</div>
                      <div className="text-xl font-bold text-slate-200">{status.last_price.toLocaleString("en-US", { minimumFractionDigits: 2 })}</div>
                    </div>
                    <div>
                      <div className="text-[10px] text-slate-500 font-semibold uppercase">Total Ticks</div>
                      <div className="text-xl font-bold text-slate-200">{status.packets_processed.toLocaleString()}</div>
                    </div>
                  </div>
                  <div className="pt-3 border-t border-slate-850 text-[10px] text-slate-500 font-mono">
                    <div>Source Clock:</div>
                    <div className="text-slate-350">{status.last_timestamp ? status.last_timestamp : "N/A"}</div>
                  </div>
                </div>
              ) : (
                <div className="text-center py-6 text-slate-500 text-xs font-sans">
                  No symbol currently active
                </div>
              )}
            </div>

            {/* Platform Specifications */}
            <div className="bg-slate-900/60 backdrop-blur-md border border-slate-850 rounded-xl p-5 text-xs text-slate-400 flex-1 flex flex-col justify-between">
              <div>
                <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Phase 1 Scope</h4>
                <ul className="space-y-2 list-disc list-inside text-slate-400 font-sans">
                  <li>Decoupled Parquet Logs (Bronze)</li>
                  <li>DuckDB Silver Table (Silver)</li>
                  <li>Multi-mode Replay Engine</li>
                  <li>Prioritized Async Event Bus</li>
                  <li>Sub-millisecond Pipeline Latency</li>
                </ul>
              </div>
              <div className="mt-4 pt-3 border-t border-slate-850 text-[10px] text-slate-500 text-center font-mono">
                System Status: operational
              </div>
            </div>

          </div>
        </div>
      </main>
    </div>
  );
};

export default App;
