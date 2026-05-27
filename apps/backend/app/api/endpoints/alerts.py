from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from app.core.redis_client import redis_client
from app.api.deps import get_current_active_user
from app.models.models import User

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
