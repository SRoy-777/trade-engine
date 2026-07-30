import os
import uuid
import json
import math
import asyncio
import urllib.request
from typing import Dict, Any, Callable, Awaitable, Optional
from core.broker.base import BaseBroker
from providers.market.dhan.models import MarketPacket
from providers.market.dhan.logger import dhan_logger

class DhanLiveBroker(BaseBroker):
    """
    Live brokerage integration connecting directly to DhanHQ V2 REST APIs.
    Communicates with exchange for real-world order routing and account status.
    """

    def __init__(self, symbol_mappings: Dict[str, str]):
        self.symbol_mappings = symbol_mappings
        self.client_id = os.getenv("CLIENT_ID", "").strip()
        self.access_token = os.getenv("ACCESS_TOKEN", "").strip()
        self.api_url = "https://api.dhan.co"

        # Optional static-IP proxy for Dhan API calls.
        # Set DHAN_PROXY_URL in HF Space secrets to route all Dhan REST calls through
        # a fixed IP (e.g. Webshare.io proxy) so the IP can be whitelisted in Dhan portal.
        # Format: http://username:password@proxy_host:port
        # If not set, direct connection is used (requires HF Space IP to be whitelisted).
        self._proxy_url = os.getenv("DHAN_PROXY_URL", "").strip() or None
        if self._proxy_url:
            dhan_logger.info("[Dhan Live Broker] Static-IP proxy configured — all Dhan API calls will route through proxy")
        else:
            dhan_logger.warning("[Dhan Live Broker] No DHAN_PROXY_URL set — using direct connection (HF IP must be whitelisted in Dhan)")

        # Local state mirroring for StrategyManager compatibility
        self._cash = 0.0
        self._positions: Dict[str, Dict[str, float]] = {}
        self._fill_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self._order_history: Dict[str, Dict[str, Any]] = {}
        self._last_balance_check = 0.0

        dhan_logger.info(f"[Dhan Live Broker] Initialised. Client ID: {self.client_id} | Mappings: {len(symbol_mappings)}")

    def _send_request(self, method: str, endpoint: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        """Helper to send synchronous HTTP requests to Dhan REST API.
        Routes through DHAN_PROXY_URL if set (recommended for HF Spaces — static IP whitelisting)."""
        url = f"{self.api_url}{endpoint}"
        headers = {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json"
        }

        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        # Build opener: use proxy if configured, else direct
        if self._proxy_url:
            proxy_handler = urllib.request.ProxyHandler({"http": self._proxy_url, "https": self._proxy_url})
            opener = urllib.request.build_opener(proxy_handler)
        else:
            opener = urllib.request.build_opener()

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with opener.open(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            dhan_logger.error(f"[Dhan Live Broker] API Request FAILED to {endpoint}: {e}")
            return {"error": str(e)}

    async def submit_order(self, order_request: Dict[str, Any]) -> str:
        """Routes the buy/sell order to Dhan API and starts status polling in the background."""
        symbol = order_request["symbol"]
        side = order_request["side"].upper()
        qty = int(order_request["quantity"])
        order_type = order_request.get("order_type", "MARKET").upper()
        price = float(order_request.get("price", 0.0))
        strategy_id = order_request.get("strategy_id", "orb")

        # Resolve symbol to security token ID
        sec_id = self.symbol_mappings.get(symbol)
        if not sec_id:
            raise ValueError(f"Dhan Live Broker Error: Could not resolve security ID token for symbol {symbol}")

        correlation_id = f"live_ord_{uuid.uuid4().hex[:12]}"
        
        payload = {
            "dhanClientId": self.client_id,
            "correlationId": correlation_id,
            "transactionType": "BUY" if side == "BUY" else "SELL",
            "exchangeSegment": "NSE_EQ",
            "productType": "INTRADAY",
            "orderType": order_type,
            "validity": "DAY",
            "securityId": sec_id,
            "quantity": qty,
            "price": price if order_type == "LIMIT" else 0.0
        }

        dhan_logger.info(f"[Dhan Live Broker] Routing {side} order for {qty} {symbol} (Token: {sec_id}) to exchange...")
        
        # Place order via REST API
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: self._send_request("POST", "/v2/orders", payload)
        )

        if "error" in response or "orderId" not in response:
            err_msg = response.get("error", response.get("remarks", "Unknown API error"))
            dhan_logger.error(f"[Dhan Live Broker] Order placement rejected by Dhan API: {err_msg}")
            raise ValueError(f"Dhan API rejection: {err_msg}")

        order_id = str(response["orderId"])
        dhan_logger.info(f"[Dhan Live Broker] Order accepted by Dhan. ID: {order_id}. Polling fill state...")

        # Store order locally
        self._order_history[order_id] = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "strategy_id": strategy_id,
            "status": "PENDING"
        }

        # Start status tracking loop in the background
        asyncio.create_task(self._poll_order_status(order_id))

        return order_id

    async def _poll_order_status(self, order_id: str):
        """Polls Dhan REST API for order execution status and triggers callback upon execution."""
        loop = asyncio.get_event_loop()
        attempts = 0
        max_attempts = 15 # 15 seconds max polling limit for market orders

        while attempts < max_attempts:
            await asyncio.sleep(1.0)
            attempts += 1

            response = await loop.run_in_executor(
                None, lambda: self._send_request("GET", f"/v2/orders/{order_id}")
            )

            if "error" in response:
                continue

            status = response.get("orderStatus")
            if status == "TRADED":  # Fully Filled
                fill_price = float(response.get("avgFilledPrice", 0.0))
                fill_qty = int(response.get("filledQty", 0))
                
                dhan_logger.info(f"[Dhan Live Broker] Order {order_id} FILLED fully on exchange at ₹{fill_price:.2f}")

                # Update local position mirrors
                local_order = self._order_history.get(order_id, {})
                local_order["status"] = "FILLED"
                local_order["fill_price"] = fill_price

                symbol = local_order["symbol"]
                side = local_order["side"]

                # Update positions dictionary
                if symbol not in self._positions:
                    self._positions[symbol] = {"qty": 0.0, "avg_price": 0.0}
                pos = self._positions[symbol]
                
                trade_qty = fill_qty if side == "BUY" else -fill_qty
                pos["qty"] += trade_qty
                if pos["qty"] == 0:
                    self._positions.pop(symbol, None)
                else:
                    pos["avg_price"] = fill_price

                # Trigger the StrategyManager callback to align strategy states
                if self._fill_callback:
                    fill_event = {
                        "strategy_id": local_order["strategy_id"],
                        "symbol": symbol,
                        "side": side,
                        "qty": fill_qty,
                        "price": fill_price,
                        "order_id": order_id,
                        "commission": self._calculate_transaction_charges(side, fill_price * fill_qty)
                    }
                    await self._fill_callback(fill_event)
                return

            elif status in ("REJECTED", "CANCELLED", "INVALID"):
                reason = response.get("rejectReason", "Order cancelled/rejected on exchange")
                dhan_logger.warning(f"[Dhan Live Broker] Order {order_id} failed: {status} - {reason}")
                if order_id in self._order_history:
                    self._order_history[order_id]["status"] = status
                return

        # If it takes too long, log timeout warning
        dhan_logger.warning(f"[Dhan Live Broker] Order {order_id} status polling timed out.")

    async def cancel_order(self, order_id: str) -> bool:
        """Cancels a pending order on Dhan."""
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: self._send_request("DELETE", f"/v2/orders/{order_id}")
        )
        if "error" not in response and response.get("orderStatus") == "CANCELLED":
            dhan_logger.info(f"[Dhan Live Broker] Successfully cancelled order {order_id}")
            return True
        return False

    def register_fill_callback(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        self._fill_callback = callback

    def get_portfolio(self) -> Dict[str, Any]:
        """Queries live available funds from Dhan (rate-limited to once every 10s) and returns mapped portfolio state."""
        import time
        now = time.time()
        
        # Only query the Dhan REST API if 10 seconds have passed to avoid blocking the event loop at 10Hz
        if now - self._last_balance_check > 10.0:
            self._last_balance_check = now
            response = self._send_request("GET", "/v2/fundlimit")
            if "availabelBalance" in response:
                self._cash = float(response["availabelBalance"])

        available_balance = self._cash

        # Calculate unrealized pnl from local position mirrors
        return {
            "cash_inr": available_balance,
            "net_asset_value_inr": available_balance, # Simple cash balance baseline
            "positions": self._positions,
            "total_fees_paid_inr": 0.0
        }

    async def on_tick(self, packet: MarketPacket) -> None:
        """Live broker does not require mock tick fills execution matching."""
        pass

    def _calculate_transaction_charges(self, side: str, turnover: float) -> float:
        """Dhan's transaction fee calculation logic."""
        brokerage = min(20.0, turnover * 0.0003)
        stt = turnover * 0.00025 if side.upper() == "SELL" else 0.0
        gst = (brokerage + (turnover * 0.0000345)) * 0.18
        stamp = turnover * 0.00003 if side.upper() == "BUY" else 0.0
        sebi = turnover * 0.0000001
        return brokerage + stt + gst + stamp + sebi
