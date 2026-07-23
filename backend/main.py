import os
import json
import asyncio
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Force load .env relative to the backend directory before other imports are executed
backend_dir = Path(__file__).resolve().parent
load_dotenv(dotenv_path=backend_dir / ".env")

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from config.config import settings
from utils.logger_setup import logger
from storage_engine.connection import db_manager
from storage_engine.csv_source import CSVReplaySource
from storage_engine.parquet_source import ParquetReplaySource
from storage_engine.logger import duckdb_logger
from providers.market.replay import ReplayProvider
from market_feed.manager import feed_manager
from event_bus.event_bus import event_bus
from services.metrics_service import metrics_service
from api.routes import router as api_router
from api.websocket import websocket_broadcaster
from core.live_runner import live_runner

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup Initialization
    logger.info("Initializing Trade Engine backend services...")
    
    # 1. Connect to DuckDB
    db_manager.connect()
    
    # 2. Register storage subscriber to EventBus at Priority 3 (Silver Layer)
    await event_bus.subscribe(duckdb_logger.on_market_event, priority=3)
    
    # 3. Register WebSocket broadcaster to EventBus at Priority 10 (Visualization Layer)
    await websocket_broadcaster.register_subscriber()
    
    # 4. Initialize Replay Source depending on file extension
    replay_path = settings.REPLAY_FILE_PATH
    logger.info(f"Configured Replay Source File: {replay_path}")
    
    # Ensure market_data directory exists
    os.makedirs(os.path.dirname(replay_path) or "market_data", exist_ok=True)
    
    # Check file type
    if replay_path.endswith(".parquet"):
        source = ParquetReplaySource(replay_path)
    else:
        source = CSVReplaySource(replay_path)
        
    # 5. Initialize Replay Provider and bind it to the orchestrator Manager
    replay_provider = ReplayProvider(source, speed=settings.REPLAY_SPEED)
    feed_manager.set_provider(replay_provider)
    
    # 6. Start telemetry and broadcast loops
    metrics_service.start()
    websocket_broadcaster.start()
    
    # 7. Auto-start Live Strategy Runner with configs/orb.yaml values at startup
    try:
        config_path = "configs/orb.yaml"
        if not os.path.exists(config_path) and os.path.exists(os.path.join("..", config_path)):
            config_path = os.path.join("..", config_path)
            
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                yaml_config = yaml.safe_load(f) or {}
            
            raw_symbols = yaml_config.get("symbols", ["SBIN", "BAJFINANCE", "INFY", "HDFCBANK", "TATAMOTORS"])
            resolved_symbols = raw_symbols
            if isinstance(raw_symbols, str) and raw_symbols.endswith(".xlsx"):
                import pandas as pd
                xlsx_path = os.path.join(os.path.dirname(config_path), "..", raw_symbols)
                if not os.path.exists(xlsx_path):
                    xlsx_path = raw_symbols
                if not os.path.exists(xlsx_path):
                    xlsx_path = os.path.join("backend", raw_symbols)
                try:
                    df = pd.read_excel(xlsx_path, header=None)
                    resolved_symbols = df[0].dropna().astype(str).str.strip().tolist()
                    resolved_symbols = [s.upper() for s in resolved_symbols if s]
                except Exception as e:
                    logger.error(f"Error loading symbols from excel {xlsx_path}: {e}")
                    resolved_symbols = ["SBIN"]
            
            raw_priority = yaml_config.get("priority_ranking", resolved_symbols)
            if isinstance(raw_priority, list):
                priority_ranking = [s.strip().upper() for s in raw_priority if s.strip().upper() in resolved_symbols]
            else:
                priority_ranking = resolved_symbols

            live_config = {
                "symbols": resolved_symbols,
                "priority_ranking": priority_ranking,
                "allocation_strategy": yaml_config.get("allocation_strategy", "SINGLE_STOCK"),
                "allocation_weights": yaml_config.get("allocation_weights", [0.5, 0.3, 0.2]),
                "capital": yaml_config.get("capital", 100000.0),
                "leverage": yaml_config.get("leverage", 5.0),
                "enable_live_stocks": yaml_config.get("enable_live_stocks", False)
            }
            
            def ui_broadcast(update_msg):
                asyncio.create_task(websocket_broadcaster.send_to_all(update_msg))
                
            await live_runner.start(live_config, ui_broadcast)
            logger.info("Automatically started Live Trading Runner with configurations from configs/orb.yaml")
        else:
            logger.warning(f"Could not find strategy config file for auto-start: {config_path}")
    except Exception as auto_start_err:
        logger.error(f"Error auto-starting Live Trading Runner: {auto_start_err}", exc_info=True)
        
    logger.info("Trade Engine backend startup sequence completed.")
    
    yield
    
    # Shutdown sequence
    logger.info("Initiating Trade Engine shutdown sequence...")
    await feed_manager.stop()
    metrics_service.stop()
    await websocket_broadcaster.stop()
    db_manager.close()
    logger.info("Trade Engine backend shutdown completed.")

