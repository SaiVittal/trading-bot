import json
import logging
import re
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.redis_client import redis_client
from app.core.auth import decode_access_token
from app.core.websocket_manager import websocket_broadcaster

router = APIRouter()
logger = logging.getLogger("app.api.websocket")

# US equity ticker symbols: 1-5 uppercase letters (covers NYSE/NASDAQ/AMEX).
# Allows dots and hyphens for instruments like BRK.B, BF-B.
_SYMBOL_RE = re.compile(r'^[A-Z][A-Z0-9.\-]{0,9}$')


def _is_valid_symbol(raw: str) -> bool:
    return bool(raw) and bool(_SYMBOL_RE.match(raw))


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint — bridges clients to the Redis event stream using a centralized broadcaster."""
    # Retrieve the WebSocket connection token from query parameters
    token = websocket.query_params.get("token")
    if not token:
        logger.warning("Rejected WebSocket connection: Token missing in query parameters.")
        await websocket.accept()
        await websocket.close(code=1008)
        return
        
    payload = decode_access_token(token)
    if not payload:
        logger.warning("Rejected WebSocket connection: Invalid or expired access token.")
        await websocket.accept()
        await websocket.close(code=1008)
        return
        
    username = payload.get("sub")
    logger.info(f"New authenticated WebSocket connection request from user: {username}.")

    from typing import Any, cast
    client = cast(Any, redis_client.client)
    if not client:
        logger.error("Redis client not initialized. Closing WebSocket.")
        await websocket.accept()
        await websocket.close()
        return

    # Add client to the global registry
    await websocket_broadcaster.connect(websocket)

    # Retrieve and sync the persistent watchlist with the connected client
    try:
        stored = await client.smembers("watchlist:symbols")
        watchlist_symbols = list(stored) if stored else ["TSLA", "NBIS", "COST", "SPX", "APPLOVIN"]
        
        await websocket.send_json({
            "channel": "watchlist:sync",
            "data": watchlist_symbols,
        })
        logger.info(f"Synced watchlist to user {username}: {watchlist_symbols}")

        # Send last cached feed status
        status_val = await client.get("market:status:last")
        if status_val:
            await websocket.send_json({
                "channel": "market:status",
                "data": json.loads(status_val),
            })
            logger.info(f"Synced last feed status to user {username}: {status_val}")
    except Exception as e:
        logger.error(f"Failed to sync state to client {username} on connect: {e}")

    try:
        while True:
            data_str = await websocket.receive_text()
            try:
                payload = json.loads(data_str)
                msg_type = payload.get("type")
                
                if msg_type == "search":
                    raw_symbol = payload.get("symbol", "").upper().strip()
                    if not _is_valid_symbol(raw_symbol):
                        logger.warning(
                            f"Rejected invalid symbol from WebSocket client: {raw_symbol!r}"
                        )
                        continue
                    logger.info(f"Client {username} subscribed to symbol: {raw_symbol}")
                    await client.publish(
                        "market:control",
                        json.dumps({"action": "add", "symbol": raw_symbol}),
                    )
                elif msg_type == "remove":
                    raw_symbol = payload.get("symbol", "").upper().strip()
                    if not _is_valid_symbol(raw_symbol):
                        logger.warning(
                            f"Rejected invalid symbol from WebSocket client: {raw_symbol!r}"
                        )
                        continue
                    logger.info(f"Client {username} requested removal of symbol: {raw_symbol}")
                    await client.publish(
                        "market:control",
                        json.dumps({"action": "remove", "symbol": raw_symbol}),
                    )
                elif msg_type == "ping":
                    # Respond to keep-alive pings immediately
                    await websocket.send_json({
                        "channel": "pong",
                        "data": "pong"
                    })
            except (json.JSONDecodeError, TypeError) as ex:
                logger.warning(f"Malformed WebSocket packet ignored from client: {ex}")
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected cleanly by user: {username}.")
    except Exception as e:
        logger.error(f"WebSocket receiver error for user {username}: {e}")
    finally:
        # Prune connection from active registry
        await websocket_broadcaster.disconnect(websocket)
        logger.info(f"WebSocket cleaned up and connection unregistered for user: {username}.")
