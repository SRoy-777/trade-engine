import logging
import json
import asyncio
from datetime import datetime
from config.config import settings

class StructuredJSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "filename": record.filename,
            "line": record.lineno,
            "thread_name": record.threadName,
        }
        
        # Async Task tracking
        try:
            current_task = asyncio.current_task()
            log_entry["task_id"] = f"task-{id(current_task)}" if current_task else "main"
        except RuntimeError:
            log_entry["task_id"] = "main"

        # Capture structured extra attributes if present
        for attr in ["event_id", "correlation_id", "provider", "processing_time_ms", "session_id"]:
            if hasattr(record, attr):
                log_entry[attr] = getattr(record, attr)
            else:
                log_entry[attr] = None

        return json.dumps(log_entry)

def setup_logging():
    log_level_str = settings.LOG_LEVEL.upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers
    if root_logger.handlers:
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            
    handler = logging.StreamHandler()
    formatter = StructuredJSONFormatter()
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Re-route Uvicorn loggers to use our structured JSON format
    for logger_name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
        uv_logger = logging.getLogger(logger_name)
        uv_logger.handlers = [handler]
        uv_logger.propagate = False

setup_logging()
logger = logging.getLogger("trade_engine")
