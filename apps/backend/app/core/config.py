from typing import Optional
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )

    PROJECT_NAME: str = "Trading Intelligence Platform"
    API_V1_STR: str = "/api/v1"

    # PostgreSQL Database Settings
    POSTGRES_USER: str = Field("postgres", validation_alias="POSTGRES_USER")
    POSTGRES_PASSWORD: str = Field("postgres", validation_alias="POSTGRES_PASSWORD")
    POSTGRES_HOST: str = Field("localhost", validation_alias="POSTGRES_HOST")
    POSTGRES_PORT: int = Field(5432, validation_alias="POSTGRES_PORT")
    POSTGRES_DB: str = Field("trading_platform", validation_alias="POSTGRES_DB")

    # Redis Cache & Bus Settings
    REDIS_URL: str = Field("redis://localhost:6379/0", validation_alias="REDIS_URL")

    # OpenAI API Settings
    OPENAI_API_KEY: Optional[str] = Field(None, validation_alias="OPENAI_API_KEY")

    # Alpaca Markets API Settings
    ALPACA_API_KEY_ID: Optional[str] = Field(None, validation_alias="ALPACA_API_KEY_ID")
    ALPACA_API_SECRET_KEY: Optional[str] = Field(None, validation_alias="ALPACA_API_SECRET_KEY")

    # Telegram Notification Settings
    TELEGRAM_BOT_TOKEN: Optional[str] = Field(None, validation_alias="TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID: Optional[str] = Field(None, validation_alias="TELEGRAM_CHAT_ID")
    SLACK_WEBHOOK_URL: Optional[str] = Field(None, validation_alias="SLACK_WEBHOOK_URL")

    # Logging config
    LOG_LEVEL: str = "INFO"

    @computed_field
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"


settings = Settings()
