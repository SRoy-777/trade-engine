import React, { useState, useEffect } from "react";
import { 
  Play, 
  Save, 
  Plus, 
  Sliders, 
  Download, 
  CheckCircle, 
  Activity, 
  TrendingUp 
} from "lucide-react";

interface ResearchLabWorkspaceProps {
  symbols: string[];
  addToast: (message: string, type: "success" | "error" | "info" | "warning") => void;
}

interface StrategyItem {
  id: string;
  name: string;
}

export const ResearchLabWorkspace: React.FC<ResearchLabWorkspaceProps> = ({ symbols, addToast }) => {
  const [activeSubTab, setActiveSubTab] = useState<"edit" | "backtest" | "compare">("edit");
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [selectedStrategyId, setSelectedStrategyId] = useState<string>("orb");
  const [editorCode, setEditorCode] = useState<string>("");
  const [newStrategyName, setNewStrategyName] = useState<string>("");
  const [showNewStrategyInput, setShowNewStrategyInput] = useState<boolean>(false);
  const [isSavingCode, setIsSavingCode] = useState<boolean>(false);

  // Single Backtest States
  const [backtestParams, setBacktestParams] = useState({
    symbols: [] as string[],
    selectAllSymbols: true,
    timeframe: "5M",
    startDate: "2026-05-01",
    endDate: "2026-07-06",
    productType: "INTRADAY",
    leverage: 5,
    capital: 100000.0,
  });
  const [isBacktesting, setIsBacktesting] = useState<boolean>(false);
  const [backtestReport, setBacktestReport] = useState<Record<string, number> | null>(null);

  // Compare Strategies States
  const [compareParams, setCompareParams] = useState({
    selectedIds: [] as string[],
    symbols: [] as string[],
    selectAllSymbols: true,
    timeframe: "5M",
    startDate: "2026-05-01",
    endDate: "2026-07-06",
    productType: "INTRADAY",
    leverage: 5,
    capital: 100000.0,
  });
  const [isComparing, setIsComparing] = useState<boolean>(false);
  const [compareReports, setCompareReports] = useState<Record<string, Record<string, number>> | null>(null);

  // Fetch strategies list
  useEffect(() => {
    fetch("/api/v1/research/strategies")
      .then(res => res.json())
      .then(data => {
        if (Array.isArray(data)) {
          setStrategies(data);
          if (data.length > 0 && !selectedStrategyId) {
            setSelectedStrategyId(data[0].id);
          }
        }
      })
      .catch(err => console.error("Error loading strategies:", err));
  }, []);

  // Fetch strategy code when strategy selection changes
  useEffect(() => {
    if (!selectedStrategyId) return;
    fetch(`/api/v1/research/strategies/code?strategy_id=${selectedStrategyId}`)
      .then(res => res.json())
      .then(data => {
        if (data && typeof data.code === "string") {
          setEditorCode(data.code);
        }
      })
      .catch(err => console.error("Error loading strategy code:", err));
  }, [selectedStrategyId]);

  const handleSaveCode = () => {
    if (!selectedStrategyId) return;
    setIsSavingCode(true);
    fetch("/api/v1/research/strategies/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strategy_id: selectedStrategyId, code: editorCode })
    })
      .then(res => res.json())
      .then(data => {
        setIsSavingCode(false);
        if (data.status === "success") {
          addToast(`Strategy "${selectedStrategyId}" code saved successfully on disk`, "success");
        } else {
          addToast(`Failed to save strategy: ${data.detail || "Unknown error"}`, "error");
        }
      })
      .catch(err => {
        setIsSavingCode(false);
        addToast(`Server connection error: ${err}`, "error");
      });
  };

  const handleCreateNewStrategy = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newStrategyName.trim()) return;
    const cleanId = newStrategyName.toLowerCase().replace(/[^a-z0-9_]/g, "");
    if (!cleanId) {
      addToast("Invalid strategy name. Use alphanumeric characters and underscores only.", "error");
      return;
    }
    
    // Append to list locally
    const newItem = { id: cleanId, name: newStrategyName.trim() + ` (${cleanId}.py)` };
    setStrategies(prev => [...prev, newItem]);
    setSelectedStrategyId(cleanId);
    setNewStrategyName("");
    setShowNewStrategyInput(false);
    addToast(`Boilerplate template created for strategy: ${cleanId}`, "success");
  };

  const toggleBacktestSymbol = (sym: string) => {
    setBacktestParams(prev => {
      const current = [...prev.symbols];
      const idx = current.indexOf(sym);
      if (idx >= 0) {
        current.splice(idx, 1);
      } else {
        current.push(sym);
      }
      return { ...prev, symbols: current, selectAllSymbols: false };
    });
  };

  const toggleCompareSymbol = (sym: string) => {
    setCompareParams(prev => {
      const current = [...prev.symbols];
      const idx = current.indexOf(sym);
      if (idx >= 0) {
        current.splice(idx, 1);
      } else {
        current.push(sym);
      }
      return { ...prev, symbols: current, selectAllSymbols: false };
    });
  };

  const toggleCompareStrategySelection = (id: string) => {
    setCompareParams(prev => {
      const current = [...prev.selectedIds];
      const idx = current.indexOf(id);
      if (idx >= 0) {
        current.splice(idx, 1);
      } else {
        if (current.length >= 3) {
          addToast("You can select a maximum of 3 strategies for comparison", "warning");
          return prev;
        }
        current.push(id);
      }
      return { ...prev, selectedIds: current };
    });
  };

  const handleRunSingleBacktest = (e: React.FormEvent) => {
    e.preventDefault();
    setIsBacktesting(true);
    setBacktestReport(null);

    const activeSymbols = backtestParams.selectAllSymbols ? symbols : backtestParams.symbols;
    if (activeSymbols.length === 0) {
      addToast("Please select at least one stock symbol to run the backtest", "warning");
      setIsBacktesting(false);
      return;
    }

    fetch("/api/v1/research/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        strategy_id: selectedStrategyId,
        symbols: activeSymbols,
        timeframe: backtestParams.timeframe,
        start_date: backtestParams.startDate,
        end_date: backtestParams.endDate,
        product_type: backtestParams.productType,
        leverage: backtestParams.productType === "INTRADAY" ? backtestParams.leverage : 1,
        capital: backtestParams.capital,
      })
    })
      .then(res => res.json())
      .then(data => {
        setIsBacktesting(false);
        if (data && data.metrics) {
          setBacktestReport(data.metrics);
          addToast(`Historical backtest simulation for ${selectedStrategyId} completed`, "success");
        }
      })
      .catch(err => {
        setIsBacktesting(false);
        addToast(`Failed to run simulation: ${err}`, "error");
      });
  };

  const handleRunCompare = (e: React.FormEvent) => {
    e.preventDefault();
    if (compareParams.selectedIds.length === 0) {
      addToast("Please select at least one strategy to compare", "warning");
      return;
    }
    
    setIsComparing(true);
    setCompareReports(null);

    const activeSymbols = compareParams.selectAllSymbols ? symbols : compareParams.symbols;
    if (activeSymbols.length === 0) {
      addToast("Please select at least one stock symbol to compare", "warning");
      setIsComparing(false);
      return;
    }

    fetch("/api/v1/research/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        strategy_ids: compareParams.selectedIds,
        symbols: activeSymbols,
        timeframe: compareParams.timeframe,
        start_date: compareParams.startDate,
        end_date: compareParams.endDate,
        product_type: compareParams.productType,
        leverage: compareParams.productType === "INTRADAY" ? compareParams.leverage : 1,
        capital: compareParams.capital,
      })
    })
      .then(res => res.json())
      .then(data => {
        setIsComparing(false);
        if (data) {
          setCompareReports(data);
          addToast(`Comparative simulation completed across ${compareParams.selectedIds.length} strategies`, "success");
        }
      })
      .catch(err => {
        setIsComparing(false);
        addToast(`Failed to run comparative simulation: ${err}`, "error");
      });
  };

  const formatVal = (key: string, val: number) => {
    if (val === undefined || val === null) return "-";
    if (key.includes("(%)") || key.includes("Win Rate")) return `${val.toFixed(2)}%`;
    if (key.includes("(INR)") || key.includes("balance") || key.includes("Winner") || key.includes("Loser") || key.includes("Profit") || key.includes("Loss")) {
      return `₹${val.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }
    if (key.includes("Ratio") || key.includes("Factor")) return val.toFixed(2);
    if (key.includes("Holding Time")) return `${Math.round(val)} mins`;
    return val.toString();
  };

  return (
    <div className="space-y-6">
      
      {/* Page Title Header */}
      <div className="flex justify-between items-center pb-4 border-b border-slate-900">
        <div>
          <h2 className="text-xl font-extrabold text-white flex items-center gap-2 font-sans">
            <Activity className="w-5 h-5 text-cyan-400" /> Research Lab Workspace
          </h2>
          <p className="text-xs text-slate-500 mt-1 font-sans">
            Sandbox backtesting environment. Fully isolated from live indicators and execution logs.
          </p>
        </div>
      </div>

      {/* Sub Tabs Selection Header */}
      <div className="flex border-b border-slate-900 gap-1.5 p-1 bg-slate-950/65 rounded-xl max-w-lg">
        {[
          { id: "edit", label: "Create & Edit Strategy" },
          { id: "backtest", label: "Backtest Strategy" },
          { id: "compare", label: "Compare Strategies" }
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveSubTab(tab.id as any)}
            className={`flex-1 py-2 text-center text-xs font-extrabold rounded-lg transition duration-200 cursor-pointer ${
              activeSubTab === tab.id
                ? "bg-cyan-600 text-white shadow-md"
                : "text-slate-400 hover:bg-slate-900/60 hover:text-slate-200"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* SUB-VIEW 1: CREATE / EDIT STRATEGY CODE */}
      {activeSubTab === "edit" && (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          
          {/* Left panel: strategy selection list */}
          <div className="bg-slate-900/40 border border-slate-900 rounded-xl p-5 shadow-lg space-y-4 h-[600px] flex flex-col justify-between">
            <div className="space-y-4">
              <h3 className="text-xs font-black uppercase text-slate-500 tracking-wider">Strategies List</h3>
              
              <div className="space-y-1.5 overflow-y-auto max-h-[420px]">
                {strategies.map(s => (
                  <button
                    key={s.id}
                    onClick={() => setSelectedStrategyId(s.id)}
                    className={`w-full text-left px-3.5 py-2.5 rounded-lg text-xs font-bold transition flex items-center justify-between border cursor-pointer ${
                      selectedStrategyId === s.id
                        ? "bg-cyan-955/20 border-cyan-500/30 text-cyan-400 font-extrabold"
                        : "bg-slate-950/40 border-slate-900 text-slate-400 hover:text-slate-200 hover:bg-slate-900"
                    }`}
                  >
                    <span>{s.id.toUpperCase()}</span>
                    <span className="text-[10px] opacity-40 font-mono">.py</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="pt-4 border-t border-slate-900/60">
              {showNewStrategyInput ? (
                <form onSubmit={handleCreateNewStrategy} className="space-y-2.5">
                  <input
                    type="text"
                    required
                    value={newStrategyName}
                    onChange={e => setNewStrategyName(e.target.value)}
                    placeholder="Strategy ID (e.g. macd_cross)"
                    className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-200 outline-none focus:border-cyan-500"
                  />
                  <div className="flex gap-2">
                    <button
                      type="submit"
                      className="flex-1 py-1.5 bg-cyan-600 hover:bg-cyan-500 active:scale-95 text-white text-[10px] font-black rounded cursor-pointer"
                    >
                      Confirm
                    </button>
                    <button
                      type="button"
                      onClick={() => setShowNewStrategyInput(false)}
                      className="flex-1 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-350 text-[10px] font-bold rounded cursor-pointer"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              ) : (
                <button
                  onClick={() => setShowNewStrategyInput(true)}
                  className="w-full py-2 bg-slate-850 hover:bg-slate-800 active:scale-98 transition text-slate-300 text-xs font-black rounded-lg border border-slate-805 flex items-center justify-center gap-1.5 cursor-pointer"
                >
                  <Plus className="w-3.5 h-3.5" /> Create New Strategy
                </button>
              )}
            </div>
          </div>

          {/* Right panel: code text editor canvas */}
          <div className="lg:col-span-3 bg-slate-900/40 border border-slate-900 rounded-xl p-5 shadow-lg flex flex-col justify-between h-[600px]">
            <div className="space-y-3 flex-1 flex flex-col">
              <div className="flex justify-between items-center">
                <div>
                  <h3 className="text-xs font-black text-slate-200 uppercase">Python Strategy File Editor</h3>
                  <p className="text-[10px] text-slate-500 mt-0.5">Core callback file: {selectedStrategyId}.py</p>
                </div>
                <span className="text-[9px] px-2 py-0.5 bg-slate-950 border border-slate-850 rounded text-cyan-400 font-mono uppercase font-black">Sandboxed Space</span>
              </div>

              <div className="flex-1 bg-slate-955 border border-slate-850 rounded-xl overflow-hidden font-mono text-xs p-3.5 relative flex">
                <textarea
                  value={editorCode}
                  onChange={e => setEditorCode(e.target.value)}
                  className="w-full h-full bg-transparent text-slate-300 outline-none resize-none font-mono leading-relaxed"
                  style={{ whiteSpace: "pre", overflowWrap: "normal" }}
                />
              </div>
            </div>

            <div className="pt-4 flex justify-end gap-3">
              <button
                type="button"
                onClick={handleSaveCode}
                disabled={isSavingCode || !selectedStrategyId}
                className="px-5 py-2 bg-cyan-600 hover:bg-cyan-500 active:scale-95 disabled:opacity-40 transition text-white font-extrabold text-xs rounded-lg shadow-md cursor-pointer flex items-center gap-2"
              >
                <Save className="w-3.5 h-3.5" /> {isSavingCode ? "Saving Changes..." : "Save Strategy Code"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* SUB-VIEW 2: BACKTEST INDIVIDUAL STRATEGY */}
      {activeSubTab === "backtest" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          
          {/* Parameter formulation panel */}
          <div className="bg-slate-900/40 border border-slate-900 rounded-xl p-5 shadow-lg h-fit">
            <h3 className="text-xs font-black uppercase text-slate-200 tracking-wider mb-4">Backtest Configurations</h3>
            
            <form onSubmit={handleRunSingleBacktest} className="space-y-4">
              <div>
                <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Strategy Model</label>
                <select
                  value={selectedStrategyId}
                  onChange={e => setSelectedStrategyId(e.target.value)}
                  className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-300 outline-none focus:border-cyan-500 cursor-pointer font-bold"
                >
                  {strategies.map(s => (
                    <option key={s.id} value={s.id}>{s.id.toUpperCase()}</option>
                  ))}
                </select>
              </div>

              <div>
                <div className="flex justify-between items-center mb-2">
                  <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider">Symbols Target Selection</label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={backtestParams.selectAllSymbols}
                      onChange={e => setBacktestParams(prev => ({ ...prev, selectAllSymbols: e.target.checked, symbols: e.target.checked ? [] : [...prev.symbols] }))}
                      className="accent-cyan-500 w-3 h-3 rounded"
                    />
                    <span className="text-[10px] text-slate-400 font-bold">Select All</span>
                  </label>
                </div>
                {!backtestParams.selectAllSymbols ? (
                  <div className="flex flex-wrap gap-1.5 max-h-32 overflow-y-auto p-1.5 bg-slate-950 rounded-lg border border-slate-850">
                    {symbols.map(sym => {
                      const isChecked = backtestParams.symbols.includes(sym);
                      return (
                        <div
                          key={sym}
                          onClick={() => toggleBacktestSymbol(sym)}
                          className={`text-[10px] px-2 py-1 border rounded-lg cursor-pointer transition font-black ${
                            isChecked
                              ? "bg-cyan-955/20 border-cyan-500/30 text-cyan-400"
                              : "bg-slate-900 border-slate-800 text-slate-400"
                          }`}
                        >
                          {sym}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="p-3 bg-slate-950 rounded-lg border border-slate-850 text-slate-550 text-[10px] italic">
                    Backtesting across all {symbols.length} live watchlist symbols.
                  </div>
                )}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Timeframe</label>
                  <select
                    value={backtestParams.timeframe}
                    onChange={e => setBacktestParams(prev => ({ ...prev, timeframe: e.target.value }))}
                    className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-300 outline-none focus:border-cyan-500 cursor-pointer font-mono font-bold"
                  >
                    {["1M", "3M", "5M", "10M", "15M", "30M"].map(tf => (
                      <option key={tf} value={tf}>{tf}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Product Type</label>
                  <select
                    value={backtestParams.productType}
                    onChange={e => setBacktestParams(prev => ({ ...prev, productType: e.target.value }))}
                    className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-300 outline-none focus:border-cyan-500 cursor-pointer font-bold"
                  >
                    <option value="INTRADAY">INTRADAY</option>
                    <option value="DELIVERY">DELIVERY</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Start Date</label>
                  <input
                    type="date"
                    value={backtestParams.startDate}
                    onChange={e => setBacktestParams(prev => ({ ...prev, startDate: e.target.value }))}
                    className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-300 outline-none focus:border-cyan-500 font-mono font-bold"
                  />
                </div>
                <div>
                  <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">End Date</label>
                  <input
                    type="date"
                    value={backtestParams.endDate}
                    onChange={e => setBacktestParams(prev => ({ ...prev, endDate: e.target.value }))}
                    className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-300 outline-none focus:border-cyan-500 font-mono font-bold"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Start Capital (INR)</label>
                  <input
                    type="number"
                    value={backtestParams.capital}
                    onChange={e => setBacktestParams(prev => ({ ...prev, capital: parseFloat(e.target.value) || 0 }))}
                    className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-300 outline-none focus:border-cyan-500 font-mono font-bold"
                  />
                </div>
                <div>
                  <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Leverage Multiplier</label>
                  <input
                    type="number"
                    disabled={backtestParams.productType === "DELIVERY"}
                    value={backtestParams.leverage}
                    onChange={e => setBacktestParams(prev => ({ ...prev, leverage: parseInt(e.target.value) || 1 }))}
                    className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-300 outline-none focus:border-cyan-500 disabled:opacity-30 disabled:pointer-events-none font-mono font-bold"
                  />
                </div>
              </div>

              <button
                type="submit"
                disabled={isBacktesting}
                className="w-full py-2.5 bg-cyan-600 hover:bg-cyan-500 active:scale-98 disabled:opacity-45 transition text-white font-extrabold text-xs rounded-lg shadow-md cursor-pointer flex items-center justify-center gap-2"
              >
                <Play className="w-3.5 h-3.5" /> {isBacktesting ? "Executing Simulation..." : "Execute Simulation"}
              </button>
            </form>
          </div>

          {/* Results dashboard grid */}
          <div className="lg:col-span-2 bg-slate-900/40 border border-slate-900 rounded-xl p-5 shadow-lg min-h-[480px] flex flex-col justify-between">
            {isBacktesting ? (
              <div className="flex-1 flex flex-col items-center justify-center space-y-3.5 py-32">
                <div className="w-8 h-8 rounded-full border-2 border-cyan-500/25 border-t-cyan-500 animate-spin" />
                <span className="text-xs text-slate-500 font-bold font-mono">Running sandboxed simulation rules...</span>
              </div>
            ) : backtestReport ? (
              <div className="space-y-4">
                <div className="flex justify-between items-center pb-2.5 border-b border-slate-900">
                  <h3 className="text-xs font-black uppercase text-slate-200 tracking-wider">Historical Simulation Report</h3>
                  <span className="text-[10px] font-mono text-cyan-400 font-bold">SUCCESS</span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
                  <div className="p-3 bg-slate-950 border border-slate-900 rounded-xl flex items-center justify-between">
                    <div>
                      <span className="text-[8px] text-slate-500 uppercase font-black font-sans">Net Return P&L</span>
                      <span className={`text-base font-black font-mono block mt-0.5 ${backtestReport["Net Profit (INR)"] >= 0 ? "text-emerald-400" : "text-rose-455"}`}>
                        {formatVal("Net Profit (INR)", backtestReport["Net Profit (INR)"])}
                      </span>
                    </div>
                    <TrendingUp className={`w-5 h-5 ${backtestReport["Net Profit (INR)"] >= 0 ? "text-emerald-400" : "text-rose-455"}`} />
                  </div>

                  <div className="p-3 bg-slate-950 border border-slate-900 rounded-xl flex items-center justify-between">
                    <div>
                      <span className="text-[8px] text-slate-500 uppercase font-black font-sans">Win Rate</span>
                      <span className="text-base font-black font-mono block mt-0.5 text-cyan-400">
                        {formatVal("Win Rate (%)", backtestReport["Win Rate (%)"])}
                      </span>
                    </div>
                    <CheckCircle className="w-5 h-5 text-cyan-400" />
                  </div>
                </div>

                <div className="border border-slate-850 rounded-xl overflow-hidden">
                  <div className="bg-slate-955 px-4 py-2 border-b border-slate-850 text-[10px] font-black text-slate-400 uppercase tracking-wider">
                    Performance Metrics Details
                  </div>
                  <div className="divide-y divide-slate-900/60 max-h-[320px] overflow-y-auto">
                    {Object.entries(backtestReport).map(([key, val]) => (
                      <div key={key} className="flex justify-between items-center py-2 px-4 text-xs">
                        <span className="text-slate-450 font-semibold">{key}</span>
                        <span className={`font-mono font-black ${
                          key.includes("Net Profit") || key.includes("Gross Profit") || key.includes("Winner")
                            ? "text-emerald-400"
                            : key.includes("Loss") || key.includes("Loser")
                            ? "text-rose-455"
                            : "text-slate-200"
                        }`}>
                          {formatVal(key, val)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center py-32 text-center">
                <Sliders className="w-12 h-12 text-slate-800 mb-3 animate-pulse" />
                <h4 className="text-sm font-black text-slate-450 font-sans">No Backtest Run Registered</h4>
                <p className="text-xs text-slate-600 max-w-sm mt-1.5 font-sans">
                  Configure strategy, dates, timeframe parameters on the left and click execute simulation to generate report.
                </p>
              </div>
            )}

            {backtestReport && !isBacktesting && (
              <div className="pt-4 border-t border-slate-900 flex justify-end">
                <button
                  onClick={() => {
                    const csvContent = "data:text/csv;charset=utf-8," + 
                      Object.entries(backtestReport).map(e => `${e[0]},${e[1]}`).join("\n");
                    const encodedUri = encodeURI(csvContent);
                    const link = document.createElement("a");
                    link.setAttribute("href", encodedUri);
                    link.setAttribute("download", `backtest_${selectedStrategyId}_report.csv`);
                    link.click();
                  }}
                  className="px-4 py-2 bg-slate-900 hover:bg-slate-850 border border-slate-850 text-slate-300 text-xs font-black rounded-lg cursor-pointer flex items-center gap-1.5"
                >
                  <Download className="w-3.5 h-3.5" /> Export Report CSV
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* SUB-VIEW 3: COMPARE STRATEGIES (UP TO 3) */}
      {activeSubTab === "compare" && (
        <div className="space-y-6">
          
          {/* Parameter Formulation Header Block */}
          <div className="bg-slate-900/40 border border-slate-900 rounded-xl p-5 shadow-lg">
            <h3 className="text-xs font-black uppercase text-slate-200 tracking-wider mb-4">Multi-Strategy Comparison Form</h3>
            
            <form onSubmit={handleRunCompare} className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
              
              {/* Strategy selector checks */}
              <div className="md:col-span-2">
                <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Select Strategies (Max 3)</label>
                <div className="flex flex-wrap gap-2">
                  {strategies.map(s => {
                    const isSelected = compareParams.selectedIds.includes(s.id);
                    return (
                      <div
                        key={s.id}
                        onClick={() => toggleCompareStrategySelection(s.id)}
                        className={`text-xs px-3.5 py-2.5 border rounded-lg cursor-pointer transition font-black uppercase ${
                          isSelected
                            ? "bg-cyan-955/20 border-cyan-500/35 text-cyan-400 font-extrabold"
                            : "bg-slate-950 border-slate-900 text-slate-400 hover:text-slate-200"
                        }`}
                      >
                        {s.id}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Symbols selector dropdown */}
              <div>
                <div className="flex justify-between items-center mb-2">
                  <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider">Symbols Target Selection</label>
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={compareParams.selectAllSymbols}
                      onChange={e => setCompareParams(prev => ({ ...prev, selectAllSymbols: e.target.checked, symbols: e.target.checked ? [] : [...prev.symbols] }))}
                      className="accent-cyan-500 w-3 h-3 rounded"
                    />
                    <span className="text-[10px] text-slate-400 font-bold">Select All</span>
                  </label>
                </div>
                {!compareParams.selectAllSymbols ? (
                  <div className="flex flex-wrap gap-1.5 max-h-16 overflow-y-auto p-1 bg-slate-950 rounded-lg border border-slate-850">
                    {symbols.map(sym => {
                      const isChecked = compareParams.symbols.includes(sym);
                      return (
                        <div
                          key={sym}
                          onClick={() => toggleCompareSymbol(sym)}
                          className={`text-[9px] px-1.5 py-0.5 border rounded cursor-pointer transition font-bold ${
                            isChecked
                              ? "bg-cyan-955/25 border-cyan-500/30 text-cyan-400"
                              : "bg-slate-900 border-slate-800 text-slate-400"
                          }`}
                        >
                          {sym}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="p-2.5 bg-slate-950 rounded-lg border border-slate-850 text-slate-500 text-[10px] italic">
                    All symbols ({symbols.length}) selected.
                  </div>
                )}
              </div>

              <div>
                <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Timeframe</label>
                <select
                  value={compareParams.timeframe}
                  onChange={e => setCompareParams(prev => ({ ...prev, timeframe: e.target.value }))}
                  className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-350 outline-none focus:border-cyan-500 cursor-pointer font-mono font-bold"
                >
                  {["1M", "3M", "5M", "10M", "15M", "30M"].map(tf => (
                    <option key={tf} value={tf}>{tf}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Product Type</label>
                <select
                  value={compareParams.productType}
                  onChange={e => setCompareParams(prev => ({ ...prev, productType: e.target.value }))}
                  className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-350 outline-none focus:border-cyan-500 cursor-pointer font-bold"
                >
                  <option value="INTRADAY">INTRADAY</option>
                  <option value="DELIVERY">DELIVERY</option>
                </select>
              </div>

              <div>
                <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Leverage Multiplier</label>
                <input
                  type="number"
                  disabled={compareParams.productType === "DELIVERY"}
                  value={compareParams.leverage}
                  onChange={e => setCompareParams(prev => ({ ...prev, leverage: parseInt(e.target.value) || 1 }))}
                  className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-350 outline-none focus:border-cyan-500 disabled:opacity-30 disabled:pointer-events-none font-mono font-bold"
                />
              </div>

              <div>
                <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Start Capital (INR)</label>
                <input
                  type="number"
                  value={compareParams.capital}
                  onChange={e => setCompareParams(prev => ({ ...prev, capital: parseFloat(e.target.value) || 0 }))}
                  className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-350 outline-none focus:border-cyan-500 font-mono font-bold"
                />
              </div>

              <div>
                <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">Start Date</label>
                <input
                  type="date"
                  value={compareParams.startDate}
                  onChange={e => setCompareParams(prev => ({ ...prev, startDate: e.target.value }))}
                  className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-350 outline-none focus:border-cyan-500 font-mono font-bold"
                />
              </div>

              <div>
                <label className="block text-[9px] font-black uppercase text-slate-500 tracking-wider mb-2">End Date</label>
                <input
                  type="date"
                  value={compareParams.endDate}
                  onChange={e => setCompareParams(prev => ({ ...prev, endDate: e.target.value }))}
                  className="w-full bg-slate-955 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-350 outline-none focus:border-cyan-500 font-mono font-bold"
                />
              </div>

              <div className="md:col-span-2 lg:col-span-4 pt-3 flex justify-end">
                <button
                  type="submit"
                  disabled={isComparing}
                  className="px-6 py-2.5 bg-cyan-600 hover:bg-cyan-500 active:scale-95 disabled:opacity-40 transition text-white font-extrabold text-xs rounded-lg shadow-md cursor-pointer flex items-center gap-2"
                >
                  <Play className="w-3.5 h-3.5" /> {isComparing ? "Running Comparison..." : "Compare Strategies"}
                </button>
              </div>
            </form>
          </div>

          {/* Comparative results table grid */}
          <div className="bg-slate-900/40 border border-slate-900 rounded-xl p-5 shadow-lg min-h-[380px] flex flex-col justify-between">
            {isComparing ? (
              <div className="flex-1 flex flex-col items-center justify-center space-y-3.5 py-24">
                <div className="w-8 h-8 rounded-full border-2 border-cyan-500/25 border-t-cyan-500 animate-spin" />
                <span className="text-xs text-slate-500 font-bold font-mono">Running side-by-side comparative scans...</span>
              </div>
            ) : compareReports ? (
              <div className="space-y-4">
                <div className="flex justify-between items-center pb-2.5 border-b border-slate-900">
                  <h3 className="text-xs font-black uppercase text-slate-200 tracking-wider">Strategies Comparison Matrix</h3>
                  <span className="text-[10px] font-mono text-cyan-400 font-bold">ALL SIMULATIONS COMPLETED</span>
                </div>

                <div className="overflow-x-auto border border-slate-850 rounded-xl">
                  <table className="w-full text-left border-collapse text-xs">
                    <thead>
                      <tr className="bg-slate-950/80 border-b border-slate-850 text-[10px] font-black text-slate-400 uppercase tracking-wider">
                        <th className="p-3.5">Metric Key</th>
                        {Object.keys(compareReports).map(stratId => (
                          <th key={stratId} className="p-3.5 font-black text-center bg-slate-900/50 border-l border-slate-850 text-cyan-400 font-sans uppercase">
                            {stratId.toUpperCase()}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-900/60">
                      {Object.keys(Object.values(compareReports)[0] || {}).map(metricKey => {
                        let bestStrat = "";
                        let bestVal = -999999999;
                        if (metricKey.includes("Net Profit") || metricKey.includes("Win Rate")) {
                          Object.entries(compareReports).forEach(([stratId, metrics]) => {
                            const val = metrics[metricKey] || 0;
                            if (val > bestVal) {
                              bestVal = val;
                              bestStrat = stratId;
                            }
                          });
                        }

                        return (
                          <tr key={metricKey} className="hover:bg-slate-950/20 transition">
                            <td className="p-3 font-semibold text-slate-400">{metricKey}</td>
                            {Object.entries(compareReports).map(([stratId, metrics]) => {
                              const val = metrics[metricKey];
                              const isBest = stratId === bestStrat;
                              return (
                                <td 
                                  key={stratId} 
                                  className={`p-3 font-mono font-black text-center border-l border-slate-850/60 ${
                                    isBest 
                                      ? "bg-emerald-950/10 text-emerald-400 animate-pulse" 
                                      : metricKey.includes("Net Profit") || metricKey.includes("Gross Profit") || metricKey.includes("Winner")
                                      ? "text-emerald-450"
                                      : metricKey.includes("Loss") || metricKey.includes("Loser")
                                      ? "text-rose-455"
                                      : "text-slate-200"
                                  }`}
                                >
                                  {formatVal(metricKey, val)}
                                  {isBest && (
                                    <span className="ml-1 text-[8px] px-1 py-0.5 bg-emerald-950 border border-emerald-900 text-emerald-400 rounded uppercase font-sans font-black">
                                      Best
                                    </span>
                                  )}
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center py-24 text-center">
                <Sliders className="w-12 h-12 text-slate-800 mb-3 animate-pulse" />
                <h4 className="text-sm font-black text-slate-400 font-sans">No Comparison Run Registered</h4>
                <p className="text-xs text-slate-600 max-w-sm mt-1.5 font-sans">
                  Select up to 3 strategy models, symbols targets, capital splits and click compare to render details side-by-side.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

    </div>
  );
};
