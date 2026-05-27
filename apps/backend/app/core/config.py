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
    DATABASE_URL:      Optional[str] = Field(None, validation_alias="DATABASE_URL")
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

    # Polygon.io
    POLYGON_API_KEY:      Optional[str] = Field(None, validation_alias="POLYGON_API_KEY")

    # Telegram
    TELEGRAM_BOT_TOKEN:    Optional[str] = Field(None,  validation_alias="TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID:      Optional[str] = Field(None,  validation_alias="TELEGRAM_CHAT_ID")
    TELEGRAM_ALERT_COOLDOWN: int         = Field(120,   validation_alias="TELEGRAM_ALERT_COOLDOWN")

    # Security & JWT Auth
    JWT_SECRET_KEY:     str = Field("prod-super-secure-jwt-secret-key-antigravity", validation_alias="JWT_SECRET_KEY")
    JWT_ALGORITHM:      str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    # Sentry & Environment
    ENV:         str = Field("development", validation_alias="ENV")
    SENTRY_DSN: Optional[str] = Field(None, validation_alias="SENTRY_DSN")

    # Upgrade engine — minimum confidence score (0-100) to fire an alert
    MIN_CONFIDENCE: int = Field(55, validation_alias="MIN_CONFIDENCE")

    LOG_LEVEL: str = "INFO"

    @computed_field
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        if self.DATABASE_URL:
            # Render/Neon style connection string - convert standard postgresql:// to postgresql+asyncpg://
            url = self.DATABASE_URL
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            # Ensure sslmode=require doesn't conflict with asyncpg unless stripped/properly configured, but standard Neon works fine
            return url
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


settings = Settings()  # type: ignore
