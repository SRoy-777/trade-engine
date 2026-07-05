from typing import Callable, Awaitable, List, Tuple, Dict, Any, Optional
from providers.market.dhan.auth import DhanAuthenticator
from providers.market.dhan.websocket_client import DhanWebSocketClient
from providers.market.dhan.packet_parser import DhanPacketParser
from providers.market.dhan.models import MarketPacket
from providers.market.dhan.logger import dhan_logger

class DhanMarketProvider:
    """High-level Dhan Live Feed Market Data Provider. Serves parsed, strongly-typed packets via callbacks."""

    def __init__(self):
        self._authenticator = DhanAuthenticator()
        self._parser = DhanPacketParser()
        self._client = DhanWebSocketClient(self._authenticator, self._handle_raw_data)
        self._market_packet_callback: Optional[Callable[[MarketPacket], Awaitable[None]]] = None

    def set_packet_callback(self, callback: Callable[[MarketPacket], Awaitable[None]]) -> None:
        """Registers a callback method to receive parsed strongly-typed MarketPacket feeds."""
        self._market_packet_callback = callback
        dhan_logger.info("Market packet callback registered successfully")

    async def start(self) -> None:
        """Starts the underlying WebSocket client loop."""
        dhan_logger.info("Starting Dhan Market Provider feed client")
        await self._client.start()

    async def stop(self) -> None:
        """Stops the underlying WebSocket client loop."""
        dhan_logger.info("Stopping Dhan Market Provider feed client")
        await self._client.stop()

    async def subscribe(self, request_code: int, instruments: List[Tuple[int, str]]) -> None:
        """Subscribes to a list of instruments (exchange_segment_code, security_id) for a specific request mode."""
        dhan_logger.info(f"Dhan Market Provider subscribing to {len(instruments)} instruments (Code={request_code})")
        await self._client.subscribe(request_code, instruments)

    async def _handle_raw_data(self, data: bytes) -> None:
        """Internal callback handler for raw binary bytes."""
        try:
            # 1. Decode raw bytes to MarketPacket
            packet = self._parser.parse(data)

            # 2. Forward to registered callback
            if self._market_packet_callback:
                await self._market_packet_callback(packet)
                
        except Exception as e:
            dhan_logger.error(f"Error handling raw WebSocket data frame: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Exposes operational status of the client connection."""
        client_status = self._client.get_connection_status()
        return {
            "provider_name": "dhan",
            "connected": client_status.connected,
            "last_connected_at": client_status.last_connected_at.isoformat() + "Z" if client_status.last_connected_at else None,
            "reconnect_attempts": client_status.reconnect_attempts,
            "status": client_status.current_mode
        }
