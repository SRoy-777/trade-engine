from providers.market.dhan.config import dhan_settings
from providers.market.dhan.exceptions import DhanAuthException
from providers.market.dhan.logger import dhan_logger

class DhanAuthenticator:
    """Handles verification and secure loading of Dhan API session credentials."""

    def __init__(self):
        self._client_id = dhan_settings.CLIENT_ID
        self._access_token = dhan_settings.ACCESS_TOKEN

    def authenticate_session(self) -> dict:
        """Validates credential variables and structures parameters for WebSocket connectivity."""
        dhan_logger.info("Initializing Dhan credentials validation")
        
        # Check against placeholder defaults
        if not self._client_id or self._client_id.strip() in ("", "YOUR_DHAN_CLIENT_ID"):
            dhan_logger.error("Dhan Client ID validation failed")
            raise DhanAuthException("Dhan CLIENT_ID is missing or set to placeholder default")

        if not self._access_token or self._access_token.strip() in ("", "YOUR_DHAN_ACCESS_TOKEN"):
            dhan_logger.error("Dhan Access Token validation failed")
            raise DhanAuthException("Dhan ACCESS_TOKEN is missing or set to placeholder default")

        dhan_logger.info("Dhan credential checks passed")
        
        # Return secure session credentials container
        return {
            "client_id": self._client_id,
            "access_token": self._access_token
        }
