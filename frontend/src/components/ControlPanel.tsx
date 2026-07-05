import React from "react";
import { Play, Pause, Square, SkipForward, FastForward } from "lucide-react";
import type { ProviderStatus } from "../hooks/useWebSocket";

interface ControlPanelProps {
  status: ProviderStatus | null;
  startReplay: () => void;
  pauseReplay: () => void;
  stopReplay: () => void;
  stepReplay: () => void;
  setReplaySpeed: (speed: number) => void;
  startLiveStrategy: (symbol: string, capital: number, profit: number, ticks: number) => void;
  stopLiveStrategy: () => void;
}

export const ControlPanel: React.FC<ControlPanelProps> = ({
  status,
  startReplay,
  pauseReplay,
  stopReplay,
  stepReplay,
  setReplaySpeed,
  startLiveStrategy,
  stopLiveStrategy,
}) => {
  const currentSpeed = status?.speed ?? 1.0;
  const currentStatus = status?.provider_status ?? "STOPPED";
  const currentMode = status?.mode ?? "MULTIPLIER";

  const [symbol, setSymbol] = React.useState("TATAMOTORS");
  const [capital, setCapital] = React.useState(60000);
  const [profit, setProfit] = React.useState(500);
  const [ticks, setTicks] = React.useState(100);

  const speeds = [
    { label: "1x Speed", value: 1.0 },
    { label: "2x Speed", value: 2.0 },
    { label: "5x Speed", value: 5.0 },
    { label: "Max Speed", value: 0.0 },
  ];

  return (
    <div className="bg-slate-900/60 backdrop-blur-md border border-slate-800 rounded-xl p-6 shadow-xl">
      <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">Replay Engine Controller</h3>
      
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-6">
        {/* Playback buttons */}
        <div className="flex items-center gap-3">
          {currentStatus === "RUNNING" && currentMode !== "LIVE_STRATEGY" ? (
            <button
              onClick={pauseReplay}
              className="flex items-center gap-2 px-5 py-2.5 bg-yellow-600 hover:bg-yellow-500 active:scale-95 transition text-white font-medium rounded-lg shadow-md cursor-pointer"
            >
              <Pause className="w-4 h-4" /> Pause
            </button>
          ) : (
            <button
              onClick={startReplay}
              disabled={currentMode === "LIVE_STRATEGY"}
              className="flex items-center gap-2 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 disabled:pointer-events-none active:scale-95 transition text-white font-medium rounded-lg shadow-md cursor-pointer"
            >
              <Play className="w-4 h-4" /> Start
            </button>
          )}

          <button
            onClick={stopReplay}
            disabled={currentStatus === "STOPPED" || currentMode === "LIVE_STRATEGY"}
            className="flex items-center gap-2 px-5 py-2.5 bg-rose-600/90 hover:bg-rose-500 disabled:opacity-40 disabled:pointer-events-none active:scale-95 transition text-white font-medium rounded-lg shadow-md cursor-pointer"
          >
            <Square className="w-4 h-4" /> Stop
          </button>

          <div className="h-8 w-px bg-slate-800 mx-2" />

          <button
            onClick={stepReplay}
            disabled={currentMode === "LIVE_STRATEGY"}
            className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:pointer-events-none active:scale-95 transition text-white font-medium rounded-lg shadow-md cursor-pointer"
            title="Switch to step mode and advance exactly 1 packet"
          >
            <SkipForward className="w-4 h-4" /> Step Tick
          </button>
        </div>

        {/* Speed Selector */}
        <div className="flex items-center gap-3">
          <span className="text-sm text-slate-400 flex items-center gap-1.5">
            <FastForward className="w-4 h-4 text-slate-500" /> Speed:
          </span>
          <div className="flex rounded-lg bg-slate-950 p-1 border border-slate-850">
            {speeds.map((s) => (
              <button
                key={s.value}
                disabled={currentMode === "LIVE_STRATEGY"}
                onClick={() => setReplaySpeed(s.value)}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition cursor-pointer ${
                  (currentMode === "MAX" && s.value === 0.0) || (currentMode !== "MAX" && currentMode !== "STEP" && currentSpeed === s.value)
                    ? "bg-slate-800 text-cyan-400 font-bold"
                    : "text-slate-400 hover:text-slate-250 disabled:opacity-30"
                }`}
              >
                {s.label.split(" ")[0]}
              </button>
            ))}
            <button
              disabled
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition select-none ${
                currentMode === "STEP" ? "bg-blue-900/40 text-blue-400 font-bold" : "hidden"
              }`}
            >
              Step Mode
            </button>
          </div>
        </div>
      </div>

      {/* Live Strategy Gateway */}
      <div className="mt-6 pt-6 border-t border-slate-800/80">
        <h3 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">Live Paper Trading Gateway</h3>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4 mb-5">
          <div>
            <label className="block text-[10px] uppercase font-bold text-slate-500 mb-1.5">Stock Symbol</label>
            <input
              type="text"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              className="w-full bg-slate-950 border border-slate-800 focus:border-cyan-500 rounded-lg px-3 py-2 text-xs font-semibold text-slate-200 outline-none transition"
              placeholder="e.g. TATAMOTORS, SBIN"
            />
          </div>
          <div>
            <label className="block text-[10px] uppercase font-bold text-slate-500 mb-1.5">Capital (INR)</label>
            <input
              type="number"
              value={capital}
              onChange={(e) => setCapital(Number(e.target.value))}
              className="w-full bg-slate-950 border border-slate-800 focus:border-cyan-500 rounded-lg px-3 py-2 text-xs font-semibold text-slate-200 outline-none transition"
            />
          </div>
          <div>
            <label className="block text-[10px] uppercase font-bold text-slate-500 mb-1.5">Target Profit (INR)</label>
            <input
              type="number"
              value={profit}
              onChange={(e) => setProfit(Number(e.target.value))}
              className="w-full bg-slate-950 border border-slate-800 focus:border-cyan-500 rounded-lg px-3 py-2 text-xs font-semibold text-slate-200 outline-none transition"
            />
          </div>
          <div>
            <label className="block text-[10px] uppercase font-bold text-slate-500 mb-1.5">Target Ticks (₹0.05/tick)</label>
            <input
              type="number"
              value={ticks}
              onChange={(e) => setTicks(Number(e.target.value))}
              className="w-full bg-slate-950 border border-slate-800 focus:border-cyan-500 rounded-lg px-3 py-2 text-xs font-semibold text-slate-200 outline-none transition"
            />
          </div>
        </div>

        <div className="flex gap-3">
          {currentMode === "LIVE_STRATEGY" && currentStatus === "RUNNING" ? (
            <button
              onClick={stopLiveStrategy}
              className="px-5 py-2.5 bg-rose-600 hover:bg-rose-500 active:scale-95 transition text-white font-medium rounded-lg shadow-md cursor-pointer text-xs flex items-center gap-2"
            >
              <Square className="w-3.5 h-3.5" /> Stop Live Strategy
            </button>
          ) : (
            <button
              onClick={() => startLiveStrategy(symbol, capital, profit, ticks)}
              className="px-5 py-2.5 bg-cyan-600 hover:bg-cyan-500 active:scale-95 transition text-white font-medium rounded-lg shadow-md cursor-pointer text-xs flex items-center gap-2"
            >
              <Play className="w-3.5 h-3.5" /> Start Live Strategy
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
