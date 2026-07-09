import React, { useState, useEffect, useRef, useMemo } from "react";
import { useWebSocket } from "./hooks/useWebSocket";
import { ResearchLabWorkspace } from "./components/ResearchLabWorkspace";
import { 
  Activity, 
  ShieldAlert, 
  TrendingUp, 
  DollarSign, 
  ListOrdered, 
  Layers, 
  History, 
  ArrowUpCircle, 
  ArrowDownCircle, 
  CheckCircle, 
  XCircle, 
  Plus, 
  Trash2, 
  Sliders, 
  Play, 
  Square,
  AlertTriangle,
  Clock,
  Briefcase,
  Calendar,
  Settings,
  Search,
  Download,
  Beaker
} from "lucide-react";

interface Toast {
  id: string;
  message: string;
  type: "success" | "error" | "info" | "warning";
}

const App: React.FC = () => {
  const {
    connectionStatus,
    metrics,
    status,
    eventLog,
    symbolsStatus,
    tradeHistory: liveTradeHistory,
    indices,
    strategyReport,
    strategyConfig,
    startLiveStrategy,
    stopLiveStrategy,
    updateStrategyConfig,
  } = useWebSocket();

  // Navigation page routing state
  const [activePage, setActivePage] = useState<
    "dashboard" | "positions" | "allocation" | "history" | "calendar" | "watch" | "settings" | "research"
  >("dashboard");

  // Notifications Toast State
  const [toasts, setToasts] = useState<Toast[]>([]);
  const addToast = (message: string, type: Toast["type"] = "info") => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 4000);
  };

  // Local configurations state (synced with configs/orb.yaml values when runner is active)
  const [localSymbols, setLocalSymbols] = useState<string[]>(["SBIN", "BAJFINANCE", "INFY", "HDFCBANK", "TATAMOTORS"]);
  const [priorityRanking, setPriorityRanking] = useState<string[]>(["SBIN", "BAJFINANCE", "INFY", "HDFCBANK", "TATAMOTORS"]);
  const [newSymbolInput, setNewSymbolInput] = useState("");
  const [capital, setCapital] = useState<number>(100000);
  const [leverage, setLeverage] = useState<number>(5);
  const [allocationStrategy, setAllocationStrategy] = useState<"SINGLE_STOCK" | "PERCENTAGE_RANKED">("SINGLE_STOCK");
  const [weights, setWeights] = useState<number[]>([0.5, 0.3, 0.2]);
  const [enableLiveStocks, setEnableLiveStocks] = useState<boolean>(false);

  // Settings tab form states
  const [refreshInterval, setRefreshInterval] = useState<number>(1000);
  const [themeMode, setThemeMode] = useState<"dark" | "glass" | "nature">("dark");
  const [maxConcurrentTrades, setMaxConcurrentTrades] = useState<number>(3);
  const [defaultStrategy, setDefaultStrategy] = useState<string>("ORB");
  const [audioAlerts, setAudioAlerts] = useState<boolean>(true);

  // Sync state parameters from backend when received
  useEffect(() => {
    if (strategyConfig) {
      setLocalSymbols(strategyConfig.symbols);
      setPriorityRanking(strategyConfig.priority_ranking);
      setCapital(strategyConfig.capital);
      setLeverage(strategyConfig.leverage);
      setAllocationStrategy(strategyConfig.allocation_strategy as "SINGLE_STOCK" | "PERCENTAGE_RANKED");
      setWeights(strategyConfig.allocation_weights);
      setEnableLiveStocks(strategyConfig.enable_live_stocks || false);
    }
  }, [strategyConfig]);

  const isRunning = status?.status === "RUNNING";
  const connectionOk = status?.connection_ok !== false;

  // Real-time Event Notifications Watcher
  // 1. Connection Changes Toasts
  const prevConnectionStatus = useRef(connectionStatus);
  useEffect(() => {
    if (connectionStatus !== prevConnectionStatus.current) {
      if (connectionStatus === "connected") {
        addToast("Connected to Trade Engine Socket API Server", "success");
      } else if (connectionStatus === "connecting") {
        addToast("Attempting to connect to Trade Engine...", "info");
      } else if (connectionStatus === "disconnected") {
        addToast("Lost connection to Trade Engine Server", "error");
      }
      prevConnectionStatus.current = connectionStatus;
    }
  }, [connectionStatus]);

  // 2. Ticks Watchdog Alarms
  const prevConnectionOk = useRef(connectionOk);
  useEffect(() => {
    if (connectionOk !== prevConnectionOk.current) {
      if (!connectionOk && isRunning) {
        addToast("Dhan Live Feed lost ticks. Retrying connection...", "error");
      } else if (connectionOk && isRunning) {
        addToast("Dhan Live Feed data feed restored", "success");
      }
      prevConnectionOk.current = connectionOk;
    }
  }, [connectionOk, isRunning]);

  // 3. Trade Entry / Exit Tracker Toasts
  const [prevActiveTrades, setPrevActiveTrades] = useState<Record<string, boolean>>({});
  useEffect(() => {
    const currentActive: Record<string, boolean> = {};
    Object.keys(symbolsStatus).forEach(sym => {
      currentActive[sym] = !!symbolsStatus[sym].active_trade;
    });

    Object.keys(currentActive).forEach(sym => {
      const wasActive = !!prevActiveTrades[sym];
      const isActive = currentActive[sym];

      if (isActive && !wasActive) {
        const detail = (symbolsStatus[sym] as any)?.active_trade_detail;
        addToast(`Order Filled: Entered ${detail?.direction || "LONG"} in ${sym} at ₹${detail?.entry_price || symbolsStatus[sym].last_ltp}`, "success");
        if (audioAlerts) playChime(true);
      } else if (!isActive && wasActive) {
        // Exited position
        addToast(`Order Closed: Exited trade in ${sym}`, "warning");
        if (audioAlerts) playChime(false);
      }
    });

    setPrevActiveTrades(currentActive);
  }, [symbolsStatus]);

  // Simple Synthesizer Audio Alerts
  const playChime = (isSuccess: boolean) => {
    try {
      const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);

      if (isSuccess) {
        osc.frequency.setValueAtTime(523.25, ctx.currentTime); // C5
        osc.frequency.setValueAtTime(659.25, ctx.currentTime + 0.12); // E5
        gain.gain.setValueAtTime(0.08, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
        osc.start();
        osc.stop(ctx.currentTime + 0.4);
      } else {
        osc.frequency.setValueAtTime(329.63, ctx.currentTime); // E4
        osc.frequency.setValueAtTime(261.63, ctx.currentTime + 0.12); // C4
        gain.gain.setValueAtTime(0.08, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
        osc.start();
        osc.stop(ctx.currentTime + 0.45);
      }
    } catch (_) {}
  };

  // Watchlist controls
  const addSymbol = () => {
    const sym = newSymbolInput.trim().toUpperCase();
    if (sym && !localSymbols.includes(sym)) {
      const updatedSymbols = [...localSymbols, sym];
      const updatedRanking = [...priorityRanking, sym];
      setLocalSymbols(updatedSymbols);
      setPriorityRanking(updatedRanking);
      setNewSymbolInput("");

      if (isRunning) {
        updateStrategyConfig({
          priority_ranking: updatedRanking
        });
      }
      addToast(`Added ${sym} to trade engine watchlist`, "info");
    }
  };

  const removeSymbol = (sym: string) => {
    const updatedSymbols = localSymbols.filter(s => s !== sym);
    const updatedRanking = priorityRanking.filter(s => s !== sym);
    setLocalSymbols(updatedSymbols);
    setPriorityRanking(updatedRanking);

    if (isRunning) {
      updateStrategyConfig({
        priority_ranking: updatedRanking
      });
    }
    addToast(`Removed ${sym} from watchlist`, "warning");
  };

  const movePriority = (index: number, direction: "UP" | "DOWN") => {
    if (direction === "UP" && index === 0) return;
    if (direction === "DOWN" && index === priorityRanking.length - 1) return;

    const newIndex = direction === "UP" ? index - 1 : index + 1;
    const updated = [...priorityRanking];
    const temp = updated[index];
    updated[index] = updated[newIndex];
    updated[newIndex] = temp;

    setPriorityRanking(updated);

    if (isRunning) {
      updateStrategyConfig({ priority_ranking: updated });
    }
  };

  const handleStartLive = () => {
    startLiveStrategy(
      localSymbols,
      capital,
      leverage,
      priorityRanking,
      allocationStrategy,
      weights,
      enableLiveStocks
    );
    addToast("Live Intraday Paper Trade Runner Initiated", "success");
  };

  // Expose unified trade log (only cover live session trade log)
  const allCompletedTrades = useMemo(() => {
    return liveTradeHistory;
  }, [liveTradeHistory]);

  // Performance calculations
  const performanceStats = useMemo(() => {
    const trades = allCompletedTrades;
    const totalTrades = trades.length;
    
    // Profit factor, winners and losers
    let wins = 0;
    let losses = 0;
    let totalGrossProfit = 0;
    let totalGrossLoss = 0;
    let totalNetPnL = 0;
    
    trades.forEach(t => {
      const net = t.Net_PnL || 0;
      totalNetPnL += net;
      if (net > 0) {
        wins++;
        totalGrossProfit += (t.Gross_PnL || 0);
      } else {
        losses++;
        totalGrossLoss += Math.abs(t.Gross_PnL || 0);
      }
    });

    const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;
    const profitFactor = totalGrossLoss > 0 ? (totalGrossProfit / totalGrossLoss) : totalGrossProfit > 0 ? 99.9 : 0;

    // Monthly Net returns calculation (Current month)
    const currentMonthPrefix = new Date().toISOString().substring(0, 7); // YYYY-MM
    const monthlyNetPnL = trades
      .filter(t => t.Entry_Time && t.Entry_Time.substring(0, 7) === currentMonthPrefix)
      .reduce((sum, t) => sum + (t.Net_PnL || 0), 0);

    // Simulated Max Drawdown based on NAV peak
    let peakNAV = capital;
    let runningNAV = capital;
    let maxDrawdown = 0;

    // Sorted chronological to calculate curves
    const chronoTrades = [...trades].sort((a,b) => new Date(a.Entry_Time).getTime() - new Date(b.Entry_Time).getTime());
    chronoTrades.forEach(t => {
      runningNAV += (t.Net_PnL || 0);
      if (runningNAV > peakNAV) peakNAV = runningNAV;
      const dd = ((peakNAV - runningNAV) / peakNAV) * 100;
      if (dd > maxDrawdown) maxDrawdown = dd;
    });

    return {
      totalNetPnL,
      monthlyNetPnL,
      totalTrades,
      wins,
      losses,
      winRate,
      profitFactor,
      maxDrawdown
    };
  }, [allCompletedTrades, capital]);

  // Active positions utilized cash calculation
  const utilizedMargin = useMemo(() => {
    return Object.values(strategyReport?.positions || {}).reduce((acc, pos) => acc + pos.capital_utilized, 0);
  }, [strategyReport]);

  // Trade History state, sorting and virtualized list parameters
  const [searchTerm, setSearchTerm] = useState("");
  const [filterSymbol, setFilterSymbol] = useState("ALL");
  const [filterMonth, setFilterMonth] = useState("ALL");
  const [filterStrategy, setFilterStrategy] = useState("ALL");
  const [sortColumn, setSortColumn] = useState<string>("Entry_Time");
  const [sortAsc, setSortAsc] = useState(false);

  const uniqueSymbols = useMemo(() => {
    const syms = new Set<string>();
    allCompletedTrades.forEach(t => { if (t.Symbol) syms.add(t.Symbol); });
    return ["ALL", ...Array.from(syms)];
  }, [allCompletedTrades]);

  const uniqueMonths = useMemo(() => {
    const months = new Set<string>();
    allCompletedTrades.forEach(t => {
      if (t.Entry_Time) {
        months.add(t.Entry_Time.substring(0, 7)); // YYYY-MM
      }
    });
    return ["ALL", ...Array.from(months).sort().reverse()];
  }, [allCompletedTrades]);

  const sortedAndFilteredHistory = useMemo(() => {
    let result = [...allCompletedTrades];

    // Search filter
    if (searchTerm.trim()) {
      const q = searchTerm.toLowerCase();
      result = result.filter(t => 
        t.Symbol?.toLowerCase().includes(q) || 
        t.Exit_Reason?.toLowerCase().includes(q) ||
        t.Setup?.toLowerCase().includes(q)
      );
    }

    // Dropdown filters
    if (filterSymbol !== "ALL") {
      result = result.filter(t => t.Symbol === filterSymbol);
    }
    if (filterMonth !== "ALL") {
      result = result.filter(t => t.Entry_Time && t.Entry_Time.substring(0, 7) === filterMonth);
    }
    if (filterStrategy !== "ALL") {
      result = result.filter(t => t.Setup === filterStrategy || (filterStrategy === "ORB" && t.Setup?.includes("Setup")));
    }

    // Column sorting
    result.sort((a: any, b: any) => {
      let valA = a[sortColumn];
      let valB = b[sortColumn];

      if (typeof valA === "string" && (valA.includes("T") || valA.includes("-"))) {
        valA = new Date(valA).getTime();
        valB = new Date(valB).getTime();
      }

      if (valA === undefined) return 1;
      if (valB === undefined) return -1;

      if (valA < valB) return sortAsc ? -1 : 1;
      if (valA > valB) return sortAsc ? 1 : -1;
      return 0;
    });

    return result;
  }, [allCompletedTrades, searchTerm, filterSymbol, filterMonth, filterStrategy, sortColumn, sortAsc]);

  // Virtualized Scroll implementation parameters
  const [scrollTop, setScrollTop] = useState(0);
  const rowHeight = 44; // px row height
  const viewportHeight = 520; // px list container
  const visibleRowsCount = Math.ceil(viewportHeight / rowHeight);
  const totalHeight = sortedAndFilteredHistory.length * rowHeight;
  
  const startIndex = Math.max(0, Math.floor(scrollTop / rowHeight) - 3);
  const endIndex = Math.min(sortedAndFilteredHistory.length, startIndex + visibleRowsCount + 6);
  
  const visibleSlice = useMemo(() => {
    return sortedAndFilteredHistory.slice(startIndex, endIndex);
  }, [sortedAndFilteredHistory, startIndex, endIndex]);
  
  const offsetTop = startIndex * rowHeight;

  const handleTableScroll = (e: React.UIEvent<HTMLDivElement>) => {
    setScrollTop(e.currentTarget.scrollTop);
  };

  const handleHeaderClick = (col: string) => {
    if (sortColumn === col) {
      setSortAsc(p => !p);
    } else {
      setSortColumn(col);
      setSortAsc(true);
    }
  };

  // CSV Data Exporter
  const exportCSV = () => {
    if (sortedAndFilteredHistory.length === 0) return;
    
    const headersLine = "Trade ID,Symbol,Setup (Strategy),Direction,Entry Date,Entry Time,Exit Date,Exit Time,Quantity,Entry Price,Exit Price,Gross P/L,Charges,Net P/L,Hold Time (Mins),Exit Reason";
    const rows = sortedAndFilteredHistory.map(t => {
      const entryD = t.Entry_Time?.split("T")[0] || "";
      const entryT = t.Entry_Time?.split("T")[1]?.slice(0,8) || "";
      const exitD = t.Exit_Time?.split("T")[0] || "";
      const exitT = t.Exit_Time?.split("T")[1]?.slice(0,8) || "";
      return [
        t.Trade_ID,
        t.Symbol,
        t.Setup || "ORB Strategy",
        t.Direction,
        entryD,
        entryT,
        exitD,
        exitT,
        t.Qty,
        t.Entry_Price,
        t.Exit_Price,
        t.Gross_PnL || 0.0,
        t.Fees || 0.0,
        t.Net_PnL || 0.0,
        t.Hold_Time_Mins || 0,
        `"${t.Exit_Reason || "Target"}"`
      ].join(",");
    });

    const csvContent = [headersLine, ...rows].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `trading_history_${new Date().toISOString().substring(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    addToast("Exported filtered trades to CSV successfully", "success");
  };

  // XML Excel Spreadsheet Exporter
  const exportExcel = () => {
    if (sortedAndFilteredHistory.length === 0) return;

    let tableHtml = `<table border="1">
      <tr style="background:#070a13;color:#ffffff;font-weight:bold;">
        <th>Trade ID</th><th>Symbol</th><th>Setup</th><th>Direction</th><th>Entry Time</th><th>Entry Price</th><th>Qty</th><th>Exit Time</th><th>Exit Price</th><th>Gross PnL</th><th>Charges</th><th>Net PnL</th><th>Hold Mins</th><th>Exit Reason</th>
      </tr>`;
      
    sortedAndFilteredHistory.forEach(t => {
      tableHtml += `<tr>
        <td>${t.Trade_ID}</td>
        <td>${t.Symbol}</td>
        <td>${t.Setup || "ORB"}</td>
        <td>${t.Direction}</td>
        <td>${t.Entry_Time}</td>
        <td>${t.Entry_Price.toFixed(2)}</td>
        <td>${t.Qty}</td>
        <td>${t.Exit_Time}</td>
        <td>${t.Exit_Price.toFixed(2)}</td>
        <td>${(t.Gross_PnL || 0).toFixed(2)}</td>
        <td>${(t.Fees || 0).toFixed(2)}</td>
        <td>${(t.Net_PnL || 0).toFixed(2)}</td>
        <td>${t.Hold_Time_Mins || 0}</td>
        <td>${t.Exit_Reason}</td>
      </tr>`;
    });
    tableHtml += `</table>`;

    const blob = new Blob([tableHtml], { type: "application/vnd.ms-excel" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `trading_report_${new Date().toISOString().substring(0, 10)}.xls`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    addToast("Exported filtered trades to Excel document", "success");
  };

  // Calendar Heatmap configuration
  const [selectedCalendarDay, setSelectedCalendarDay] = useState<string | null>(null);
  
  // Group all completed trades for clicked day
  const selectedDayTrades = useMemo(() => {
    if (!selectedCalendarDay) return [];
    return allCompletedTrades.filter(t => {
      const tDate = t.Entry_Time?.split("T")[0];
      return tDate === selectedCalendarDay;
    });
  }, [selectedCalendarDay, allCompletedTrades]);

  // Calendar Month renderer
  const renderCalendarMonth = (monthOffset: number) => {
    // Renders months relative to current date
    const targetDate = new Date();
    targetDate.setMonth(targetDate.getMonth() - monthOffset);
    const month = targetDate.getMonth();
    const year = targetDate.getFullYear();

    const monthName = targetDate.toLocaleString("default", { month: "long" });
    const totalDays = new Date(year, month + 1, 0).getDate();
    const firstDayIndex = new Date(year, month, 1).getDay(); // 0 is Sunday

    const daysList = [];
    for (let i = 0; i < firstDayIndex; i++) {
      daysList.push(null);
    }
    for (let d = 1; d <= totalDays; d++) {
      daysList.push(d);
    }

    return (
      <div key={`${year}-${month}`} className="bg-slate-900/60 border border-slate-850 rounded-xl p-5 shadow-lg">
        <h4 className="text-xs font-black text-cyan-400 uppercase tracking-widest mb-3 border-b border-slate-850/60 pb-2">
          {monthName} {year}
        </h4>
        <div className="grid grid-cols-7 gap-1.5 text-center">
          {["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map(d => (
            <div key={d} className="text-[10px] text-slate-500 font-bold uppercase py-0.5">{d}</div>
          ))}
          {daysList.map((day, idx) => {
            if (day === null) {
              return <div key={`empty-${idx}`} className="aspect-square" />;
            }

            const dayString = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
            const dayTrades = allCompletedTrades.filter(t => t.Entry_Time && t.Entry_Time.substring(0, 10) === dayString);
            const tradesCount = dayTrades.length;
            const netDayPnL = dayTrades.reduce((sum, t) => sum + (t.Net_PnL || 0), 0);
            const grossDayPnL = dayTrades.reduce((sum, t) => sum + (t.Gross_PnL || 0), 0);
            const feesDayPnL = dayTrades.reduce((sum, t) => sum + (t.Fees || 0), 0);
            const winsCount = dayTrades.filter(t => (t.Net_PnL || 0) > 0).length;
            const winRate = tradesCount > 0 ? (winsCount / tradesCount) * 100 : 0;
            const avgHold = tradesCount > 0 ? Math.round(dayTrades.reduce((sum, t) => sum + (t.Hold_Time_Mins || 0), 0) / tradesCount) : 0;

            let cellClass = "bg-slate-950/60 border border-slate-900 text-slate-500 hover:border-slate-800";
            if (tradesCount > 0) {
              cellClass = netDayPnL >= 0 
                ? "bg-emerald-950/30 border border-emerald-500/50 hover:bg-emerald-900/40 text-emerald-400 font-black cursor-pointer scale-100" 
                : "bg-rose-950/30 border border-rose-500/50 hover:bg-rose-900/40 text-rose-400 font-black cursor-pointer scale-100";
            }

            return (
              <div 
                key={`day-${day}`}
                onClick={() => tradesCount > 0 && setSelectedCalendarDay(dayString)}
                className={`aspect-square flex items-center justify-center text-[10px] rounded transition duration-150 relative group ${cellClass}`}
              >
                {day}

                {/* Instant hovering details tooltip */}
                {tradesCount > 0 && (
                  <div className="hidden group-hover:block absolute bottom-full left-1/2 transform -translate-x-1/2 mb-2 w-48 bg-slate-950/95 border border-slate-880 rounded-xl p-3.5 shadow-2xl text-left text-[10px] font-sans font-normal text-slate-300 z-50 pointer-events-none">
                    <div className="font-extrabold text-white border-b border-slate-850 pb-1 mb-1.5 flex justify-between">
                      <span>{dayString}</span>
                      <span className="text-[9px] bg-slate-805 text-slate-400 px-1.5 py-0.2 rounded font-mono">#{tradesCount} Trades</span>
                    </div>
                    <div className="flex justify-between mt-1"><span>Gross Profit:</span><span className={grossDayPnL >= 0 ? "text-emerald-400" : "text-rose-400"}>₹{grossDayPnL.toFixed(2)}</span></div>
                    <div className="flex justify-between"><span>Charges & Fees:</span><span className="text-rose-450">₹{feesDayPnL.toFixed(2)}</span></div>
                    <div className="flex justify-between font-black border-t border-slate-850 mt-1.5 pt-1 text-slate-105">
                      <span>Net PnL:</span><span className={netDayPnL >= 0 ? "text-emerald-400" : "text-rose-400"}>₹{netDayPnL.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between mt-1 border-t border-slate-900 pt-1 text-slate-400 font-mono text-[9px]">
                      <span>Win Rate: {winRate.toFixed(0)}%</span>
                      <span>Hold: {avgHold}m</span>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  // Capital Allocation Settings Saver
  const saveAllocationWeights = (newWeights: number[]) => {
    setWeights(newWeights);
    updateStrategyConfig({
      allocation_weights: newWeights
    });
    addToast("Capital allocation weights updated successfully", "success");
  };

  // Save Settings panel configuration parameters
  const handleSaveSettings = (e: React.FormEvent) => {
    e.preventDefault();
    updateStrategyConfig({
      capital: capital,
      leverage: leverage,
      allocation_strategy: allocationStrategy,
      allocation_weights: weights,
      enable_live_stocks: enableLiveStocks
    } as any);
    addToast("Global strategy settings persisted back to configs/orb.yaml", "success");
  };

  return (
    <div className={`min-h-screen ${themeMode === "nature" ? "theme-nature" : themeMode === "glass" ? "theme-glass" : "theme-dark"} ${themeMode === "glass" ? "bg-[#040810] bg-radial from-[#121c32] to-[#040810]" : "bg-slate-950"} text-slate-100 font-sans flex`}>
      
      {/* 1. LEFT PERMANENT SIDEBAR */}
      <aside className="w-64 border-r border-slate-900 bg-slate-950/80 backdrop-blur-md flex flex-col justify-between flex-shrink-0 z-30">
        <div>
          {/* Logo Brand Header */}
          <div className="p-6 border-b border-slate-900 flex items-center gap-3">
            <div className="p-2 rounded-lg bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 shadow-md">
              <Activity className="w-5 h-5 animate-pulse" />
            </div>
            <div>
              <h2 className="text-sm font-black tracking-wider text-white uppercase">Antigravity Terminal</h2>
              <span className="text-[9px] text-slate-500 font-bold uppercase tracking-widest block">Core Platform v2.0</span>
            </div>
          </div>

          {/* Connection status card */}
          <div className="px-5 py-4 border-b border-slate-900/60 bg-slate-950/40">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[9px] text-slate-500 uppercase font-black">Connection Health</span>
              <span className={`w-2 h-2 rounded-full ${connectionStatus === "connected" ? "bg-emerald-400 animate-ping" : "bg-rose-500 animate-pulse"}`} />
            </div>
            <div className="text-[11px] font-mono text-slate-350 flex justify-between">
              <span>Status:</span>
              <span className={`font-bold ${connectionStatus === "connected" ? "text-emerald-400" : "text-rose-550"}`}>
                {connectionStatus.toUpperCase()}
              </span>
            </div>
            <div className="text-[10px] font-mono text-slate-500 flex justify-between mt-0.5">
              <span>Latency:</span>
              <span className="text-slate-400">~{metrics?.avg_pipeline_time_ms ? (metrics.avg_pipeline_time_ms * 1000).toFixed(0) : "12"} ms</span>
            </div>
          </div>

          {/* Navigation Sidebar List */}
          <nav className="p-4 space-y-1.5">
            {[
              { id: "dashboard", label: "Dashboard Hub", icon: Briefcase },
              { id: "positions", label: "Live Positions", icon: Layers, badge: Object.keys(strategyReport?.positions || {}).length },
              { id: "allocation", label: "Capital Allocation", icon: Sliders },
              { id: "history", label: "Trade History", icon: History, badge: allCompletedTrades.length },
              { id: "calendar", label: "Trading Calendar", icon: Calendar },
              { id: "research", label: "Research Lab", icon: Beaker },
              { id: "watch", label: "Market Watch", icon: ListOrdered },
              { id: "settings", label: "System Settings", icon: Settings },
            ].map(item => {
              const Icon = item.icon;
              const isActive = activePage === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => setActivePage(item.id as any)}
                  className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-lg text-xs font-bold transition duration-150 cursor-pointer ${
                    isActive 
                      ? "bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 shadow-md shadow-cyan-950/20" 
                      : "text-slate-400 hover:text-slate-200 hover:bg-slate-900/40"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <Icon className={`w-4 h-4 ${isActive ? "text-cyan-400" : "text-slate-400"}`} />
                    <span>{item.label}</span>
                  </div>
                  {!!item.badge && (
                    <span className={`text-[9px] font-black px-1.5 py-0.5 rounded-full ${isActive ? "bg-cyan-500/20 text-cyan-300" : "bg-slate-900 text-slate-500 border border-slate-800"}`}>
                      {item.badge}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>

        {/* Start / Stop Control Footer in Sidebar */}
        <div className="p-4 border-t border-slate-905 bg-slate-950/40">
          {isRunning ? (
            <button
              onClick={stopLiveStrategy}
              className="w-full flex items-center justify-center gap-2 py-2.5 bg-rose-600 hover:bg-rose-500 active:scale-95 transition text-white font-extrabold text-xs rounded-lg shadow-md cursor-pointer"
            >
              <Square className="w-3.5 h-3.5" /> Stop Paper Trade
            </button>
          ) : (
            <button
              onClick={handleStartLive}
              disabled={localSymbols.length === 0}
              className="w-full flex items-center justify-center gap-2 py-2.5 bg-cyan-600 hover:bg-cyan-500 disabled:opacity-40 disabled:cursor-not-allowed active:scale-95 transition text-white font-extrabold text-xs rounded-lg shadow-md cursor-pointer"
            >
              <Play className="w-3.5 h-3.5" /> Start Paper Trade
            </button>
          )}
        </div>
      </aside>

      {/* 2. MAIN WORKING INTERFACE CONTENT AREA */}
      <main className="flex-1 flex flex-col min-w-0 overflow-y-auto p-6 max-h-screen">
        
        {/* Connection Disconnection Notice Banner */}
        {connectionStatus !== "connected" && (
          <div className="mb-5 flex items-center justify-between px-4 py-3 rounded-lg bg-rose-950/40 border border-rose-900/60 text-rose-300 text-xs font-semibold animate-pulse shadow-md">
            <div className="flex items-center gap-2">
              <ShieldAlert className="w-4 h-4 text-rose-400" />
              <span>CRITICAL ERROR: Trade Engine Socket Connection Lost. Retrying connection...</span>
            </div>
          </div>
        )}

        {!connectionOk && isRunning && (
          <div className="mb-5 flex flex-col md:flex-row items-center justify-between gap-3 px-5 py-4 rounded-xl bg-amber-950/30 border border-amber-900/50 text-amber-200 text-xs shadow-md relative overflow-hidden">
            <div className="absolute left-0 top-0 bottom-0 w-1.5 bg-amber-500 animate-pulse"></div>
            <div className="flex items-center gap-3">
              <AlertTriangle className="w-5 h-5 text-amber-400 animate-bounce" />
              <div>
                <span className="font-bold text-white block uppercase tracking-wider text-[10px] mb-0.5">Dhan Feed Disconnected</span>
                <span className="text-slate-400">Live stock feeds are currently suspended. Retrying stream handshake hooks.</span>
              </div>
            </div>
            <div className="px-3 py-1 rounded bg-amber-500/10 border border-amber-500/20 font-bold uppercase text-[9px] tracking-widest text-amber-400 animate-pulse">
              Watchdog Suspension Mode
            </div>
          </div>
        )}

        {/* -------------------- PAGE RENDER ROUTING -------------------- */}

        {/* PAGE 1: DASHBOARD HUB */}
        {activePage === "dashboard" && (
          <div className="space-y-6">
            
            {/* Index cards tracker */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              {Object.entries(indices).map(([idxName, val]) => {
                const isBullish = val.change_pct >= 0;
                return (
                  <div key={idxName} className="bg-slate-900/40 border border-slate-850 rounded-xl p-5 shadow-lg flex items-center justify-between relative overflow-hidden">
                    <div className={`absolute left-0 top-0 bottom-0 w-1 ${isBullish ? "bg-emerald-500" : "bg-rose-500"}`} />
                    <div>
                      <span className="text-[10px] text-slate-500 font-black uppercase tracking-widest block">{idxName.replace("_", " ")}</span>
                      <div className="text-2xl font-black text-white mt-1.5 font-mono">
                        ₹{val.ltp > 0 ? val.ltp.toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "0.00"}
                      </div>
                      <div className="text-[10px] text-slate-500 font-mono mt-0.5">
                        Open: ₹{val.open > 0 ? val.open.toFixed(2) : "0.00"}
                      </div>
                    </div>
                    <div className="text-right">
                      <span className={`text-xs font-black font-mono px-2.5 py-1 rounded-full ${isBullish ? "bg-emerald-500/10 text-emerald-400" : "bg-rose-500/10 text-rose-450"}`}>
                        {isBullish ? "+" : ""}{val.change_pct.toFixed(2)}%
                      </span>
                      <div className={`text-[9px] font-black uppercase tracking-wider mt-2.5 ${isBullish ? "text-emerald-500" : "text-rose-505"}`}>
                        Trend: {val.trend}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Account Info Cards */}
            <div>
              <h3 className="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-3">Intraday Account Ledger</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-5">
                {[
                  { label: "Net Asset Value (NAV)", val: strategyReport?.net_asset_value_inr || capital, color: "text-white", bg: "bg-indigo-500/10 text-indigo-400", icon: TrendingUp, sub: "Equity Curve Peak" },
                  { label: "Available Cash Balance", val: strategyReport?.cash_inr || capital, color: "text-slate-200", bg: "bg-cyan-500/10 text-cyan-400", icon: DollarSign, sub: "Unused Capital" },
                  { label: "Used Intraday Margin", val: utilizedMargin, color: "text-slate-200", bg: "bg-amber-500/10 text-amber-400", icon: Layers, sub: "Active Exposure" },
                  { label: "Reserved Margin (10%)", val: utilizedMargin * 0.10, color: "text-slate-350", bg: "bg-slate-800 text-slate-400", icon: ShieldAlert, sub: "Buffered Lock" },
                  { label: "Remaining Margin", val: (strategyReport?.cash_inr || capital) - utilizedMargin, color: "text-slate-300", bg: "bg-purple-500/10 text-purple-400", icon: TrendingUp, sub: "Free Buying Power" },
                ].map((item, idx) => {
                  const Icon = item.icon;
                  return (
                    <div key={idx} className="bg-slate-900/60 border border-slate-850 rounded-xl p-4.5 shadow-md flex flex-col justify-between h-28">
                      <div className="flex justify-between items-start">
                        <span className="text-[9px] text-slate-500 font-bold uppercase tracking-wider leading-snug">{item.label}</span>
                        <div className={`p-1.5 rounded-lg text-xs ${item.bg}`}>
                          <Icon className="w-3.5 h-3.5" />
                        </div>
                      </div>
                    <div>
                      <div className={`text-base font-black ${item.color} font-mono`}>
                        ₹{item.val.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                      </div>
                      <span className="text-[8px] text-slate-500 tracking-wide font-medium block mt-0.5">{item.sub}</span>
                    </div>
                  </div>
                );
              })}
              </div>
            </div>

            {/* Capital Parameters Detail Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              
              {/* Account Capital limits */}
              <div className="bg-slate-900/60 border border-slate-850 rounded-xl p-5 shadow-lg lg:col-span-2">
                <h3 className="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-4">Capital Limits Configuration</h3>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                  {[
                    { label: "Max Capital Pool", val: `₹${capital.toLocaleString("en-IN")}` },
                    { label: "Current Allocated", val: `₹${(allocationStrategy === "SINGLE_STOCK" ? capital : weights.reduce((s, w) => s + w, 0) * capital).toLocaleString("en-IN")}` },
                    { label: "Intraday Leverage", val: `${leverage}x` },
                    { label: "Total Buying Power", val: `₹${((strategyReport?.cash_inr || capital) * leverage).toLocaleString("en-IN")}` }
                  ].map((card, i) => (
                    <div key={i} className="bg-slate-950/60 border border-slate-900 rounded-lg p-3 text-center">
                      <span className="block text-[8px] text-slate-500 font-black uppercase tracking-wider mb-1">{card.label}</span>
                      <span className="text-xs font-black text-white font-mono">{card.val}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Watchdog log count indicator */}
              <div className="bg-slate-900/60 border border-slate-850 rounded-xl p-5 shadow-lg flex flex-col justify-between">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-[10px] text-slate-500 font-black uppercase tracking-widest">Active Engine Config</h4>
                  <span className="text-[9px] bg-slate-800 text-slate-400 font-bold px-2 py-0.5 rounded-full">{isRunning ? "LIVE" : "STOPPED"}</span>
                </div>
                <div className="space-y-1 text-xs text-slate-400">
                  <div className="flex justify-between font-mono"><span>Watchlist Count:</span><span className="text-white font-bold">{localSymbols.length}</span></div>
                  <div className="flex justify-between font-mono"><span>Allocation Mode:</span><span className="text-white font-bold">{allocationStrategy}</span></div>
                </div>
              </div>
            </div>

            {/* Performance metrics dashboard card */}
            <div className="bg-slate-900/60 border border-slate-850 rounded-xl p-5 shadow-lg">
              <h3 className="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-4">Historical Performance Summary</h3>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-9 gap-4 text-center">
                {[
                  { label: "Today's P/L", val: `₹${(liveTradeHistory.reduce((s,t)=>s+(t.Net_PnL||0),0)).toFixed(2)}`, color: liveTradeHistory.reduce((s,t)=>s+(t.Net_PnL||0),0) >= 0 ? "text-emerald-400" : "text-rose-400" },
                  { label: "Monthly P/L", val: `₹${performanceStats.monthlyNetPnL.toFixed(2)}`, color: performanceStats.monthlyNetPnL >= 0 ? "text-emerald-400" : "text-rose-400" },
                  { label: "Total Net P/L", val: `₹${performanceStats.totalNetPnL.toFixed(2)}`, color: performanceStats.totalNetPnL >= 0 ? "text-emerald-400" : "text-rose-400" },
                  { label: "Total Trades", val: performanceStats.totalTrades, color: "text-slate-200" },
                  { label: "Winners", val: performanceStats.wins, color: "text-emerald-400" },
                  { label: "Losers", val: performanceStats.losses, color: "text-rose-400" },
                  { label: "Win Rate", val: `${performanceStats.winRate.toFixed(1)}%`, color: "text-cyan-400" },
                  { label: "Profit Factor", val: performanceStats.profitFactor.toFixed(2), color: "text-indigo-400" },
                  { label: "Max Drawdown", val: `${performanceStats.maxDrawdown.toFixed(2)}%`, color: "text-amber-400" }
                ].map((stat, i) => (
                  <div key={i} className="bg-slate-950/60 border border-slate-900 rounded-lg p-2.5">
                    <span className="block text-[8px] text-slate-500 font-bold uppercase tracking-wider mb-1 leading-normal">{stat.label}</span>
                    <span className={`text-xs font-black font-mono ${stat.color}`}>{stat.val}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Live Positions Widget */}
            <div className="bg-slate-900/60 border border-slate-850 rounded-xl p-5 shadow-lg">
              <div className="flex items-center justify-between mb-4.5 border-b border-slate-850/60 pb-2.5">
                <h3 className="text-xs font-black text-slate-400 uppercase tracking-wider flex items-center gap-2">
                  <Layers className="w-4 h-4 text-cyan-400" /> Active System Positions
                </h3>
                <span className="text-[10px] text-slate-500 font-bold font-mono">
                  Open Positions: {Object.keys(strategyReport?.positions || {}).length}
                </span>
              </div>

              {Object.keys(strategyReport?.positions || {}).length > 0 ? (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  {Object.values(strategyReport!.positions).map(pos => {
                    const statusDetail = symbolsStatus[pos.symbol];
                    const activeDetail = (statusDetail as any)?.active_trade_detail;
                    const directionText = pos.qty > 0 ? "LONG" : "SHORT";
                    
                    const openPrice = activeDetail?.entry_price || pos.avg_price;
                    const ltpPrice = statusDetail?.last_ltp || pos.avg_price;
                    const slPrice = activeDetail?.stop_loss || (pos.qty > 0 ? (statusDetail?.range_low || openPrice * 0.99) : (statusDetail?.range_high || openPrice * 1.01));
                    const targetPrice = activeDetail?.take_profit || (pos.qty > 0 ? openPrice * 1.015 : openPrice * 0.985);

                    // PnL percent calculations
                    const pnlPct = ((ltpPrice - openPrice) / openPrice * 100) * (pos.qty > 0 ? 1 : -1);

                    // Risk Reward Ratio calculation
                    const riskRange = Math.abs(openPrice - slPrice);
                    const currentRiskRange = Math.abs(ltpPrice - openPrice);
                    const currentRR = riskRange > 0 ? (currentRiskRange / riskRange).toFixed(2) : "0.00";

                    // Visual progress calculations
                    let progressPercent = 0;
                    if (pos.qty > 0) {
                      // LONG: SL = left, target = right
                      const totalWidth = targetPrice - slPrice;
                      progressPercent = totalWidth > 0 ? ((ltpPrice - slPrice) / totalWidth) * 100 : 50;
                    } else {
                      // SHORT: SL = left (high), target = right (low)
                      const totalWidth = slPrice - targetPrice;
                      progressPercent = totalWidth > 0 ? ((slPrice - ltpPrice) / totalWidth) * 100 : 50;
                    }
                    progressPercent = Math.max(0, Math.min(100, progressPercent));

                    return (
                      <div key={pos.symbol} className="bg-slate-950 border border-slate-850 rounded-xl p-5 shadow-lg relative overflow-hidden flex flex-col justify-between">
                        <div className={`absolute left-0 top-0 bottom-0 w-1 ${pos.unrealized_pnl >= 0 ? "bg-emerald-500" : "bg-rose-500"}`} />
                        <div>
                          <div className="flex items-center justify-between mb-3 border-b border-slate-900 pb-2">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-black text-slate-100">{pos.symbol}</span>
                              <span className={`text-[9px] font-black px-2 py-0.5 rounded-full ${
                                pos.qty > 0 ? "bg-emerald-500/10 text-emerald-400" : "bg-rose-500/10 text-rose-400"
                              }`}>
                                {directionText}
                              </span>
                              <span className="text-[9px] bg-slate-900 text-slate-400 px-1.5 py-0.5 rounded border border-slate-850 font-medium">
                                ORB Strategy
                              </span>
                            </div>
                            <div className="text-right">
                              <span className={`text-sm font-extrabold block font-mono ${pos.unrealized_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                                ₹{pos.unrealized_pnl.toFixed(2)}
                              </span>
                              <span className={`text-[9px] block font-mono ${pnlPct >= 0 ? "text-emerald-500" : "text-rose-500"}`}>
                                {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                              </span>
                            </div>
                          </div>

                          <div className="grid grid-cols-4 gap-3 text-[10px] text-slate-400 font-mono mb-3">
                            <div>
                              <span className="block text-[8px] text-slate-500 font-bold uppercase mb-0.5">Entry Price</span>
                              <span className="text-slate-200">₹{openPrice.toFixed(2)}</span>
                            </div>
                            <div>
                              <span className="block text-[8px] text-slate-500 font-bold uppercase mb-0.5">Quantity</span>
                              <span className="text-slate-200">{Math.abs(pos.qty)}</span>
                            </div>
                            <div>
                              <span className="block text-[8px] text-slate-500 font-bold uppercase mb-0.5">Current LTP</span>
                              <span className="text-white font-extrabold">₹{ltpPrice.toFixed(2)}</span>
                            </div>
                            <div>
                              <span className="block text-[8px] text-slate-500 font-bold uppercase mb-0.5">Current RR</span>
                              <span className="text-cyan-400 font-bold">{currentRR} R</span>
                            </div>
                          </div>

                          {/* Visual progress bar: SL -> Entry -> Target */}
                          <div className="mb-4">
                            <div className="flex justify-between text-[9px] text-slate-500 font-mono mb-1">
                              <span className="text-rose-400 font-bold">SL: ₹{slPrice.toFixed(1)}</span>
                              <span className="text-slate-400">Entry: ₹{openPrice.toFixed(1)}</span>
                              <span className="text-emerald-400 font-bold">Tgt: ₹{targetPrice.toFixed(1)}</span>
                            </div>
                            <div className="w-full h-1.5 bg-slate-900 rounded-full relative overflow-hidden">
                              {/* Draw Entry Price marker block */}
                              <div className="absolute left-[33%] top-0 bottom-0 w-0.5 bg-slate-500 z-10" />
                              <div 
                                className={`h-full rounded-full transition-all duration-300 ${pos.unrealized_pnl >= 0 ? "bg-emerald-500/70" : "bg-rose-500/70"}`}
                                style={{ width: `${progressPercent}%` }}
                              />
                            </div>
                          </div>
                        </div>

                        <div className="pt-3 border-t border-slate-900 flex items-center justify-between text-[9px] font-mono">
                          <span className="text-slate-500 font-medium">Realized: ₹{pos.realized_pnl.toFixed(2)}</span>
                          <span className="text-slate-500 font-medium">Time: {activeDetail?.entry_time ? activeDetail.entry_time.split("T")[1]?.slice(0, 8) : "09:30:00"}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="text-center py-10 bg-slate-950/30 border border-dashed border-slate-850 rounded-xl text-slate-500 text-xs font-medium">
                  No Active Positions
                </div>
              )}
            </div>

            {/* Live activity logs */}
            <div className="bg-slate-900/60 border border-slate-850 rounded-xl p-5 shadow-lg">
              <h3 className="text-xs font-black text-slate-400 uppercase tracking-wider mb-4 flex items-center gap-2">
                <Clock className="w-4 h-4 text-cyan-400" /> Real-time activity pulse
              </h3>
              <div className="bg-slate-950 border border-slate-900 rounded-lg p-3 max-h-48 overflow-y-auto space-y-1.5 font-mono text-[10px]">
                {eventLog.length > 0 ? (
                  eventLog.map((log) => (
                    <div key={log.event_id} className="flex items-start justify-between py-1 border-b border-slate-900/60 hover:bg-slate-900/30">
                      <div className="flex items-center gap-3">
                        <span className="text-cyan-500 font-bold">[{log.received_timestamp.split("T")[1]?.slice(0, 8)}]</span>
                        <span className="text-slate-350">{log.symbol} tick parsed</span>
                        <span className="text-slate-500">LTP:</span>
                        <span className="text-slate-200 font-bold">₹{log.ltp.toFixed(2)}</span>
                        <span className="text-slate-500">Vol:</span>
                        <span className="text-slate-400">{log.volume.toLocaleString()}</span>
                      </div>
                      <span className="text-slate-600 text-[8px] uppercase tracking-wider">{log.correlation_id}</span>
                    </div>
                  ))
                ) : (
                  <div className="text-center py-4 text-slate-600">
                    Awaiting live market stream packets...
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* PAGE 2: LIVE POSITIONS DETAIL */}
        {activePage === "positions" && (
          <div className="space-y-6">
            <div className="flex justify-between items-center pb-4 border-b border-slate-900">
              <div>
                <h2 className="text-xl font-extrabold text-white">Live Operations Center</h2>
                <p className="text-xs text-slate-500 mt-1">Monitor, adjust, and square off live intraday breakouts</p>
              </div>
            </div>

            {/* Aggregate parameters */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
              <div className="bg-slate-900/60 border border-slate-850 rounded-xl p-5 shadow-lg text-center">
                <span className="block text-[9px] text-slate-500 font-black uppercase tracking-wider mb-1">Open Positions</span>
                <span className="text-2xl font-black text-white font-mono">{Object.keys(strategyReport?.positions || {}).length}</span>
              </div>
              <div className="bg-slate-900/60 border border-slate-850 rounded-xl p-5 shadow-lg text-center">
                <span className="block text-[9px] text-slate-500 font-black uppercase tracking-wider mb-1">Margin In Use</span>
                <span className="text-2xl font-black text-cyan-400 font-mono">₹{utilizedMargin.toLocaleString("en-IN")}</span>
              </div>
              <div className="bg-slate-900/60 border border-slate-850 rounded-xl p-5 shadow-lg text-center">
                <span className="block text-[9px] text-slate-500 font-black uppercase tracking-wider mb-1">Total Open Risk P&L</span>
                <span className={`text-2xl font-black font-mono ${Object.values(strategyReport?.positions || {}).reduce((s,p)=>s+p.unrealized_pnl,0) >= 0 ? "text-emerald-400" : "text-rose-450"}`}>
                  ₹{Object.values(strategyReport?.positions || {}).reduce((s,p)=>s+p.unrealized_pnl,0).toFixed(2)}
                </span>
              </div>
            </div>

            {/* Position expansion list */}
            {Object.keys(strategyReport?.positions || {}).length > 0 ? (
              <div className="space-y-4">
                {Object.values(strategyReport!.positions).map(pos => {
                  const statusDetail = symbolsStatus[pos.symbol];
                  const activeDetail = (statusDetail as any)?.active_trade_detail;
                  const directionText = pos.qty > 0 ? "LONG" : "SHORT";
                  
                  const openPrice = activeDetail?.entry_price || pos.avg_price;
                  const ltpPrice = statusDetail?.last_ltp || pos.avg_price;
                  const slPrice = activeDetail?.stop_loss || (pos.qty > 0 ? (statusDetail?.range_low || openPrice * 0.99) : (statusDetail?.range_high || openPrice * 1.01));
                  const targetPrice = activeDetail?.take_profit || (pos.qty > 0 ? openPrice * 1.015 : openPrice * 0.985);
                  
                  const pnlPct = ((ltpPrice - openPrice) / openPrice * 100) * (pos.qty > 0 ? 1 : -1);

                  return (
                    <div key={pos.symbol} className="bg-slate-950 border border-slate-850 rounded-xl p-6 shadow-lg flex flex-col md:flex-row md:items-center justify-between gap-6 relative overflow-hidden">
                      <div className={`absolute left-0 top-0 bottom-0 w-1.5 ${pos.unrealized_pnl >= 0 ? "bg-emerald-500" : "bg-rose-550"}`} />
                      
                      <div className="flex items-center gap-4.5">
                        <div className="p-3 bg-slate-900 border border-slate-850 rounded-xl">
                          <Activity className="w-6 h-6 text-cyan-400" />
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="text-base font-black text-white">{pos.symbol}</span>
                            <span className={`text-[9px] font-black px-2 py-0.5 rounded-full ${
                              pos.qty > 0 ? "bg-emerald-500/10 text-emerald-400" : "bg-rose-500/10 text-rose-450"
                            }`}>
                              {directionText}
                            </span>
                          </div>
                          <span className="text-[10px] text-slate-500 font-mono mt-1 block">Strategy: ORB Strategy | Entry Time: {activeDetail?.entry_time ? activeDetail.entry_time.split("T")[1]?.slice(0,8) : "09:30:00"}</span>
                        </div>
                      </div>

                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 font-mono text-xs">
                        <div>
                          <span className="block text-[8px] text-slate-500 font-bold uppercase mb-1">Entry Price</span>
                          <span className="text-slate-350">₹{openPrice.toFixed(2)}</span>
                        </div>
                        <div>
                          <span className="block text-[8px] text-slate-500 font-bold uppercase mb-1">Current LTP</span>
                          <span className="text-white font-extrabold">₹{ltpPrice.toFixed(2)}</span>
                        </div>
                        <div>
                          <span className="block text-[8px] text-slate-500 font-bold uppercase mb-1">Stop Loss</span>
                          <span className="text-rose-400">₹{slPrice.toFixed(2)}</span>
                        </div>
                        <div>
                          <span className="block text-[8px] text-slate-500 font-bold uppercase mb-1">Target</span>
                          <span className="text-emerald-400">₹{targetPrice.toFixed(2)}</span>
                        </div>
                      </div>

                      <div className="text-right flex flex-col items-end justify-center min-w-[100px]">
                        <span className={`text-base font-black font-mono ${pos.unrealized_pnl >= 0 ? "text-emerald-400" : "text-rose-450"}`}>
                          ₹{pos.unrealized_pnl.toFixed(2)}
                        </span>
                        <span className={`text-xs font-mono font-bold block ${pnlPct >= 0 ? "text-emerald-500" : "text-rose-450"}`}>
                          {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="text-center py-20 bg-slate-900/20 border border-dashed border-slate-850 rounded-2xl text-slate-500 text-xs">
                No active strategy positions open
              </div>
            )}
          </div>
        )}

        {/* PAGE 3: CAPITAL ALLOCATION MODULATOR */}
        {activePage === "allocation" && (
          <div className="space-y-6">
            <div className="flex justify-between items-center pb-4 border-b border-slate-900">
              <div>
                <h2 className="text-xl font-extrabold text-white">Capital Allocation Modulator</h2>
                <p className="text-xs text-slate-500 mt-1">Configure portfolio capital splitting rules dynamically</p>
              </div>
            </div>

            {/* Static Config Buttons */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-5">
              {[
                { label: "Option 1 (100%)", text: "Single trade block. 100% of capital pool in one signal.", weights: [1.0, 0.0, 0.0] },
                { label: "Option 2 (50 / 50%)", text: "Two trade block. Capital split equally between top two signals.", weights: [0.5, 0.5, 0.0] },
                { label: "Option 3 (33 / 33 / 33%)", text: "Three trade split. Equal capital blocks for top three priorities.", weights: [0.33, 0.33, 0.33] },
                { label: "Option 4 (25 / 25 / 25 / 25%)", text: "Four trade split. Equal distribution for top four ranks.", weights: [0.25, 0.25, 0.25, 0.25] },
              ].map((opt, i) => {
                // Determine matches
                const isSelected = weights.length === opt.weights.length && weights.every((w, idx) => Math.abs(w - opt.weights[idx]) < 0.05);
                return (
                  <div 
                    key={i} 
                    onClick={() => saveAllocationWeights(opt.weights)}
                    className={`bg-slate-900/60 border rounded-xl p-5 shadow-lg flex flex-col justify-between h-40 cursor-pointer transition duration-150 ${
                      isSelected 
                        ? "border-cyan-500/60 bg-cyan-950/10" 
                        : "border-slate-850 hover:border-slate-800"
                    }`}
                  >
                    <div>
                      <h4 className="text-xs font-black text-slate-200 uppercase">{opt.label}</h4>
                      <p className="text-[10px] text-slate-500 mt-2 leading-relaxed">{opt.text}</p>
                    </div>
                    <div className="flex items-center gap-1">
                      {opt.weights.map((w, idx) => w > 0 ? (
                        <span key={idx} className="text-[9px] font-black px-1.5 py-0.5 bg-slate-950 border border-slate-900 rounded font-mono text-cyan-400">
                          {(w * 100).toFixed(0)}%
                        </span>
                      ) : null)}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Custom Sliders grid */}
            <div className="bg-slate-900/60 border border-slate-850 rounded-xl p-6 shadow-lg">
              <div className="flex items-center justify-between mb-4 border-b border-slate-900 pb-3">
                <h3 className="text-xs font-black text-slate-350 uppercase tracking-wider">Custom allocation weights editor</h3>
                <span className={`text-[10px] font-black font-mono px-3 py-1 rounded bg-slate-950 border border-slate-900 ${
                  Math.abs(weights.reduce((s,w)=>s+w,0) - 1.0) < 0.01 ? "text-emerald-400" : "text-amber-400"
                }`}>
                  Total Weight: {(weights.reduce((s,w)=>s+w,0) * 100).toFixed(0)}% (Requires 100% total)
                </span>
              </div>

              <div className="space-y-5 max-w-2xl">
                {[0, 1, 2].map(rank => (
                  <div key={rank} className="flex items-center justify-between gap-6">
                    <span className="text-xs text-slate-400 font-bold w-24">Rank {rank + 1} Alloc:</span>
                    <input 
                      type="range"
                      min="0"
                      max="100"
                      value={Math.round((weights[rank] || 0) * 100)}
                      onChange={e => {
                        const copy = [...weights];
                        copy[rank] = parseFloat(e.target.value) / 100;
                        setWeights(copy);
                      }}
                      className="flex-1 accent-cyan-500 h-1 bg-slate-900 rounded-lg cursor-pointer"
                    />
                    <div className="flex items-center gap-1.5 w-16">
                      <input 
                        type="number"
                        min="0"
                        max="100"
                        value={Math.round((weights[rank] || 0) * 100)}
                        onChange={e => {
                          const copy = [...weights];
                          copy[rank] = (parseInt(e.target.value) || 0) / 100;
                          setWeights(copy);
                        }}
                        className="w-12 bg-slate-950 border border-slate-900 rounded px-2 py-1 text-xs text-center font-bold text-cyan-400 outline-none"
                      />
                      <span className="text-[10px] text-slate-500 font-bold">%</span>
                    </div>
                  </div>
                ))}
              </div>

              <div className="mt-6 pt-4 border-t border-slate-900 flex justify-end">
                <button
                  onClick={() => saveAllocationWeights(weights)}
                  className="px-5 py-2 bg-cyan-600 hover:bg-cyan-500 active:scale-95 transition text-white font-extrabold text-xs rounded-lg shadow-md cursor-pointer"
                >
                  Save Custom Allocations
                </button>
              </div>
            </div>
          </div>
        )}

        {/* PAGE 4: VIRTUALIZED TRADE HISTORY */}
        {activePage === "history" && (
          <div className="space-y-6">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-slate-900">
              <div>
                <h2 className="text-xl font-extrabold text-white">Execution Logs Terminal</h2>
                <p className="text-xs text-slate-500 mt-1">High-performance virtualized grid displaying historical trade logs</p>
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={exportCSV}
                  className="flex items-center gap-2 px-4 py-2 bg-slate-900 border border-slate-850 hover:bg-slate-800 text-slate-200 font-bold text-xs rounded-lg cursor-pointer transition duration-150"
                >
                  <Download className="w-3.5 h-3.5" /> Export CSV
                </button>
                <button
                  onClick={exportExcel}
                  className="flex items-center gap-2 px-4 py-2 bg-slate-900 border border-slate-850 hover:bg-slate-800 text-slate-200 font-bold text-xs rounded-lg cursor-pointer transition duration-150"
                >
                  <Download className="w-3.5 h-3.5" /> Export Excel
                </button>
              </div>
            </div>

            {/* Filter Bar */}
            <div className="bg-slate-900/60 border border-slate-850 rounded-xl p-5 shadow-lg grid grid-cols-1 sm:grid-cols-5 gap-4">
              <div className="relative">
                <Search className="w-3.5 h-3.5 text-slate-500 absolute left-3 top-1/2 transform -translate-y-1/2" />
                <input 
                  type="text"
                  placeholder="Search symbol, reason..."
                  value={searchTerm}
                  onChange={e => setSearchTerm(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-850 rounded-lg pl-9 pr-3 py-2 text-xs font-medium text-slate-200 outline-none focus:border-cyan-500 transition duration-150"
                />
              </div>

              <div>
                <select 
                  value={filterSymbol}
                  onChange={e => setFilterSymbol(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-xs font-medium text-slate-300 outline-none focus:border-cyan-500 cursor-pointer"
                >
                  <option value="ALL">All Symbols</option>
                  {uniqueSymbols.filter(s => s !== "ALL").map(sym => (
                    <option key={sym} value={sym}>{sym}</option>
                  ))}
                </select>
              </div>

              <div>
                <select 
                  value={filterMonth}
                  onChange={e => setFilterMonth(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-xs font-medium text-slate-300 outline-none focus:border-cyan-500 cursor-pointer"
                >
                  <option value="ALL">All Months</option>
                  {uniqueMonths.filter(m => m !== "ALL").map(month => (
                    <option key={month} value={month}>{month}</option>
                  ))}
                </select>
              </div>

              <div>
                <select 
                  value={filterStrategy}
                  onChange={e => setFilterStrategy(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-xs font-medium text-slate-300 outline-none focus:border-cyan-500 cursor-pointer"
                >
                  <option value="ALL">All Strategies</option>
                  <option value="ORB">ORB Strategy</option>
                </select>
              </div>

              <div className="text-right flex items-center justify-end text-xs text-slate-500 font-mono">
                Showing {sortedAndFilteredHistory.length} of {allCompletedTrades.length} trades
              </div>
            </div>

            {/* Virtualized Table */}
            <div className="bg-slate-900/60 border border-slate-850 rounded-xl overflow-hidden shadow-lg">
              <div className="overflow-x-auto">
                <div className="min-w-[1500px] flex flex-col">
                  {/* Table Header */}
                  <div className="bg-slate-950 text-[10px] uppercase font-black tracking-wider text-slate-500 flex items-center py-3 border-b border-slate-900 select-none">
                    {[
                      { col: "Trade_ID", label: "Trade ID", width: "w-20" },
                      { col: "Symbol", label: "Symbol", width: "w-24" },
                      { col: "Setup", label: "Strategy", width: "w-28" },
                      { col: "Direction", label: "Direction", width: "w-24" },
                      { col: "Entry_Time", label: "Entry Date/Time", width: "w-44" },
                      { col: "Exit_Time", label: "Exit Date/Time", width: "w-44" },
                      { col: "Qty", label: "Quantity", width: "w-20" },
                      { col: "Entry_Price", label: "Entry Price", width: "w-28" },
                      { col: "Exit_Price", label: "Exit Price", width: "w-28" },
                      { col: "Gross_PnL", label: "Gross P/L", width: "w-28" },
                      { col: "Fees", label: "Charges", width: "w-24" },
                      { col: "Net_PnL", label: "Net P/L", width: "w-28" },
                      { col: "Exit_Reason", label: "Exit Reason", width: "w-36" }
                    ].map(h => (
                      <div 
                        key={h.col} 
                        onClick={() => handleHeaderClick(h.col)}
                        className={`${h.width} flex-shrink-0 px-3 cursor-pointer hover:text-white flex items-center gap-1.5`}
                      >
                        <span>{h.label}</span>
                        {sortColumn === h.col && (
                          <span>{sortAsc ? "▲" : "▼"}</span>
                        )}
                      </div>
                    ))}
                  </div>

                  {/* Virtualized Scroll container */}
                  <div 
                    onScroll={handleTableScroll}
                    className="overflow-y-auto max-h-[520px] relative bg-slate-950/40"
                    style={{ height: `${viewportHeight}px` }}
                  >
                    {sortedAndFilteredHistory.length > 0 ? (
                      <div style={{ height: `${totalHeight}px`, width: "100%", position: "relative" }}>
                        <div 
                          className="absolute left-0 w-full flex flex-col" 
                          style={{ transform: `translateY(${offsetTop}px)` }}
                        >
                          {visibleSlice.map(t => {
                            const isProfit = (t.Net_PnL || 0) >= 0;
                            const entryDate = t.Entry_Time?.split("T")[0] || "";
                            const entryTime = t.Entry_Time?.split("T")[1]?.slice(0, 8) || "";
                            const exitDate = t.Exit_Time?.split("T")[0] || "";
                            const exitTime = t.Exit_Time?.split("T")[1]?.slice(0, 8) || "";
                            const gross = t.Gross_PnL || 0;
                            const charges = t.Fees || 0;
                            const net = t.Net_PnL || 0;
                            
                            return (
                              <div 
                                key={t.Trade_ID}
                                className="flex items-center text-xs font-semibold py-2.5 border-b border-slate-900/60 hover:bg-slate-900/40 text-slate-350 transition duration-100"
                                style={{ height: `${rowHeight}px` }}
                              >
                                <div className="w-20 flex-shrink-0 px-3 font-mono text-[10px] text-slate-500">#{t.Trade_ID}</div>
                                <div className="w-24 flex-shrink-0 px-3 font-black text-slate-100">{t.Symbol}</div>
                                <div className="w-28 flex-shrink-0 px-3 font-mono text-[10px] text-slate-400">{t.Setup || "ORB"}</div>
                                <div className="w-24 flex-shrink-0 px-3">
                                  <span className={`text-[9px] font-black px-2 py-0.5 rounded ${
                                    t.Direction === "LONG" ? "bg-emerald-500/10 text-emerald-400" : "bg-rose-500/10 text-rose-450"
                                  }`}>
                                    {t.Direction}
                                  </span>
                                </div>
                                <div className="w-44 flex-shrink-0 px-3 font-mono text-[10px] leading-tight">
                                  <span className="text-slate-300 block">{entryDate}</span>
                                  <span className="text-slate-500">{entryTime}</span>
                                </div>
                                <div className="w-44 flex-shrink-0 px-3 font-mono text-[10px] leading-tight">
                                  <span className="text-slate-300 block">{exitDate}</span>
                                  <span className="text-slate-500">{exitTime}</span>
                                </div>
                                <div className="w-20 flex-shrink-0 px-3 font-mono text-slate-300">{t.Qty}</div>
                                <div className="w-28 flex-shrink-0 px-3 font-mono">₹{t.Entry_Price.toFixed(2)}</div>
                                <div className="w-28 flex-shrink-0 px-3 font-mono">₹{t.Exit_Price.toFixed(2)}</div>
                                <div className={`w-28 flex-shrink-0 px-3 font-mono ${gross >= 0 ? "text-emerald-400" : "text-rose-400"}`}>₹{gross.toFixed(2)}</div>
                                <div className="w-24 flex-shrink-0 px-3 font-mono text-rose-400/90">₹{charges.toFixed(2)}</div>
                                <div className={`w-28 flex-shrink-0 px-3 font-mono font-bold ${isProfit ? "text-emerald-400" : "text-rose-400"}`}>₹{net.toFixed(2)}</div>
                                <div className="w-36 flex-shrink-0 px-3 font-semibold text-slate-400 text-[10px] truncate">{t.Exit_Reason || "Target"}</div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ) : (
                      <div className="text-center py-20 text-slate-500 text-xs">
                        No executions matched the current search criteria
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* PAGE 5: TRADING CALENDAR */}
        {activePage === "calendar" && (
          <div className="space-y-6">
            <div className="flex justify-between items-center pb-4 border-b border-slate-900">
              <div>
                <h2 className="text-xl font-extrabold text-white">Daily Heatmap Terminal</h2>
                <p className="text-xs text-slate-500 mt-1">Spaced month-by-month grids indicating trading profitability</p>
              </div>
            </div>

            {/* Grid for Months */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {[0, 1, 2, 3, 4, 5].map(offset => renderCalendarMonth(offset))}
            </div>

            {/* Daily drill down executions list drawer */}
            {selectedCalendarDay && (
              <div className="fixed inset-0 bg-slate-950/70 backdrop-blur-sm flex items-center justify-center z-50 p-4">
                <div className="bg-[#0b0f19] border border-slate-850 rounded-2xl w-full max-w-4xl p-6 shadow-2xl flex flex-col max-h-[80vh] animate-in fade-in zoom-in duration-200">
                  <div className="flex items-center justify-between border-b border-slate-900 pb-4 mb-4">
                    <div>
                      <h3 className="text-base font-extrabold text-white">Daily Executions Report</h3>
                      <p className="text-xs text-slate-500 mt-1">Displaying all trades completed on {selectedCalendarDay}</p>
                    </div>
                    <button 
                      onClick={() => setSelectedCalendarDay(null)}
                      className="text-slate-400 hover:text-white font-extrabold px-3.5 py-1.5 bg-slate-900 hover:bg-slate-800 rounded-lg cursor-pointer text-xs transition duration-150"
                    >
                      Close Drawer
                    </button>
                  </div>

                  <div className="overflow-y-auto flex-1 rounded-lg border border-slate-900">
                    <table className="w-full text-left border-collapse">
                      <thead>
                        <tr className="bg-slate-950 text-[10px] uppercase font-bold text-slate-500 border-b border-slate-900">
                          <th className="p-3">ID</th>
                          <th className="p-3">Symbol</th>
                          <th className="p-3">Direction</th>
                          <th className="p-3">Entry Time</th>
                          <th className="p-3">Exit Time</th>
                          <th className="p-3">Quantity</th>
                          <th className="p-3">Entry Price</th>
                          <th className="p-3">Exit Price</th>
                          <th className="p-3">Charges</th>
                          <th className="p-3">Net PnL</th>
                          <th className="p-3">Exit Reason</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-900/50 text-xs">
                        {selectedDayTrades.map(t => {
                          const isProfit = (t.Net_PnL || 0) >= 0;
                          return (
                            <tr key={t.Trade_ID} className="hover:bg-slate-900/40 text-slate-300">
                              <td className="p-3 font-mono text-[10px] text-slate-500">#{t.Trade_ID}</td>
                              <td className="p-3 font-black text-white">{t.Symbol}</td>
                              <td className="p-3">
                                <span className={`text-[9px] font-black px-2 py-0.5 rounded ${
                                  t.Direction === "LONG" ? "bg-emerald-500/10 text-emerald-400" : "bg-rose-500/10 text-rose-450"
                                }`}>
                                  {t.Direction}
                                </span>
                              </td>
                              <td className="p-3 font-mono text-slate-400">{t.Entry_Time?.split("T")[1]?.slice(0, 8)}</td>
                              <td className="p-3 font-mono text-slate-400">{t.Exit_Time?.split("T")[1]?.slice(0, 8)}</td>
                              <td className="p-3 font-mono">{t.Qty}</td>
                              <td className="p-3 font-mono">₹{t.Entry_Price.toFixed(2)}</td>
                              <td className="p-3 font-mono">₹{t.Exit_Price.toFixed(2)}</td>
                              <td className="p-3 font-mono text-rose-450">₹{t.Fees.toFixed(2)}</td>
                              <td className={`p-3 font-black font-mono ${isProfit ? "text-emerald-400" : "text-rose-400"}`}>₹{t.Net_PnL.toFixed(2)}</td>
                              <td className="p-3 text-slate-400 font-semibold">{t.Exit_Reason}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* PAGE 6: MARKET WATCH MONITOR */}
        {activePage === "watch" && (
          <div className="space-y-6">
            <div className="flex justify-between items-center pb-4 border-b border-slate-900">
              <div>
                <h2 className="text-xl font-extrabold text-white">Live Watchlist Monitor</h2>
                <p className="text-xs text-slate-500 mt-1">Real-time quote and ORB breakout levels tracking panel</p>
              </div>
            </div>

            {/* Symbols Table */}
            <div className="bg-slate-900/60 border border-slate-850 rounded-xl overflow-hidden shadow-lg">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-slate-950 text-[10px] uppercase font-black tracking-wider text-slate-500 border-b border-slate-900">
                    <th className="p-4">Symbol</th>
                    <th className="p-4">Feed Status</th>
                    <th className="p-4">Open</th>
                    <th className="p-4">High</th>
                    <th className="p-4">Low</th>
                    <th className="p-4">LTP (Current)</th>
                    <th className="p-4">Close (Prev)</th>
                    <th className="p-4">Volume</th>
                    <th className="p-4">Today's %</th>
                    <th className="p-4">ORB Range Levels</th>
                    <th className="p-4">Last Updated</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-900/50 text-xs">
                  {localSymbols.map(sym => {
                    const s = symbolsStatus[sym];
                    const hasWarning = s?.warning || !connectionOk;
                    const changePct = s?.open ? ((s.last_ltp - s.open) / s.open * 100) : 0;
                    
                    return (
                      <tr key={sym} className="hover:bg-slate-900/30 text-slate-300">
                        <td className="p-4 font-black text-white text-sm">{sym}</td>
                        <td className="p-4">
                          {s?.offline ? (
                            <span className="text-[9px] bg-amber-500/10 text-amber-500 border border-amber-500/20 px-2 py-0.5 rounded font-black uppercase">Offline</span>
                          ) : hasWarning ? (
                            <span className="text-[9px] bg-rose-500/10 text-rose-450 border border-rose-500/20 px-2 py-0.5 rounded font-black uppercase">No Feed</span>
                          ) : s?.active_trade ? (
                            <span className="text-[9px] bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 px-2 py-0.5 rounded font-black uppercase">In Trade</span>
                          ) : s?.range_established ? (
                            <span className="text-[9px] bg-emerald-500/10 text-emerald-450 border border-emerald-500/20 px-2 py-0.5 rounded font-black uppercase">Range Set</span>
                          ) : isRunning ? (
                            <span className="text-[9px] bg-slate-805 text-slate-400 border border-slate-800 px-2 py-0.5 rounded font-black uppercase">Tracking</span>
                          ) : (
                            <span className="text-[9px] bg-slate-950 text-slate-650 px-2 py-0.5 rounded font-black uppercase">Inactive</span>
                          )}
                        </td>
                        <td className="p-4 font-mono">
                          {s?.offline ? (
                            <span className="text-slate-500 font-bold uppercase tracking-widest text-[9px]">Offline</span>
                          ) : (
                            `₹${s?.open ? s.open.toFixed(2) : "0.00"}`
                          )}
                        </td>
                        <td className="p-4 font-mono text-emerald-400">
                          {s?.offline ? (
                            <span className="text-slate-500 font-bold uppercase tracking-widest text-[9px]">Offline</span>
                          ) : (
                            `₹${s?.high ? s.high.toFixed(2) : "0.00"}`
                          )}
                        </td>
                        <td className="p-4 font-mono text-rose-450">
                          {s?.offline ? (
                            <span className="text-slate-500 font-bold uppercase tracking-widest text-[9px]">Offline</span>
                          ) : (
                            `₹${s?.low ? s.low.toFixed(2) : "0.00"}`
                          )}
                        </td>
                        <td className="p-4 font-mono font-black text-white">
                          {s?.offline ? (
                            <span className="text-slate-500 font-bold uppercase tracking-widest text-[9px]">Offline</span>
                          ) : (
                            `₹${s?.last_ltp ? s.last_ltp.toFixed(2) : "0.00"}`
                          )}
                        </td>
                        <td className="p-4 font-mono text-slate-500">₹{s?.close ? s.close.toFixed(2) : "0.00"}</td>
                        <td className="p-4 font-mono text-slate-400">{s?.volume ? s.volume.toLocaleString() : "0"}</td>
                        <td className={`p-4 font-mono font-black ${changePct >= 0 ? "text-emerald-400" : "text-rose-450"}`}>
                          {changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%
                        </td>
                        <td className="p-4 font-mono text-cyan-400 font-bold">
                          {s?.range_established && s.range_low !== null && s.range_high !== null ? `₹${s.range_low.toFixed(1)} - ₹${s.range_high.toFixed(1)}` : "Computing..."}
                        </td>
                        <td className="p-4 font-mono text-slate-500 text-[10px]">
                          {hasWarning ? "Connection Lost" : "Just now"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* PAGE 7: SYSTEM CONFIG SETTINGS */}
        {activePage === "settings" && (
          <div className="space-y-6">
            <div className="flex justify-between items-center pb-4 border-b border-slate-900">
              <div>
                <h2 className="text-xl font-extrabold text-white">Configuration Dashboard</h2>
                <p className="text-xs text-slate-500 mt-1">Configure, adjust, and persist trading strategies parameters</p>
              </div>
            </div>

            <form onSubmit={handleSaveSettings} className="bg-slate-900/60 border border-slate-850 rounded-xl p-6 shadow-lg space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div>
                  <label className="block text-[10px] font-black uppercase text-slate-500 tracking-wider mb-2">Default Starting Capital Pool (INR)</label>
                  <input 
                    type="number" 
                    value={capital}
                    onChange={e => setCapital(parseInt(e.target.value) || 0)}
                    className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-200 outline-none focus:border-cyan-500 transition duration-150"
                  />
                  <span className="text-[10px] text-slate-500 mt-1 block">Determines the allocation pool split calculations.</span>
                </div>

                <div>
                  <label className="block text-[10px] font-black uppercase text-slate-500 tracking-wider mb-2">Intraday Leverage Multiplier</label>
                  <input 
                    type="number" 
                    value={leverage}
                    onChange={e => setLeverage(parseInt(e.target.value) || 1)}
                    className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-200 outline-none focus:border-cyan-500 transition duration-150"
                  />
                  <span className="text-[10px] text-slate-500 mt-1 block">Intraday margin multiplier (defaults to 5x NSE rules).</span>
                </div>

                <div>
                  <label className="block text-[10px] font-black uppercase text-slate-500 tracking-wider mb-2">Theme Mode</label>
                  <select 
                    value={themeMode}
                    onChange={e => setThemeMode(e.target.value as any)}
                    className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-350 outline-none focus:border-cyan-500 cursor-pointer"
                  >
                    <option value="dark">Professional Slate Dark</option>
                    <option value="glass">Vibrant Glassmorphism Neon</option>
                    <option value="nature">Organic Nature Beige</option>
                  </select>
                </div>

                <div>
                  <label className="block text-[10px] font-black uppercase text-slate-500 tracking-wider mb-2">Live Stock Feeds (Dhan Data API)</label>
                  <div className="flex items-center mt-2">
                    <input 
                      type="checkbox"
                      id="enableLiveStocks"
                      checked={enableLiveStocks}
                      onChange={e => setEnableLiveStocks(e.target.checked)}
                      className="w-4.5 h-4.5 rounded bg-slate-950 border border-slate-850 text-cyan-500 focus:ring-cyan-500 cursor-pointer accent-cyan-500"
                    />
                    <label htmlFor="enableLiveStocks" className="ml-2 text-xs text-slate-300 cursor-pointer">
                      Enable Live Stock Feeds
                    </label>
                  </div>
                  <span className="text-[9px] text-slate-500 mt-1 block">Requires manual activation of ₹499/mo Data API on Dhan portal. If disabled, index feeds (Nifty/Bank Nifty) stream for free.</span>
                </div>

                <div>
                  <label className="block text-[10px] font-black uppercase text-slate-500 tracking-wider mb-2">Refresh Interval (ms)</label>
                  <input 
                    type="number" 
                    value={refreshInterval}
                    onChange={e => setRefreshInterval(parseInt(e.target.value) || 1000)}
                    className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-200 outline-none focus:border-cyan-500 transition duration-150"
                  />
                </div>

                <div>
                  <label className="block text-[10px] font-black uppercase text-slate-500 tracking-wider mb-2">Default Strategy Module</label>
                  <select 
                    value={defaultStrategy}
                    onChange={e => setDefaultStrategy(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-350 outline-none focus:border-cyan-500 cursor-pointer"
                  >
                    <option value="ORB">Opening Range Breakout (ORB)</option>
                  </select>
                </div>

                <div>
                  <label className="block text-[10px] font-black uppercase text-slate-500 tracking-wider mb-2">System Audio Alerts</label>
                  <label className="flex items-center gap-3 mt-2 cursor-pointer">
                    <input 
                      type="checkbox"
                      checked={audioAlerts}
                      onChange={e => setAudioAlerts(e.target.checked)}
                      className="accent-cyan-500 w-4 h-4 rounded cursor-pointer"
                    />
                    <span className="text-xs text-slate-300 font-bold">Enable Chime alerts on fills and trade closures</span>
                  </label>
                </div>

                <div>
                  <label className="block text-[10px] font-black uppercase text-slate-500 tracking-wider mb-2">Max Concurrent Trades</label>
                  <input 
                    type="number" 
                    value={maxConcurrentTrades}
                    onChange={e => setMaxConcurrentTrades(parseInt(e.target.value) || 1)}
                    className="w-full bg-slate-950 border border-slate-850 rounded-lg px-3 py-2 text-xs text-slate-200 outline-none focus:border-cyan-500 transition duration-150"
                  />
                  <span className="text-[10px] text-slate-500 mt-1 block">Maximum concurrent positions (strategy blocks excess triggers).</span>
                </div>
              </div>

              {/* Priority watchlist items configuration */}
              <div className="pt-4 border-t border-slate-900">
                <label className="block text-[10px] font-black uppercase text-slate-500 tracking-wider mb-3">Priority Watchlist Management</label>
                <div className="flex gap-2 max-w-md mb-3">
                  <input
                    type="text"
                    value={newSymbolInput}
                    onChange={e => setNewSymbolInput(e.target.value.toUpperCase())}
                    placeholder="Enter stock symbol (e.g. RELIANCE)"
                    className="flex-1 bg-slate-950 border border-slate-850 rounded-lg px-3.5 py-2 text-xs font-semibold text-slate-200 outline-none focus:border-cyan-500 transition"
                  />
                  <button
                    type="button"
                    onClick={addSymbol}
                    className="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 active:scale-95 transition text-white font-extrabold text-xs rounded-lg cursor-pointer flex items-center gap-1.5"
                  >
                    <Plus className="w-3.5 h-3.5" /> Add
                  </button>
                </div>

                <div className="space-y-2 max-h-60 overflow-y-auto pt-1 max-w-md">
                  {priorityRanking.map((sym, idx) => (
                    <div key={sym} className="flex items-center justify-between p-2 rounded-lg bg-slate-950/80 border border-slate-900">
                      <div className="flex items-center gap-3">
                        <span className="text-[9px] text-slate-500 font-mono">Rank #{idx+1}</span>
                        <span className="text-xs font-black text-white">{sym}</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => movePriority(idx, "UP")}
                          disabled={idx === 0}
                          className="p-1 rounded bg-slate-900 border border-slate-850 hover:bg-slate-800 disabled:opacity-30 disabled:pointer-events-none cursor-pointer text-slate-400"
                        >
                          <ArrowUpCircle className="w-3.5 h-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => movePriority(idx, "DOWN")}
                          disabled={idx === priorityRanking.length - 1}
                          className="p-1 rounded bg-slate-900 border border-slate-850 hover:bg-slate-800 disabled:opacity-30 disabled:pointer-events-none cursor-pointer text-slate-400"
                        >
                          <ArrowDownCircle className="w-3.5 h-3.5" />
                        </button>
                        <button 
                          type="button" 
                          onClick={() => removeSymbol(sym)}
                          className="p-1 rounded bg-slate-900 border border-slate-850 hover:bg-rose-950/40 hover:text-rose-400 cursor-pointer transition text-slate-505"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="pt-4 border-t border-slate-900 flex justify-end">
                <button
                  type="submit"
                  className="px-6 py-2.5 bg-cyan-600 hover:bg-cyan-500 active:scale-95 transition text-white font-extrabold text-xs rounded-lg shadow-md cursor-pointer"
                >
                  Apply & Persist Configurations
                </button>
              </div>
            </form>
          </div>
        )}

        {/* PAGE 8: RESEARCH LAB SANDBOX WORKSPACE */}
        {activePage === "research" && (
          <ResearchLabWorkspace 
            symbols={localSymbols} 
            addToast={addToast} 
          />
        )}
      </main>

      {/* 3. FLOATING NON-BLOCKING NOTIFICATIONS TOAST BAR CONTAINER */}
      <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2.5 max-w-sm w-full pointer-events-none">
        {toasts.map(t => {
          let typeClass = "bg-slate-950/95 border-slate-850 text-cyan-400";
          let Icon = Activity;
          if (t.type === "success") {
            typeClass = "bg-emerald-950/95 border-emerald-900/60 text-emerald-400";
            Icon = CheckCircle;
          } else if (t.type === "error") {
            typeClass = "bg-rose-950/95 border-rose-900/60 text-rose-455";
            Icon = XCircle;
          } else if (t.type === "warning") {
            typeClass = "bg-amber-950/95 border-amber-900/60 text-amber-450";
            Icon = AlertTriangle;
          }

          return (
            <div 
              key={t.id} 
              className={`flex items-start gap-3 p-4 rounded-xl border shadow-2xl backdrop-blur-md pointer-events-auto animate-in slide-in-from-right duration-250 ${typeClass}`}
            >
              <div className="mt-0.5 flex-shrink-0">
                <Icon className="w-4.5 h-4.5" />
              </div>
              <div className="text-[11px] font-semibold leading-relaxed text-slate-200">
                {t.message}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default App;
