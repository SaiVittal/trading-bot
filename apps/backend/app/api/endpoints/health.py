import time
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.database import get_db
from app.core.redis_client import redis_client

router = APIRouter()

# Record startup timestamp to calculate uptime
START_TIME = time.time()

@router.get("", status_code=status.HTTP_200_OK)
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Perform a deep diagnostics health check on all connected storage subsystems.
    """
    diagnostics = {
        "status": "healthy",
        "database": "disconnected",
        "redis": "disconnected",
        "uptime_seconds": round(time.time() - START_TIME, 2)
    }

    # 1. Verify PostgreSQL Database Read/Write loop
    try:
        # Run a simple SELECT 1 to verify execution
        db_result = await db.execute(text("SELECT 1"))
        if db_result.scalar() == 1:
            diagnostics["database"] = "connected"
    except Exception as e:
        diagnostics["status"] = "unhealthy"
        diagnostics["database"] = f"failed: {str(e)}"

    # 2. Verify Redis In-Memory Client Ping loop
    try:
        redis_ok = await redis_client.ping()
        if redis_ok:
            diagnostics["redis"] = "connected"
        else:
            diagnostics["status"] = "unhealthy"
            diagnostics["redis"] = "failed ping response"
    except Exception as e:
        diagnostics["status"] = "unhealthy"
        diagnostics["redis"] = f"failed: {str(e)}"

    # Return HTTP 503 if any subsystem is down
    if diagnostics["status"] == "unhealthy":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=diagnostics
        )

    return diagnostics
