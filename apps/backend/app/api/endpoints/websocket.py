import asyncio
import json
import logging
import re
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.redis_client import redis_client
from app.core.auth import decode_access_token

router = APIRouter()
logger = logging.getLogger("app.api.websocket")

CHANNELS = ["market:ticks", "market:candles", "signals:alerts"]

# US equity ticker symbols: 1-5 uppercase letters (covers NYSE/NASDAQ/AMEX).
# Allows dots and hyphens for instruments like BRK.B, BF-B.
_SYMBOL_RE = re.compile(r'^[A-Z][A-Z0-9.\-]{0,9}$')


def _is_valid_symbol(raw: str) -> bool:
    return bool(raw) and bool(_SYMBOL_RE.match(raw))


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint — bridges clients to the Redis event stream."""
    await websocket.accept()
    
    # Authenticate the WebSocket connection using query parameter token
    token = websocket.query_params.get("token")
    if not token:
        logger.warning("Rejected WebSocket connection: Token missing in query parameters.")
        await websocket.close(code=1008)
        return
        
    payload = decode_access_token(token)
    if not payload:
        logger.warning("Rejected WebSocket connection: Invalid or expired access token.")
        await websocket.close(code=1008)
        return
        
    username = payload.get("sub")
    logger.info(f"New authenticated WebSocket connection accepted for user: {username}.")

    client = redis_client.client
    if not client:
        logger.error("Redis client not initialized. Closing WebSocket.")
        await websocket.close()
        return

    pubsub = client.pubsub()
    await pubsub.subscribe(*CHANNELS)
    logger.debug(f"WebSocket subscribed to: {CHANNELS}")

    running = True

    async def client_receiver():
        nonlocal running
        try:
            while running:
                data_str = await websocket.receive_text()
                try:
                    payload = json.loads(data_str)
                    if payload.get("type") == "search":
                        raw_symbol = payload.get("symbol", "").upper().strip()
                        if not _is_valid_symbol(raw_symbol):
                            logger.warning(
                                f"Rejected invalid symbol from WebSocket client: {raw_symbol!r}"
                            )
                            continue
                        logger.info(f"Client subscribed to symbol: {raw_symbol}")
                        await client.publish(
                            "market:control",
                            json.dumps({"action": "add", "symbol": raw_symbol}),
                        )
                except (json.JSONDecodeError, TypeError) as ex:
                    logger.warning(f"Malformed WebSocket packet ignored: {ex}")
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected by client.")
            running = False
        except Exception as e:
            logger.error(f"WebSocket receiver error: {e}")
            running = False

    receiver_task = asyncio.create_task(client_receiver())

    try:
        while running:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if message and message["type"] == "message":
                msg_data = message["data"]
                if isinstance(msg_data, (str, bytes)):
                    await websocket.send_json({
                        "channel": message["channel"],
                        "data":    json.loads(msg_data),
                    })
            await asyncio.sleep(0.01)
    except Exception as e:
        logger.error(f"WebSocket broadcast loop error: {e}")
    finally:
        running = False
        receiver_task.cancel()
        await pubsub.unsubscribe(*CHANNELS)
        await pubsub.aclose()
        logger.info("WebSocket cleaned up.")
