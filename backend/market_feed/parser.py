import json
import uuid
import time
from datetime import datetime
from typing import Optional, Dict, Any
from models.market import RawPacket, MarketEvent
from utils.logger_setup import logger

class PacketParser:
    def __init__(self):
        self._total_parse_time_ms = 0.0
        self._parse_count = 0

    def parse(self, packet: RawPacket) -> MarketEvent:
        """Parses a RawPacket into a standardized MarketEvent."""
        start_time = time.perf_counter()
        self._parse_count += 1
        
        try:
            # Parse payload (expected to be JSON)
            data: Dict[str, Any] = json.loads(packet.raw_payload)
            
            # Map timestamps
            exchange_ts_raw = data.get("exchange_timestamp") or data.get("timestamp")
            exchange_ts: Optional[datetime] = None
            if exchange_ts_raw:
                try:
                    # Support ISO format parsing
                    if isinstance(exchange_ts_raw, str):
                        exchange_ts = datetime.fromisoformat(exchange_ts_raw.replace("Z", "+00:00"))
                    elif isinstance(exchange_ts_raw, (int, float)):
                        exchange_ts = datetime.fromtimestamp(exchange_ts_raw)
                except Exception as ts_err:
                    logger.debug(f"Failed to parse exchange timestamp '{exchange_ts_raw}': {ts_err}")

            event = MarketEvent(
                event_id=str(uuid.uuid4()),
                correlation_id=packet.packet_id,
                exchange_timestamp=exchange_ts,
                received_timestamp=packet.received_timestamp,
                processed_timestamp=datetime.utcnow(),
                symbol=data.get("symbol", "UNKNOWN"),
                ltp=float(data.get("ltp", 0.0)),
                open=float(data.get("open", 0.0)),
                high=float(data.get("high", 0.0)),
                low=float(data.get("low", 0.0)),
                close=float(data.get("close", 0.0)),
                volume=int(data.get("volume", 0)),
                source_provider=packet.provider
            )
            
            parse_time_ms = (time.perf_counter() - start_time) * 1000
            self._total_parse_time_ms += parse_time_ms
            return event

        except Exception as e:
            logger.error(
                f"Failed to parse packet payload into MarketEvent: {e}",
                extra={"correlation_id": packet.packet_id, "provider": packet.provider}
            )
            raise ValueError(f"Packet parser error: {e}") from e

    @property
    def average_parse_time_ms(self) -> float:
        if self._parse_count == 0:
            return 0.0
        return self._total_parse_time_ms / self._parse_count

packet_parser = PacketParser()
