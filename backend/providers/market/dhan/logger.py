import logging
import json
from datetime import datetime, timezone
from providers.market.dhan.config import dhan_settings

class DhanJSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "module": record.module,
            "log_level": record.levelname,
            "event": record.getMessage()
        }
        
        # Guard against logging secrets
        serialized = json.dumps(log_entry)
        for secret in [dhan_settings.ACCESS_TOKEN, dhan_settings.DHAN_API_KEY, dhan_settings.DHAN_API_SECRET]:
            if secret and len(secret) > 8 and secret in serialized:
                serialized = serialized.replace(secret, "********")
                
        return serialized

def get_dhan_logger():
    log_level_str = dhan_settings.LOG_LEVEL.upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    
    logger = logging.getLogger("trade_engine.dhan")
    logger.setLevel(log_level)
    
    # Avoid adding duplicate handlers if they exist
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = DhanJSONFormatter()
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.propagate = False
        
    return logger

dhan_logger = get_dhan_logger()
