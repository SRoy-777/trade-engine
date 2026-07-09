import { useEffect, useState, useRef, useCallback } from "react";

export interface TelemetryMetrics {
  packets_per_sec: number;
  events_per_sec: number;
  bronze_buffer_size: number;
  silver_buffer_size: number;
  avg_parser_time_ms: number;
  avg_event_bus_time_ms: number;
  avg_pipeline_time_ms: number;
  total_packets: number;
  total_inserts: number;
  last_symbol: string;
  last_price: number;
  last_timestamp: string;
  replay_delay_secs: number;
}

export interface ProviderStatus {
  provider_name: string;
  status: string;
  speed: number;
  mode: string;
  packets_processed: number;
  last_symbol: string | null;
  last_price: number;
  last_timestamp: string | null;
  elapsed_time_secs: number;
  session_id: string | null;
  provider_status: string;
  connection_ok?: boolean;
  warning_symbols?: string[];
}

export interface MarketEventData {
  event_id: string;
  correlation_id: string;
  symbol: string;
  ltp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  exchange_timestamp: string | null;
  received_timestamp: string;
  processed_timestamp: string;
}

export interface SymbolStatusDetail {
  symbol: string;
  range_high: number | null;
  range_low: number | null;
  range_established: boolean;
  trade_taken: boolean;
  active_trade: boolean;
  active_trade_detail?: any;
  is_active: boolean;
  warning: boolean;
  last_ltp: number;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  volume?: number;
}

export interface ActivePositionDetail {
  symbol: string;
  qty: number;
  avg_price: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  leverage: number;
  capital_utilized: number;
}

export interface StrategyReport {
  pnl_inr: number;
  realized_pnl_inr: number;
  unrealized_pnl_inr: number;
  positions: Record<string, ActivePositionDetail>;
  cash_inr: number;
  net_asset_value_inr: number;
  total_fees_paid_inr: number;
}

export interface StrategyConfig {
  symbols: string[];
  priority_ranking: string[];
  allocation_strategy: string;
  allocation_weights: number[];
  capital: number;
  leverage: number;
}

export interface TradeHistoryRecord {
  Trade_ID: number;
  Symbol: string;
  Direction: string;
  Setup: string;
  Entry_Time: string;
  Entry_Price: number;
  Qty: number;
  Exit_Time: string;
  Exit_Price: number;
  Gross_PnL: number;
  Fees: number;
  Net_PnL: number;
  Exit_Reason: string;
  Hold_Time_Mins: number;
  Capital_Utilized: number;
  Leverage: number;
}

export interface WSMessage {
  type: string;
  metrics: TelemetryMetrics;
  status: ProviderStatus;
  latest_event: MarketEventData | null;
  symbols_status?: Record<string, SymbolStatusDetail>;
  trade_history?: TradeHistoryRecord[];
  strategy_report?: StrategyReport;
  configuration?: StrategyConfig;
  indices?: Record<string, { ltp: number; change_pct: number; trend: string; open: number }>;
}

const getWsUrl = (overrideUrl?: string): string => {
  if (overrideUrl) return overrideUrl;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host;
  
  if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
    if (window.location.port === "5173") {
      return "ws://localhost:8000/ws";
    }
  }
  return `${protocol}//${host}/ws`;
};

