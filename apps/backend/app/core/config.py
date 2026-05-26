from typing import Optional
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PROJECT_NAME: str = "Trading Intelligence Platform"
    API_V1_STR:   str = "/api/v1"

    # PostgreSQL
    POSTGRES_USER:     str = Field("postgres",         validation_alias="POSTGRES_USER")
    POSTGRES_PASSWORD: str = Field("postgres",         validation_alias="POSTGRES_PASSWORD")
    POSTGRES_HOST:     str = Field("localhost",        validation_alias="POSTGRES_HOST")
    POSTGRES_PORT:     int = Field(5432,               validation_alias="POSTGRES_PORT")
    POSTGRES_DB:       str = Field("trading_platform", validation_alias="POSTGRES_DB")

    # Redis
    REDIS_URL: str = Field("redis://localhost:6379/0", validation_alias="REDIS_URL")

    # CORS — comma-separated origins or "*" for development wildcard
    CORS_ORIGINS: str = Field("*", validation_alias="CORS_ORIGINS")

    # OpenAI
    OPENAI_API_KEY: Optional[str] = Field(None, validation_alias="OPENAI_API_KEY")

    # Alpaca Markets
    ALPACA_API_KEY_ID:    Optional[str] = Field(None, validation_alias="ALPACA_API_KEY_ID")
    ALPACA_API_SECRET_KEY: Optional[str] = Field(None, validation_alias="ALPACA_API_SECRET_KEY")


    # Telegram
    TELEGRAM_BOT_TOKEN:    Optional[str] = Field(None,  validation_alias="TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID:      Optional[str] = Field(None,  validation_alias="TELEGRAM_CHAT_ID")
    TELEGRAM_ALERT_COOLDOWN: int         = Field(120,   validation_alias="TELEGRAM_ALERT_COOLDOWN")

    # Upgrade engine — minimum confidence score (0-100) to fire an alert
    MIN_CONFIDENCE: int = Field(55, validation_alias="MIN_CONFIDENCE")

    LOG_LEVEL: str = "INFO"

    @computed_field
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()  # type: ignore
