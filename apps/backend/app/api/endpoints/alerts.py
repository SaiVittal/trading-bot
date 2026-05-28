import json
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.redis_client import redis_client
from app.core.database import get_db
from app.api.deps import get_current_active_user
from app.models.models import User, TradingSignal

router = APIRouter()

class TelegramToggleRequest(BaseModel):
    enabled: bool

class TelegramToggleResponse(BaseModel):
    enabled: bool
    message: str

@router.get("/telegram/status", response_model=TelegramToggleResponse)
async def get_telegram_alerts_status(
    current_user: User = Depends(get_current_active_user)
) -> TelegramToggleResponse:
    """Get the current status of Telegram alerts."""
    enabled = True
    if redis_client.client:
        try:
            status_val = await redis_client.client.get("telegram_alerts_enabled")
            if status_val == "false":
                enabled = False
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read alert status from Redis: {str(e)}"
            )
    return TelegramToggleResponse(
        enabled=enabled,
        message=f"Telegram alerts are {'enabled' if enabled else 'disabled'}."
    )

@router.post("/telegram/toggle", response_model=TelegramToggleResponse)
async def toggle_telegram_alerts(
    payload: TelegramToggleRequest,
    current_user: User = Depends(get_current_active_user)
) -> TelegramToggleResponse:
    """Enable or disable Telegram alerts dynamically."""
    if redis_client.client:
        try:
            val = "true" if payload.enabled else "false"
            await redis_client.client.set("telegram_alerts_enabled", val)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to write alert status to Redis: {str(e)}"
            )
    return TelegramToggleResponse(
        enabled=payload.enabled,
        message=f"Telegram alerts have been successfully {'enabled' if payload.enabled else 'disabled'}."
    )


@router.get("/signals/recent")
async def get_recent_signals_redis(
    symbol: Optional[str] = Query(None, description="Filter by ticker symbol"),
    limit: int = Query(default=15, ge=1, le=50),
    current_user: User = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """
    Fetch the most recent alert payloads from Redis cache.
    Returns full structured alert objects (fast, for UI population on connect).
    """
    client = redis_client.client
    if not client:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis unavailable")
    try:
        if symbol:
            raw = await client.lrange(f"alerts:recent:{symbol.upper()}", 0, limit - 1)
            return [json.loads(r) for r in raw]

        # No symbol filter — fetch across all watchlist symbols
        watchlist_raw = await client.smembers("watchlist:symbols")
        all_alerts: List[Dict] = []
        seen: set = set()
        for sym in watchlist_raw:
            for r in await client.lrange(f"alerts:recent:{sym}", 0, limit - 1):
                a = json.loads(r)
                uid = f"{a.get('symbol')}_{a.get('timestamp')}"
                if uid not in seen:
                    seen.add(uid)
                    all_alerts.append(a)
        all_alerts.sort(key=lambda a: a.get("timestamp", 0), reverse=True)
        return all_alerts[:limit]
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/signals/history")
async def get_signal_history_db(
    symbol: Optional[str] = Query(None, description="Filter by ticker symbol"),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """
    Fetch signal audit log from PostgreSQL (for historical analysis).
    Returns core fields only (not the full alert payload — use /signals/recent for that).
    """
    stmt = select(TradingSignal).order_by(TradingSignal.timestamp.desc()).limit(limit)
    if symbol:
        stmt = stmt.where(TradingSignal.symbol == symbol.upper())
    result = await db.execute(stmt)
    signals = result.scalars().all()
    return [
        {
            "signal_id":     s.signal_id,
            "symbol":        s.symbol,
            "side":          s.side,
            "price":         s.price,
            "strategy_name": s.strategy_name,
            "confidence":    s.confidence,
            "status":        s.status,
            "timestamp":     s.timestamp.isoformat() if s.timestamp else None,
        }
        for s in signals
    ]


@router.get("/candles/{symbol}")
async def get_candle_history(
    symbol: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """
    Fetch recent 5-second candles from Redis cache for a symbol.
    Used to seed the chart on page load / reconnect.
    """
    client = redis_client.client
    if not client:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis unavailable")
    try:
        raw = await client.lrange(f"candles:recent:{symbol.upper()}", -limit, -1)
        return [json.loads(c) for c in raw]
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
