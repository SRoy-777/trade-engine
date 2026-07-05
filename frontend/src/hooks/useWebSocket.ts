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

export interface WSMessage {
  type: string;
  metrics: TelemetryMetrics;
  status: ProviderStatus;
  latest_event: MarketEventData | null;
}

const getWsUrl = (overrideUrl?: string): string => {
  if (overrideUrl) return overrideUrl;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.host;
  
  // If debugging locally on Vite port 5173, point to backend on port 8000
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
  const wsRef = useRef<WebSocket | null>(null);

  // Keep a ref of the log list to avoid closure problems in callbacks
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
            
            if (msg.latest_event) {
              const currentLog = eventLogRef.current;
              // Avoid duplicates if the event_id is already the last item
              if (currentLog.length === 0 || currentLog[0].event_id !== msg.latest_event.event_id) {
                // Prepend and limit size to 15 entries
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
        // Attempt reconnection after 3 seconds
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

  // Send action utilities
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

  const startLiveStrategy = useCallback((symbol: string, capital: number, targetProfit: number, ticksTarget: number) => {
    sendAction("start_live_strategy", { symbol, capital, target_profit: targetProfit, ticks_target: ticksTarget });
  }, [sendAction]);

  const stopLiveStrategy = useCallback(() => {
    sendAction("stop_live_strategy");
  }, [sendAction]);

  return {
    connectionStatus,
    metrics,
    status,
    eventLog,
    startReplay,
    pauseReplay,
    stopReplay,
    stepReplay,
    setReplaySpeed,
    startLiveStrategy,
    stopLiveStrategy,
  };
}
