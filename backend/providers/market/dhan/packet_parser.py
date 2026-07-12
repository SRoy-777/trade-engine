import struct
from datetime import datetime, timezone
from typing import Dict, Any
from providers.market.dhan.exceptions import DhanParserException
from providers.market.dhan.models import MarketPacket

# Exchange code string map
EXCHANGE_SEGMENTS = {
    0: "IDX_I",
    1: "NSE_EQ",
    2: "NSE_FNO",
    3: "NSE_CURRENCY",
    4: "BSE_EQ",
    5: "MCX_COMM",
    7: "BSE_CURRENCY",
    8: "BSE_FNO"
}

class DhanPacketParser:
    """Decodes raw binary packet feeds from DhanHQ websocket into MarketPacket schemas."""

    def parse(self, data: bytes) -> MarketPacket:
        if not data:
            raise DhanParserException("Empty binary payload received")

        try:
            # Unpack first byte to determine feed packet response code
            first_byte = struct.unpack('<B', data[0:1])[0]
        except Exception as e:
            raise DhanParserException(f"Failed to decode response header code: {e}")

        # Parse corresponding packet formats
        if first_byte == 2:  # Ticker Data
            return self._parse_ticker(data)
        elif first_byte == 4:  # Quote Data
            return self._parse_quote(data)
        elif first_byte == 3:  # Market Depth Data
            return self._parse_depth(data)
        elif first_byte == 8:  # Full Data
            return self._parse_full(data)
        elif first_byte == 5:  # OI Data
            return self._parse_oi(data)
        elif first_byte == 6:  # Previous Close Data
            return self._parse_prev_close(data)
        elif first_byte == 50:  # Server Disconnect Error Codes
            self._handle_server_disconnect(data)
            raise DhanParserException("Server disconnection warning received")
        else:
            raise DhanParserException(f"Unsupported feed response code: {first_byte}")

    def _parse_ticker(self, data: bytes) -> MarketPacket:
        expected_size = 16
        if len(data) < expected_size:
            raise DhanParserException(f"Ticker data buffer size too small ({len(data)} < {expected_size})")

        try:
            # Format: <B (Code) H (Len) B (Exch) I (SecID) f (LTP) I (LTT)
            unpacked = struct.unpack('<BHBIfI', data[:expected_size])
            exchange_code = unpacked[2]
            sec_id = str(unpacked[3])
            ltp = float(unpacked[4])
            ltt_epoch = unpacked[5]
            
            return MarketPacket(
                packet_type="Ticker",
                exchange_segment=EXCHANGE_SEGMENTS.get(exchange_code, f"UNKNOWN_{exchange_code}"),
                security_id=sec_id,
                ltp=ltp,
                timestamp=datetime.fromtimestamp(ltt_epoch) if ltt_epoch > 0 else datetime.now(timezone.utc).replace(tzinfo=None),
                raw_fields={"ltt_epoch": ltt_epoch}
            )
        except Exception as e:
            raise DhanParserException(f"Failed to unpack Ticker data: {e}")

    def _parse_quote(self, data: bytes) -> MarketPacket:
        expected_size = 50
        if len(data) < expected_size:
            raise DhanParserException(f"Quote data buffer size too small ({len(data)} < {expected_size})")

        try:
            # Format: < B (Code) H (Len) B (Exch) I (SecID) f (LTP) H (LTQ) I (LTT) f (AvgPrice) I (Vol) I (SellQ) I (BuyQ) f (O) f (C) f (H) f (L)
            unpacked = struct.unpack('<BHBIfHIfIIIffff', data[:expected_size])
            exchange_code = unpacked[2]
            sec_id = str(unpacked[3])
            ltp = float(unpacked[4])
            ltq = unpacked[5]
            ltt_epoch = unpacked[6]
            avg_price = float(unpacked[7])
            volume = int(unpacked[8])
            sell_q = int(unpacked[9])
            buy_q = int(unpacked[10])
            open_p = float(unpacked[11])
            close_p = float(unpacked[12])
            high_p = float(unpacked[13])
            low_p = float(unpacked[14])

            return MarketPacket(
                packet_type="Quote",
                exchange_segment=EXCHANGE_SEGMENTS.get(exchange_code, f"UNKNOWN_{exchange_code}"),
                security_id=sec_id,
                ltp=ltp,
                volume=volume,
                timestamp=datetime.fromtimestamp(ltt_epoch) if ltt_epoch > 0 else datetime.now(timezone.utc).replace(tzinfo=None),
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                raw_fields={
                    "ltq": ltq,
                    "avg_price": avg_price,
                    "sell_quantity": sell_q,
                    "buy_quantity": buy_q
                }
            )
        except Exception as e:
            raise DhanParserException(f"Failed to unpack Quote data: {e}")

    def _parse_depth(self, data: bytes) -> MarketPacket:
        expected_size = 112
        if len(data) < expected_size:
            raise DhanParserException(f"Depth data buffer size too small ({len(data)} < {expected_size})")

        try:
            # Format: < B (Code) H (Len) B (Exch) I (SecID) f (LTP) 100s (Depth)
            unpacked = struct.unpack('<BHBIf100s', data[:expected_size])
            exchange_code = unpacked[2]
            sec_id = str(unpacked[3])
            ltp = float(unpacked[4])
            depth_bytes = unpacked[5]

            # Unpack 5 levels of depth (size of one depth item is 16 bytes: <IIHHff)
            # Bid Qty, Ask Qty, Bid Orders, Ask Orders, Bid Price, Ask Price
            depth_list = []
            packet_format = '<IIHHff'
            packet_size = struct.calcsize(packet_format)
            for i in range(5):
                start = i * packet_size
                end = start + packet_size
                d_unpacked = struct.unpack(packet_format, depth_bytes[start:end])
                depth_list.append({
                    "bid_qty": d_unpacked[0],
                    "ask_qty": d_unpacked[1],
                    "bid_orders": d_unpacked[2],
                    "ask_orders": d_unpacked[3],
                    "bid_price": round(d_unpacked[4], 2),
                    "ask_price": round(d_unpacked[5], 2)
                })

            return MarketPacket(
                packet_type="Depth",
                exchange_segment=EXCHANGE_SEGMENTS.get(exchange_code, f"UNKNOWN_{exchange_code}"),
                security_id=sec_id,
                ltp=ltp,
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                raw_fields={"depth_levels": depth_list}
            )
        except Exception as e:
            raise DhanParserException(f"Failed to unpack Depth data: {e}")

    def _parse_full(self, data: bytes) -> MarketPacket:
        expected_size = 162
        if len(data) < expected_size:
            raise DhanParserException(f"Full data buffer size too small ({len(data)} < {expected_size})")

        try:
            # Format: <BHBIfHIfIIIIIIffff100s
            unpacked = struct.unpack('<BHBIfHIfIIIIIIffff100s', data[:expected_size])
            exchange_code = unpacked[2]
            sec_id = str(unpacked[3])
            ltp = float(unpacked[4])
            ltt_epoch = unpacked[6]
            volume = int(unpacked[8])
            oi = int(unpacked[11])
            open_p = float(unpacked[14])
            close_p = float(unpacked[15])
            high_p = float(unpacked[16])
            low_p = float(unpacked[17])

            return MarketPacket(
                packet_type="Full",
                exchange_segment=EXCHANGE_SEGMENTS.get(exchange_code, f"UNKNOWN_{exchange_code}"),
                security_id=sec_id,
                ltp=ltp,
                volume=volume,
                timestamp=datetime.fromtimestamp(ltt_epoch) if ltt_epoch > 0 else datetime.now(timezone.utc).replace(tzinfo=None),
                open=open_p,
                high=high_p,
                low=low_p,
                close=close_p,
                raw_fields={"oi": oi}
            )
        except Exception as e:
            raise DhanParserException(f"Failed to unpack Full data: {e}")

    def _parse_oi(self, data: bytes) -> MarketPacket:
        expected_size = 12
        if len(data) < expected_size:
            raise DhanParserException(f"OI data buffer size too small ({len(data)} < {expected_size})")
        try:
            # Format: <BHBII
            unpacked = struct.unpack('<BHBII', data[:expected_size])
            exchange_code = unpacked[2]
            sec_id = str(unpacked[3])
            oi = int(unpacked[4])
            return MarketPacket(
                packet_type="OI",
                exchange_segment=EXCHANGE_SEGMENTS.get(exchange_code, f"UNKNOWN_{exchange_code}"),
                security_id=sec_id,
                ltp=0.0,  # No LTP in OI packet
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                raw_fields={"oi": oi}
            )
        except Exception as e:
            raise DhanParserException(f"Failed to unpack OI data: {e}")

    def _parse_prev_close(self, data: bytes) -> MarketPacket:
        expected_size = 16
        if len(data) < expected_size:
            raise DhanParserException(f"PrevClose data buffer size too small ({len(data)} < {expected_size})")
        try:
            # Format: <BHBIfI
            unpacked = struct.unpack('<BHBIfI', data[:expected_size])
            exchange_code = unpacked[2]
            sec_id = str(unpacked[3])
            prev_close = float(unpacked[4])
            prev_oi = int(unpacked[5])
            return MarketPacket(
                packet_type="PrevClose",
                exchange_segment=EXCHANGE_SEGMENTS.get(exchange_code, f"UNKNOWN_{exchange_code}"),
                security_id=sec_id,
                ltp=0.0,
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                close=prev_close,
                raw_fields={"prev_oi": prev_oi}
            )
        except Exception as e:
            raise DhanParserException(f"Failed to unpack PrevClose data: {e}")

    def _handle_server_disconnect(self, data: bytes) -> None:
        try:
            # Format: <BHBIH (code, length, exchange, secid, reason_code)
            unpacked = struct.unpack('<BHBIH', data[:10])
            reason_code = unpacked[4]
            # Mapping reason codes
            reasons = {
                805: "No. of active websocket connections exceeded",
                806: "Subscribe to Data APIs to continue",
                807: "Access Token expired or invalid"
            }
            reason = reasons.get(reason_code, f"Unknown code: {reason_code}")
            raise DhanParserException(f"Server Disconnection Code {reason_code}: {reason}")
        except Exception as e:
            if isinstance(e, DhanParserException):
                raise e
            raise DhanParserException(f"Failed to parse server disconnection payload: {e}")
