from typing import List, Optional
from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PROJECT_NAME: str = "Trading Intelligence Platform"
    API_V1_STR:   str = "/api/v1"
    ENV:          str = Field("development", validation_alias="ENV")
    LOG_LEVEL:    str = Field("INFO",        validation_alias="LOG_LEVEL")

    # ── PostgreSQL ─────────────────────────────────────────────────
    DATABASE_URL:      Optional[str] = Field(None,        validation_alias="DATABASE_URL")
    POSTGRES_USER:     str           = Field("postgres",  validation_alias="POSTGRES_USER")
    POSTGRES_PASSWORD: str           = Field("postgres",  validation_alias="POSTGRES_PASSWORD")
    POSTGRES_HOST:     str           = Field("localhost", validation_alias="POSTGRES_HOST")
    POSTGRES_PORT:     int           = Field(5432,        validation_alias="POSTGRES_PORT")
    POSTGRES_DB:       str           = Field("trading_platform", validation_alias="POSTGRES_DB")

    # ── Redis ──────────────────────────────────────────────────────
    REDIS_URL: str = Field("redis://localhost:6379/0", validation_alias="REDIS_URL")

    # ── CORS ──────────────────────────────────────────────────────
    # Comma-separated list of allowed origins, or "*" for development only.
    CORS_ORIGINS: str = Field("*", validation_alias="CORS_ORIGINS")

    # ── Auth & JWT ────────────────────────────────────────────────
    # No default — must be supplied via env in all environments.
    JWT_SECRET_KEY:              str = Field(...,  validation_alias="JWT_SECRET_KEY")
    JWT_ALGORITHM:               str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(1440, validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES")  # 24 h

    # ── Admin seeding ─────────────────────────────────────────────
    ADMIN_USERNAME: str = Field("admin",              validation_alias="ADMIN_USERNAME")
    ADMIN_EMAIL:    str = Field("admin@tradingplatform.local", validation_alias="ADMIN_EMAIL")
    # No default — must be set explicitly so teams choose a real password.
    ADMIN_PASSWORD: str = Field(..., validation_alias="ADMIN_PASSWORD")

    # ── Alpaca Markets ────────────────────────────────────────────
    ALPACA_API_KEY:    Optional[str] = Field(None,  validation_alias="ALPACA_API_KEY")
    ALPACA_API_SECRET: Optional[str] = Field(None,  validation_alias="ALPACA_API_SECRET")
    # "sip" = paid (all US exchanges); "iex" = free (IEX only)
    ALPACA_FEED:       str           = Field("sip", validation_alias="ALPACA_FEED")

    # Feed staleness watchdog — seconds before connection is considered stale
    FEED_STALE_MARKET_HOURS_SECS:  float = Field(45.0,   validation_alias="FEED_STALE_MARKET_HOURS_SECS")
    FEED_STALE_OFF_HOURS_SECS:     float = Field(1800.0, validation_alias="FEED_STALE_OFF_HOURS_SECS")

    # ── OpenAI ────────────────────────────────────────────────────
    OPENAI_API_KEY: Optional[str] = Field(None,     validation_alias="OPENAI_API_KEY")
    OPENAI_MODEL:   str           = Field("gpt-4o", validation_alias="OPENAI_MODEL")

    # ── Telegram ──────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN:    Optional[str] = Field(None, validation_alias="TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID:      Optional[str] = Field(None, validation_alias="TELEGRAM_CHAT_ID")
    # Minimum seconds between alerts for the same symbol (per-symbol cooldown)
    TELEGRAM_ALERT_COOLDOWN: int = Field(900, validation_alias="TELEGRAM_ALERT_COOLDOWN")

    # ── Candle engine ─────────────────────────────────────────────
    CANDLE_WINDOW_SECS:  int = Field(5,    validation_alias="CANDLE_WINDOW_SECS")   # granularity of each candle
    CANDLE_HISTORY_SIZE: int = Field(1440, validation_alias="CANDLE_HISTORY_SIZE")  # max candles kept in memory (~2 h)

    # Minimum closed candles before the engine is allowed to fire any alert.
    # 120 × 5 s = 10 minutes of data — ensures indicators are meaningful.
    MIN_CANDLE_HISTORY: int = Field(120, validation_alias="MIN_CANDLE_HISTORY")

    # ── Strategy / signal quality gates ───────────────────────────
    # Upgrade engine overall minimum confidence (0–100) to fire an alert
    MIN_CONFIDENCE: int = Field(55, validation_alias="MIN_CONFIDENCE")

    # Consensus gate: require this many strategies agreeing, OR a single
    # strategy whose confidence meets CONSENSUS_SINGLE_MIN_CONF.
    CONSENSUS_MIN_STRATEGIES:   int = Field(2,  validation_alias="CONSENSUS_MIN_STRATEGIES")
    CONSENSUS_SINGLE_MIN_CONF:  int = Field(78, validation_alias="CONSENSUS_SINGLE_MIN_CONF")

    # Direction-reversal guard: after a BUY, require stronger conviction for SELL.
    REVERSAL_MIN_STRATEGIES: int = Field(3,  validation_alias="REVERSAL_MIN_STRATEGIES")
    REVERSAL_MIN_CONF:        int = Field(75, validation_alias="REVERSAL_MIN_CONF")

    # ── Global alert rate limiter ─────────────────────────────────
    RATE_LIMIT_WINDOW_SECS: int = Field(600, validation_alias="RATE_LIMIT_WINDOW_SECS")  # 10-min window
    RATE_LIMIT_MAX_ALERTS:  int = Field(4,   validation_alias="RATE_LIMIT_MAX_ALERTS")   # max per window

    # ── Opening Drive module ───────────────────────────────────────
    OD_MIN_CONFIDENCE: int   = Field(55,  validation_alias="OD_MIN_CONFIDENCE")
    OD_MIN_RVOL:       float = Field(1.5, validation_alias="OD_MIN_RVOL")
    OD_MIN_GAP_PCT:    float = Field(0.5, validation_alias="OD_MIN_GAP_PCT")

    # ── S/R strategy module ───────────────────────────────────────
    SR_MIN_CONFIDENCE: int = Field(55, validation_alias="SR_MIN_CONFIDENCE")

    # ── Default watchlist (comma-separated) ───────────────────────
    # First login / reset seeds these symbols for every new user.
    DEFAULT_WATCHLIST: str = Field(
        "TSLA,NBIS,COST,SPX,APPLOVIN",
        validation_alias="DEFAULT_WATCHLIST",
    )

    # ── Sentry ────────────────────────────────────────────────────
    SENTRY_DSN: Optional[str] = Field(None, validation_alias="SENTRY_DSN")

    # ── Validators ────────────────────────────────────────────────

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def _jwt_secret_not_placeholder(cls, v: str) -> str:
        weak = {"changeme", "secret", "your_jwt_secret"}
        if any(w in v.lower() for w in weak):
            raise ValueError(
                "JWT_SECRET_KEY looks like a placeholder. "
                "Set a strong random secret in your environment."
            )
        if len(v) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters.")
        return v

    @field_validator("ADMIN_PASSWORD")
    @classmethod
    def _admin_password_not_weak(cls, v: str) -> str:
        weak = {"adminpass123", "admin", "password", "changeme", "your_"}
        if any(w in v.lower() for w in weak):
            raise ValueError(
                "ADMIN_PASSWORD is too weak or is a placeholder. "
                "Set a strong password in your environment."
            )
        if len(v) < 10:
            raise ValueError("ADMIN_PASSWORD must be at least 10 characters.")
        return v

    @field_validator("OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "ALPACA_API_KEY", "ALPACA_API_SECRET", mode="before")
    @classmethod
    def _reject_placeholder_keys(cls, v: object) -> object:
        if isinstance(v, str) and v.startswith("your_"):
            raise ValueError(
                f"API key value '{v}' looks like a placeholder. "
                "Either set the real value or leave the variable unset."
            )
        return v

    # ── Computed fields ────────────────────────────────────────────

    @computed_field
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            if "sslmode=require" in url:
                url = url.replace("sslmode=require", "ssl=require")
            return url
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field
    @property
    def DEFAULT_WATCHLIST_SYMBOLS(self) -> List[str]:
        """Parsed list of default watchlist symbols."""
        return [s.strip().upper() for s in self.DEFAULT_WATCHLIST.split(",") if s.strip()]

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.ENV.lower() == "production"


settings = Settings()  # type: ignore
