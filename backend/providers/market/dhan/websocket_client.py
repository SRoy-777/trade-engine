import asyncio
import json
import time
from datetime import datetime
from typing import Callable, Awaitable, List, Dict, Any, Optional, Set, Tuple
import websockets
from providers.market.dhan.config import dhan_settings
from providers.market.dhan.auth import DhanAuthenticator
from providers.market.dhan.logger import dhan_logger
from providers.market.dhan.models import ConnectionStatus, RawPacket
from providers.market.dhan.exceptions import DhanNetworkException, DhanSubscriptionException

class DhanWebSocketClient:
    """Low-level WebSocket client for connecting to Dhan Live Feed with retry limits and heartbeats."""

    def __init__(self, authenticator: DhanAuthenticator, on_raw_packet: Callable[[bytes], Awaitable[None]]):
        self._authenticator = authenticator
        self._ws_url = dhan_settings.WS_URL
        self._on_raw_packet_callback = on_raw_packet
        
        self._ws: Optional[Any] = None
        self._is_active = False
        self._connection_status = ConnectionStatus(connected=False)
        self._client_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        # Keep track of subscriptions to re-apply on reconnect
        # Key: request_code (e.g. 15), Value: Set of (exchange_segment, security_id) tuples
        self._subscribed_instruments: Dict[int, Set[Tuple[int, str]]] = {
            15: set(), # Ticker
            17: set(), # Quote
            21: set()  # Full
        }
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Starts the WebSocket connection worker task."""
        if self._is_active:
            return
        
        self._is_active = True
        self._client_task = asyncio.create_task(self._connect_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        dhan_logger.info("Dhan WebSocket client background threads started")

    async def stop(self) -> None:
        """Gracefully disconnects and shuts down connection worker tasks."""
        if not self._is_active:
            return
        
        dhan_logger.info("Stopping Dhan WebSocket client")
        self._is_active = False
        
        # Stop background loops
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # Send unsubscribe/disconnect packet if socket is active
        if self._ws and self._connection_status.connected:
            try:
                # RequestCode 12 is Dhan client disconnection code
                dhan_logger.info("Sending disconnect request frame to server")
                await self._ws.send(json.dumps({"RequestCode": 12}))
                await asyncio.sleep(0.1) # brief pause to let it exit
            except Exception as e:
                dhan_logger.debug(f"Failed to send disconnect frame: {e}")
            
            try:
                await self._ws.close()
            except Exception as e:
                dhan_logger.debug(f"Failed to close websocket socket: {e}")
                
        self._ws = None
        self._connection_status.connected = False
        self._connection_status.current_mode = "DISCONNECTED"

        if self._client_task:
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass
            self._client_task = None
            
        dhan_logger.info("Disconnected")

    async def subscribe(self, request_code: int, instruments: List[Tuple[int, str]]) -> None:
        """Subscribes to a list of instruments (exchange_segment_code, security_id)."""
        if request_code not in self._subscribed_instruments:
            raise DhanSubscriptionException(f"Unsupported subscription request code: {request_code}")

        # Update cache
        async with self._lock:
            for exchange, sec_id in instruments:
                self._subscribed_instruments[request_code].add((exchange, sec_id))

        # Send subscription request immediately if connection is live
        if self._ws and self._connection_status.connected:
            await self._send_subscription(request_code, instruments)

    async def _send_subscription(self, request_code: int, instruments: List[Tuple[int, str]]) -> None:
        """Sends the JSON subscription frame to Dhan WebSocket in batches of 100."""
        if not self._ws:
            return

        # Mapping exchange segment numeric codes to string tags required by Dhan V2
        exch_map = {
            0: "IDX_I",
            1: "NSE_EQ",
            2: "NSE_FNO",
            3: "NSE_CURRENCY",
            4: "BSE_EQ",
            5: "MCX_COMM",
            7: "BSE_CURRENCY",
            8: "BSE_FNO"
        }

        # Dhan limits JSON messages to 100 instruments per frame
        batch_size = 100
        for i in range(0, len(instruments), batch_size):
            batch = instruments[i:i + batch_size]
            payload = {
                "RequestCode": request_code,
                "InstrumentCount": len(batch),
                "InstrumentList": [
                    {
                        "ExchangeSegment": exch_map.get(ex, str(ex)),
                        "SecurityId": str(sec_id)
                    } for ex, sec_id in batch
                ]
            }
            try:
                dhan_logger.info(f"Subscribing to {len(batch)} instruments (Code={request_code})")
                await self._ws.send(json.dumps(payload))
                dhan_logger.info("Subscription Success")
            except Exception as e:
                dhan_logger.error(f"Subscription Failure: {e}")
                raise DhanSubscriptionException(f"Failed to transmit subscription frame: {e}")

    async def _resubscribe_all(self) -> None:
        """Re-subscribes to all registered instruments upon reconnecting."""
        dhan_logger.info("Re-applying cached instrument subscriptions on reconnect")
        async with self._lock:
            for request_code, instruments_set in self._subscribed_instruments.items():
                if instruments_set:
                    await self._send_subscription(request_code, list(instruments_set))

    async def _connect_loop(self) -> None:
        """Websocket lifecycle manager with exponential backoff reconnects."""
        backoff = 1.0
        
        while self._is_active:
            try:
                # Retrieve verified auth tokens
                auth_data = self._authenticator.authenticate_session()
                token = auth_data["access_token"]
                client_id = auth_data["client_id"]
                
                # Format URL per DhanHQ V2 Websocket specification
                url = f"{self._ws_url}?version=2&token={token}&clientId={client_id}&authType=2"
                
                dhan_logger.info("Connecting...")
                # Connect to WebSocket using ping parameters
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=10 * 1024 * 1024 # 10MB frame limits
                ) as ws:
                    self._ws = ws
                    self._connection_status.connected = True
                    self._connection_status.last_connected_at = datetime.utcnow()
                    self._connection_status.current_mode = "CONNECTED"
                    
                    dhan_logger.info("Authentication Successful")
                    dhan_logger.info("Connected")
                    
                    # Reset backoff counters
                    backoff = 1.0
                    self._connection_status.reconnect_attempts = 0
                    
                    # Re-subscribe to cached tickers
                    await self._resubscribe_all()
                    
                    # Recv frame loop
                    while self._is_active:
                        data = await ws.recv()
                        if isinstance(data, bytes):
                            # Yield raw frame
                            await self._on_raw_packet_callback(data)
                        else:
                            dhan_logger.warning(f"Received unexpected text frame from server: {data}")
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._ws = None
                self._connection_status.connected = False
                self._connection_status.current_mode = "DISCONNECTED"
                
                if not self._is_active:
                    break
                    
                self._connection_status.reconnect_attempts += 1
                dhan_logger.error(f"Disconnected. Network failure: {e}")
                
                # Reconnect backoff sleep
                dhan_logger.info(f"Reconnect Attempt in {backoff:.1f}s (Attempt {self._connection_status.reconnect_attempts})")
                await asyncio.sleep(backoff)
                
                # Exponential backoff capped at 60s
                backoff = min(backoff * 2, 60.0)

    async def _heartbeat_loop(self) -> None:
        """Periodic heartbeat logger simulating connection health audit checks."""
        while self._is_active:
            try:
                await asyncio.sleep(30.0)
                if self._ws and self._ws.state == websockets.protocol.State.OPEN:
                    dhan_logger.info("Heartbeat - Connection active")
                    # Send a native ping to check network latency
                    start_time = time.perf_counter()
                    pong = await self._ws.ping()
                    await asyncio.wait_for(pong, timeout=5.0)
                    rtt = (time.perf_counter() - start_time) * 1000
                    dhan_logger.debug(f"Heartbeat RTT response: {rtt:.2f}ms")
                else:
                    dhan_logger.warning("Heartbeat check - Client disconnected")
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                dhan_logger.error("Heartbeat timeout - Server failed to respond to ping within 5 seconds")
                # Sever connection to trigger reconnect loop
                if self._ws:
                    await self._ws.close(code=1011, reason="Heartbeat timeout")
            except Exception as e:
                dhan_logger.debug(f"Heartbeat connection check warning: {e}")

    def get_connection_status(self) -> ConnectionStatus:
        return self._connection_status