app = FastAPI(
    title="Trade Engine Platform - Phase 1",
    description="Algorithmic trading platform core market feed architecture",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for frontend cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Attach API endpoints
app.include_router(api_router)

@app.get("/health")
def read_health():
    import traceback
    import base64
    import json
    import datetime
    
    config_err = None
    resolved_paths = []
    yaml_raw = None
    
    # Check Dhan Access Token expiration
    token = os.getenv("ACCESS_TOKEN", "")
    token_exp = "No Token Found"
    is_expired = True
    try:
        if token and len(token.split(".")) >= 2:
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
            payload_bytes = base64.b64decode(payload_b64)
            payload_data = json.loads(payload_bytes.decode("utf-8"))
            exp_timestamp = payload_data.get("exp")
            if exp_timestamp:
                exp_dt = datetime.datetime.fromtimestamp(exp_timestamp, datetime.timezone.utc)
                ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
                exp_dt_ist = exp_dt.astimezone(ist_tz)
                token_exp = exp_dt_ist.strftime("%Y-%m-%d %H:%M:%S IST")
                is_expired = datetime.datetime.now(datetime.timezone.utc) > exp_dt
    except Exception as e:
        token_exp = f"Error decoding: {e}"

    try:
        config_path = "configs/orb.yaml"
        if not os.path.exists(config_path) and os.path.exists(os.path.join("..", config_path)):
            config_path = os.path.join("..", config_path)
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                yaml_config = yaml.safe_load(f) or {}
            yaml_raw = str(yaml_config.get("symbols"))
            raw_symbols = yaml_config.get("symbols", [])
            if isinstance(raw_symbols, str) and raw_symbols.endswith(".xlsx"):
                xlsx_path = os.path.join(os.path.dirname(config_path), "..", raw_symbols)
                resolved_paths.append(f"path1: {xlsx_path} (exists: {os.path.exists(xlsx_path)})")
                if not os.path.exists(xlsx_path):
                    xlsx_path = raw_symbols
                    resolved_paths.append(f"path2: {xlsx_path} (exists: {os.path.exists(xlsx_path)})")
                if not os.path.exists(xlsx_path):
                    xlsx_path = os.path.join("backend", raw_symbols)
                    resolved_paths.append(f"path3: {xlsx_path} (exists: {os.path.exists(xlsx_path)})")
    except Exception as e:
        config_err = traceback.format_exc()

    return {
        "status": "running",
        "engine": "Trade Engine Platform",
        "live_runner_active": live_runner.active,
        "dhan_connected": live_runner.connection_ok if live_runner.active else False,
        "token_expires_at": token_exp,
        "token_expired": is_expired,
        "symbols_tracked": len(live_runner.symbols) if live_runner.active else 0,
        "symbols": live_runner.symbols if live_runner.active else [],
        "yaml_raw_symbols": yaml_raw,
        "config_err": config_err,
        "resolved_paths": resolved_paths
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket_broadcaster.connect(websocket)
    try:
        while True:
            # Keep socket alive and handle client controls sent via WebSocket
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                action = msg.get("action")
                value = msg.get("value")
                
                if action == "start":
                    await feed_manager.start()
                elif action == "pause":
                    await feed_manager.pause()
                elif action == "stop":
                    await feed_manager.stop()
                elif action == "step":
                    await feed_manager.step()
                elif action == "speed":
                    speed_val = float(value if value is not None else 1.0)
                    await feed_manager.set_speed(speed_val)
                elif action == "start_live_strategy":
                    def ui_broadcast(update_msg):
                        asyncio.create_task(websocket_broadcaster.send_to_all(update_msg))
                    await live_runner.start(value or {}, ui_broadcast)
                elif action == "stop_live_strategy":
                    await live_runner.stop()
                elif action == "update_strategy_config":
                    live_runner.update_strategy_config(value or {})
            except Exception as parse_err:
                logger.error(f"Error handling websocket client packet: {parse_err}", exc_info=True)
                
    except WebSocketDisconnect:
        await websocket_broadcaster.disconnect(websocket)
    except Exception as e:
        logger.debug(f"Websocket socket handler exception: {e}")
        await websocket_broadcaster.disconnect(websocket)

# Serve compiled frontend static assets in production if directory exists
from fastapi.staticfiles import StaticFiles
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
        reload=True
    )
