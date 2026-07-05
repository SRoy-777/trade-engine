from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class DhanSettings(BaseSettings):
    DHAN_API_KEY: str = Field(default="")
    DHAN_API_SECRET: str = Field(default="")
    CLIENT_ID: str = Field(...)
    ACCESS_TOKEN: str = Field(...)
    WS_URL: str = Field(default="wss://api-feed.dhan.co")
    LOG_LEVEL: str = Field(default="INFO")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Instantiate settings specifically for Dhan context
dhan_settings = DhanSettings()
