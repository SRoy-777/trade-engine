from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class OrbConfig(BaseSettings):
    """Configuration values for the Opening Range Breakout (ORB) strategy."""
    ORB_START: str = Field(default="09:15")
    ORB_END: str = Field(default="09:30")
    VOLUME_LOOKBACK: int = Field(default=20)
    MIN_VOLUME_MULTIPLIER: float = Field(default=1.5)
    RISK_REWARD: float = Field(default=1.5)
    MAX_TRADES_PER_DAY: int = Field(default=1)
    LAST_ENTRY_TIME: str = Field(default="14:30")
    SQUARE_OFF_TIME: str = Field(default="15:10")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Instantiate global config block
orb_config = OrbConfig()
