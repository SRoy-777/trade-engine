import asyncio
import json
from typing import List, Dict, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect
from models.market import MarketEvent
from services.metrics_service import metrics_service
from market_feed.manager import feed_manager
from event_bus.event_bus import event_bus
from utils.logger_setup import logger

class WebSocketBroadcaster:
    def __init__(self):
        self._active_connections: List[WebSocket] = []
        self._latest_event: Optional[MarketEvent] = None
        self._lock = asyncio.Lock()
        self._broadcast_task: Optional[asyncio.Task] = None
        self._is_active = False

    async def register_subscriber(self) -> None:
        """Registers this broadcaster as a visual subscriber to the Event Bus (Priority 10)."""
        await event_bus.subscribe(self.on_market_event, priority=10)

    async def on_market_event(self, event: MarketEvent) -> None:
        """Saves the latest event for consolidation in the next broadcast pulse."""
        async with self._lock:
            self._latest_event = event

    async def connect(self, websocket: WebSocket) -> None:
        """Accepts and tracks a new client websocket connection."""
        await websocket.accept()
        async with self._lock:
            self._active_connections.append(websocket)
        logger.info(f"Client connected to WebSocket. Total clients: {len(self._active_connections)}")
        
        # Send initial status packet immediately
        try:
            initial_msg = self._build_update_message()
            await websocket.send_text(json.dumps(initial_msg))
        except Exception as e:
            logger.error(f"Error sending initial websocket status: {e}")

    async def disconnect(self, websocket: WebSocket) -> None:
        """Removes a disconnected client."""
        async with self._lock:
            if websocket in self._active_connections:
                self._active_connections.remove(websocket)
        logger.info(f"Client disconnected from WebSocket. Total clients: {len(self._active_connections)}")

    def start(self) -> None:
        """Starts the 100ms broadcast telemetry loop."""
        self._is_active = True
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        logger.info("Started WebSocket Broadcaster 10Hz telemetry thread")

    async def stop(self) -> None:
        """Stops the broadcast loop and closes all client sockets."""
        self._is_active = False
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None

        async with self._lock:
            for ws in list(self._active_connections):
                try:
                    await ws.close(code=1000, reason="Server shutting down")
                except Exception:
                    pass
            self._active_connections.clear()
        logger.info("Stopped WebSocket Broadcaster")

    def _build_update_message(self) -> Dict[str, Any]:
        """Assembles metrics, status, and ticks into a single JSON payload."""
        # Direct fallback for live strategy telemetry streams
        from core.live_runner import live_runner
        if live_runner.active:
            return live_runner.compile_telemetry_message()

        metrics = metrics_service.get_metrics()
        status = feed_manager.get_status()
        
        latest_event_data = None
        if self._latest_event:
            latest_event_data = {
                "event_id": self._latest_event.event_id,
                "correlation_id": self._latest_event.correlation_id,
                "symbol": self._latest_event.symbol,
                "ltp": self._latest_event.ltp,
                "open": self._latest_event.open,
                "high": self._latest_event.high,
                "low": self._latest_event.low,
                "close": self._latest_event.close,
                "volume": self._latest_event.volume,
                "exchange_timestamp": self._latest_event.exchange_timestamp.isoformat() + "Z" if self._latest_event.exchange_timestamp else None,
                "received_timestamp": self._latest_event.received_timestamp.isoformat() + "Z",
                "processed_timestamp": self._latest_event.processed_timestamp.isoformat() + "Z",
            }

        return {
            "type": "telemetry_pulse",
            "metrics": metrics,
            "status": status,
            "latest_event": latest_event_data,
            "indices": live_runner.indices
        }

    async def _broadcast_loop(self) -> None:
        """Consolidates metrics and pushes updates to all clients at 10Hz."""
        while self._is_active:
            try:
                await asyncio.sleep(0.1)  # 100ms interval
                
                async with self._lock:
                    if not self._active_connections:
                        continue
                        
                update_msg = self._build_update_message()
                msg_str = json.dumps(update_msg)

                # Broadcast to all clients
                async with self._lock:
                    closed_websockets = []
                    for ws in self._active_connections:
                        try:
                            await ws.send_text(msg_str)
                        except WebSocketDisconnect:
                            closed_websockets.append(ws)
                        except Exception as e:
                            logger.debug(f"Failed to send to websocket, client likely closed: {e}")
                            closed_websockets.append(ws)
                            
                    # Clean up broken connections
                    for ws in closed_websockets:
                        if ws in self._active_connections:
                            self._active_connections.remove(ws)
            except asyncio.CancelledError:
                break
            except Exception as loop_err:
                logger.error(f"Error in WebSocket broadcast loop: {loop_err}")

    async def send_to_all(self, message: Dict[str, Any]) -> None:
        """Sends a JSON-serializable message directly to all active WebSocket clients."""
        msg_str = json.dumps(message)
        async with self._lock:
            closed_websockets = []
            for ws in self._active_connections:
                try:
                    await ws.send_text(msg_str)
                except Exception:
                    closed_websockets.append(ws)
            for ws in closed_websockets:
                if ws in self._active_connections:
                    self._active_connections.remove(ws)

websocket_broadcaster = WebSocketBroadcaster()
