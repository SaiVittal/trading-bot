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
    watchlist_symbols: list = ["TSLA", "NBIS", "COST", "SPX", "APPLOVIN"]
    try:
        stored = await client.smembers("watchlist:symbols")
        if stored:
            watchlist_symbols = list(stored)

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

        # ── Send recent candle history (last 50 per symbol) ───────
        # Allows the chart to render immediately on connect without waiting for new ticks.
        for sym in watchlist_symbols:
            try:
                raw_candles = await client.lrange(f"candles:recent:{sym}", -50, -1)
                if raw_candles:
                    await websocket.send_json({
                        "channel": "market:candles:history",
                        "data": {
                            "symbol": sym,
                            "candles": [json.loads(c) for c in raw_candles],
                        },
                    })
            except Exception as _e:
                logger.debug(f"Candle history send skipped for {sym}: {_e}")

        # ── Send recent alert history (last 15 across all watchlist symbols) ──
        # Populates the signal panel immediately on connect.
        try:
            all_alerts: list = []
            seen_ids: set = set()
            for sym in watchlist_symbols:
                raw_alerts = await client.lrange(f"alerts:recent:{sym}", 0, 14)
                for r in raw_alerts:
                    a = json.loads(r)
                    uid = f"{a.get('symbol')}_{a.get('timestamp')}"
                    if uid not in seen_ids:
                        seen_ids.add(uid)
                        all_alerts.append(a)
            if all_alerts:
                all_alerts.sort(key=lambda a: a.get("timestamp", 0), reverse=True)
                await websocket.send_json({
                    "channel": "signals:alerts:history",
                    "data": all_alerts[:15],
                })
                logger.info(f"Sent {len(all_alerts[:15])} cached alert(s) to {username}")
        except Exception as _e:
            logger.debug(f"Alert history send failed for {username}: {_e}")

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
