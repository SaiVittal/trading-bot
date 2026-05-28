from fastapi import APIRouter
from app.api.endpoints import health, websocket, auth, alerts, watchlist

api_router = APIRouter()

# Register core endpoint groups
api_router.include_router(auth.router,      prefix="/auth",      tags=["user-authentication"])
api_router.include_router(health.router,    prefix="/health",    tags=["system-telemetry"])
api_router.include_router(websocket.router, prefix="/ws",        tags=["realtime-websockets"])
api_router.include_router(alerts.router,    prefix="/alerts",    tags=["alerts-control"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["watchlist"])

