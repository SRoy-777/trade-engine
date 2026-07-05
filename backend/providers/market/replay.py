import asyncio
import uuid
import json
import time
from datetime import datetime
from typing import Callable, Awaitable, Dict, Any, Optional
from core.provider import BaseMarketProvider
from core.replay_source import ReplaySource
from models.market import RawPacket
from utils.logger_setup import logger

class ReplayProvider(BaseMarketProvider):
    """Provider that replays historical data from a ReplaySource in various playback modes."""

    def __init__(self, source: ReplaySource, speed: float = 1.0):
        self._source = source
        self._speed = speed
        self._status = "STOPPED"
        # Modes: "MULTIPLIER", "MAX", "STEP"
        self._mode = "MULTIPLIER" if speed > 0 else "MAX"
        
        self._packet_callback: Optional[Callable[[RawPacket], Awaitable[None]]] = None
        self._playback_task: Optional[asyncio.Task] = None
        self._play_event = asyncio.Event()
        self._step_event = asyncio.Event()

        # Telemetry metrics
        self._packets_processed = 0
        self._last_symbol: Optional[str] = None
        self._last_price: float = 0.0
        self._last_timestamp: Optional[str] = None
        self._session_id: Optional[str] = None
        self._start_time: Optional[float] = None
        self._elapsed_time: float = 0.0

    @property
    def provider_name(self) -> str:
        return "replay"

    @property
    def status(self) -> str:
        return self._status

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def mode(self) -> str:
        return self._mode

    def set_packet_callback(self, callback: Callable[[RawPacket], Awaitable[None]]) -> None:
        self._packet_callback = callback

    async def start(self) -> None:
        """Starts or resumes the playback stream."""
        if self._status == "RUNNING":
            return

        logger.info(f"Starting ReplayProvider (speed={self._speed}, mode={self._mode})")
        
        if self._status == "STOPPED":
            self._packets_processed = 0
            self._last_symbol = None
            self._last_price = 0.0
            self._last_timestamp = None
            self._start_time = time.perf_counter()
            self._elapsed_time = 0.0
            await self._source.open()
            self._source.reset()
            self._status = "RUNNING"
            self._play_event.set()
            self._playback_task = asyncio.create_task(self._playback_loop())
        elif self._status == "PAUSED":
            self._status = "RUNNING"
            self._play_event.set()
            # If we were in STEP mode, resume normal playback mode
            if self._mode == "STEP":
                self._mode = "MULTIPLIER" if self._speed > 0 else "MAX"

    async def pause(self) -> None:
        """Pauses the playback stream."""
        if self._status != "RUNNING":
            return
        logger.info("Pausing ReplayProvider")
        self._status = "PAUSED"
        self._play_event.clear()

    async def stop(self) -> None:
        """Stops the playback stream entirely, closing source file descriptors."""
        if self._status == "STOPPED":
            return
        logger.info("Stopping ReplayProvider")
        self._status = "STOPPED"
        self._play_event.clear()
        self._step_event.clear()
        
        if self._playback_task:
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
            self._playback_task = None
            
        await self._source.close()

    async def set_speed(self, speed: float) -> None:
        """Dynamically adjusts playback speed."""
        logger.info(f"Setting ReplayProvider speed to: {speed}")
        self._speed = speed
        if speed <= 0:
            self._mode = "MAX"
        elif self._mode != "STEP":
            self._mode = "MULTIPLIER"

    async def enable_step_mode(self) -> None:
        """Enables step-by-step debug playback."""
        logger.info("Enabling Step Mode in ReplayProvider")
        self._mode = "STEP"
        self._step_event.clear()
        if self._status == "STOPPED":
            # Start in paused state
            await self.start()
            await self.pause()
            self._status = "PAUSED"

    async def step(self) -> None:
        """Advances playback by exactly one tick packet."""
        if self._mode != "STEP":
            await self.enable_step_mode()
        
        logger.debug("Stepping ReplayProvider by 1 packet")
        # Ensure we are in running/paused status and allow one packet to process
        if self._status == "PAUSED":
            self._status = "RUNNING"
            self._play_event.set()
        
        self._step_event.set()

    def get_status(self) -> Dict[str, Any]:
        """Exposes playback telemetry."""
        if self._start_time and self._status == "RUNNING":
            self._elapsed_time = time.perf_counter() - self._start_time
            
        return {
            "provider_name": self.provider_name,
            "status": self._status,
            "speed": self._speed,
            "mode": self._mode,
            "packets_processed": self._packets_processed,
            "last_symbol": self._last_symbol,
            "last_price": self._last_price,
            "last_timestamp": self._last_timestamp,
            "elapsed_time_secs": round(self._elapsed_time, 2)
        }

    async def _playback_loop(self) -> None:
        """Core background replay execution routine."""
        prev_event_time: Optional[datetime] = None
        
        while self._status != "STOPPED":
            try:
                # If paused, block until resume
                if self._status == "PAUSED":
                    await self._play_event.wait()
                    prev_event_time = None
                    continue

                # If in step mode, block until manual step trigger
                if self._mode == "STEP":
                    await self._step_event.wait()
                    self._step_event.clear()
                    # Re-check status in case it stopped during wait
                    if self._status != "RUNNING":
                        continue

                # Retrieve next sequential raw packet payload
                tick = await self._source.read_next()
                if tick is None:
                    logger.info("Replay source EOF reached.")
                    self._status = "STOPPED"
                    break

                # Handle playback sleep delay for simulated real-time/multiplier modes
                if self._mode == "MULTIPLIER" and prev_event_time is not None:
                    curr_ts_str = tick.get("timestamp")
                    if curr_ts_str:
                        try:
                            # Normalize ISO string Z format for parsing
                            curr_event_time = datetime.fromisoformat(curr_ts_str.replace("Z", "+00:00"))
                            time_diff_secs = (curr_event_time - prev_event_time).total_seconds()
                            
                            if time_diff_secs > 0:
                                sleep_secs = time_diff_secs / self._speed
                                # Prevent excessive hangs from large raw intervals (limit to max 5s divided by speed)
                                sleep_secs = min(sleep_secs, 5.0 / self._speed)
                                if sleep_secs > 0.001:
                                    await asyncio.sleep(sleep_secs)
                        except Exception as parse_err:
                            logger.debug(f"Replay timing offset parsing error: {parse_err}")

                # Save current timestamp for next iteration diff
                curr_ts_str = tick.get("timestamp")
                if curr_ts_str:
                    try:
                        prev_event_time = datetime.fromisoformat(curr_ts_str.replace("Z", "+00:00"))
                    except Exception:
                        pass

                # Package dictionary into RawPacket schema
                packet_id = str(uuid.uuid4())
                received_ts = datetime.utcnow()
                
                # Payload carries raw values exactly as received from broker
                payload_str = json.dumps(tick)

                raw_packet = RawPacket(
                    packet_id=packet_id,
                    provider=self.provider_name,
                    received_timestamp=received_ts,
                    raw_payload=payload_str
                )

                # Route to callback
                if self._packet_callback:
                    await self._packet_callback(raw_packet)

                # Update stats
                self._packets_processed += 1
                self._last_symbol = tick.get("symbol")
                self._last_price = tick.get("ltp", 0.0)
                self._last_timestamp = curr_ts_str

            except asyncio.CancelledError:
                break
            except Exception as loop_err:
                logger.error(f"Error in replay playback execution loop: {loop_err}")
                await asyncio.sleep(0.1)  # Throttle on error to prevent hot-looping
                
        # Close connection when loop exits
        await self._source.close()
