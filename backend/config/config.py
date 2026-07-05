import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    REPLAY_FILE_PATH: str = Field(default="market_data/historical_data.csv")
    REPLAY_SPEED: float = Field(default=1.0)
    DATABASE_PATH: str = Field(default="storage/trade_engine.db")
    BRONZE_STORAGE_DIR: str = Field(default="storage/bronze")
    LOG_LEVEL: str = Field(default="INFO")
    HOST: str = Field(default="127.0.0.1")
    PORT: int = Field(default=8000)
    BATCH_FLUSH_INTERVAL_SECS: float = Field(default=0.5)
    BATCH_FLUSH_SIZE: int = Field(default=500)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Instantiate settings
settings = Settings()