export function useWebSocket(overrideUrl?: string) {
  const [connectionStatus, setConnectionStatus] = useState<"connecting" | "connected" | "disconnected">("disconnected");
  const [metrics, setMetrics] = useState<TelemetryMetrics | null>(null);
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [eventLog, setEventLog] = useState<MarketEventData[]>([]);
  
  // Dynamic State variables for multi-symbol ORB Dashboard
  const [symbolsStatus, setSymbolsStatus] = useState<Record<string, SymbolStatusDetail>>({});
  const [tradeHistory, setTradeHistory] = useState<TradeHistoryRecord[]>([]);
  const [indices, setIndices] = useState<Record<string, { ltp: number; change_pct: number; trend: string; open: number }>>({
    "NIFTY_50": { ltp: 0, change_pct: 0, trend: "NEUTRAL", open: 0 },
    "BANK_NIFTY": { ltp: 0, change_pct: 0, trend: "NEUTRAL", open: 0 }
  });
  const [strategyReport, setStrategyReport] = useState<StrategyReport | null>(null);
  const [strategyConfig, setStrategyConfig] = useState<StrategyConfig | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const eventLogRef = useRef<MarketEventData[]>([]);

  useEffect(() => {
    let reconnectTimeout: number;

    const connect = () => {
      setConnectionStatus("connecting");
      const socket = new WebSocket(getWsUrl(overrideUrl));
      wsRef.current = socket;

      socket.onopen = () => {
        setConnectionStatus("connected");
      };

      socket.onmessage = (event) => {
        try {
          const msg: WSMessage = JSON.parse(event.data);
          
          if (msg.type === "telemetry_pulse") {
            setMetrics(msg.metrics);
            setStatus(msg.status);
            
            if (msg.symbols_status) {
              setSymbolsStatus(msg.symbols_status);
            }
            if (msg.trade_history) {
              // Offset live Trade IDs by 10000 to keep unique keys
              const mapped = msg.trade_history.map(t => ({
                ...t,
                Trade_ID: typeof t.Trade_ID === "number" ? t.Trade_ID + 10000 : t.Trade_ID
              }));
              setTradeHistory(mapped);
            }
            if (msg.indices) {
              setIndices(msg.indices);
            }
            if (msg.strategy_report) {
              setStrategyReport(msg.strategy_report);
            }
            if (msg.configuration) {
              setStrategyConfig(msg.configuration);
            }
            
            if (msg.latest_event) {
              const currentLog = eventLogRef.current;
              if (currentLog.length === 0 || currentLog[0].event_id !== msg.latest_event.event_id) {
                const updated = [msg.latest_event, ...currentLog].slice(0, 15);
                eventLogRef.current = updated;
                setEventLog(updated);
              }
            }
          }
        } catch (e) {
          console.error("Error parsing websocket message", e);
        }
      };

      socket.onclose = () => {
        setConnectionStatus("disconnected");
        reconnectTimeout = window.setTimeout(connect, 3000);
      };

      socket.onerror = () => {
        socket.close();
      };
    };

    connect();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
      clearTimeout(reconnectTimeout);
    };
  }, [overrideUrl]);

  const sendAction = useCallback((action: string, value?: any) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action, value }));
    }
  }, []);

  const startReplay = useCallback(() => sendAction("start"), [sendAction]);
  const pauseReplay = useCallback(() => sendAction("pause"), [sendAction]);
  const stopReplay = useCallback(() => sendAction("stop"), [sendAction]);
  const stepReplay = useCallback(() => sendAction("step"), [sendAction]);
  const setReplaySpeed = useCallback((speed: number) => sendAction("speed", speed), [sendAction]);

  const startLiveStrategy = useCallback((
    symbols: string[],
    capital: number,
    leverage: number,
    priorityRanking: string[],
    allocationStrategy: string,
    allocationWeights: number[]
  ) => {
    sendAction("start_live_strategy", {
      symbols,
      capital,
      leverage,
      priority_ranking: priorityRanking,
      allocation_strategy: allocationStrategy,
      allocation_weights: allocationWeights
    });
  }, [sendAction]);

  const stopLiveStrategy = useCallback(() => {
    sendAction("stop_live_strategy");
  }, [sendAction]);

  const updateStrategyConfig = useCallback((config: {
    priority_ranking?: string[];
    allocation_strategy?: string;
    allocation_weights?: number[];
    capital?: number;
    leverage?: number;
  }) => {
    sendAction("update_strategy_config", config);
  }, [sendAction]);

  return {
    connectionStatus,
    metrics,
    status,
    eventLog,
    symbolsStatus,
    tradeHistory,
    indices,
    strategyReport,
    strategyConfig,
    startReplay,
    pauseReplay,
    stopReplay,
    stepReplay,
    setReplaySpeed,
    startLiveStrategy,
    stopLiveStrategy,
    updateStrategyConfig
  };
}

