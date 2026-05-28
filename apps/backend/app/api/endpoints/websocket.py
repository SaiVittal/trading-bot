import json
import logging
import re
from typing import Any, cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.redis_client import redis_client
from app.core.auth import decode_access_token
from app.core.database import async_session
from app.core.websocket_manager import websocket_broadcaster
from app.models.models import User, UserWatchlist
from app.api.endpoints.watchlist import (
    get_user_symbols_cached,
    _add_to_global,
    _remove_from_global_if_unused,
    _user_cache_key,
    _USER_CACHE_TTL,
    _valid_symbol,
)

router = APIRouter()
logger = logging.getLogger("app.api.websocket")

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _redis() -> Any:
    return cast(Any, redis_client.client)


@router.websocket("")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint — bridges authenticated clients to the Redis event
    stream using a centralized broadcaster.

    On connect:
      1. Validate JWT token
      2. Load user's personal watchlist from DB/Redis cache
      3. Send watchlist:sync, market:status, candle history, alert history
      4. Enter the receive loop — handle add/remove/ping messages,
         persisting watchlist changes to DB + Redis + market feed.
    """
    token = websocket.query_params.get("token")
    if not token:
        logger.warning("Rejected WebSocket: missing token.")
        await websocket.accept()
        await websocket.close(code=1008)
        return

    payload = decode_access_token(token)
    if not payload:
        logger.warning("Rejected WebSocket: invalid/expired token.")
        await websocket.accept()
        await websocket.close(code=1008)
        return

    username = payload.get("sub")

    # ── Resolve user from DB ────────────────────────────────────────
    user_obj: User | None = None
    async with async_session() as db:
        result = await db.execute(select(User).where(User.username == username))
        user_obj = result.scalar_one_or_none()

    if not user_obj:
        logger.warning(f"Rejected WebSocket: user {username!r} not found in DB.")
        await websocket.accept()
        await websocket.close(code=1008)
        return

    user_id = user_obj.id
    logger.info(f"WebSocket connect: {username} (id={user_id})")

    client = _redis()
    if not client:
        logger.error("Redis not available. Closing WebSocket.")
        await websocket.accept()
        await websocket.close()
        return

    await websocket_broadcaster.connect(websocket)

    # ── Send initial state ──────────────────────────────────────────
    try:
        # 1. User-specific watchlist from DB/cache
        async with async_session() as db:
            watchlist_symbols = await get_user_symbols_cached(user_id, db)

        # Ensure these symbols are in the global market-feed set
        for sym in watchlist_symbols:
            await _add_to_global(sym)

        await websocket.send_json({
            "channel": "watchlist:sync",
            "data":    watchlist_symbols,
        })
        logger.info(f"Synced watchlist to {username}: {watchlist_symbols}")

        # 2. Last market status
        status_val = await client.get("market:status:last")
        if status_val:
            await websocket.send_json({
                "channel": "market:status",
                "data":    json.loads(status_val),
            })

        # 3. Candle history (last 50 per symbol)
        for sym in watchlist_symbols:
            try:
                raw_candles = await client.lrange(f"candles:recent:{sym}", -50, -1)
                if raw_candles:
                    await websocket.send_json({
                        "channel": "market:candles:history",
                        "data": {
                            "symbol":  sym,
                            "candles": [json.loads(c) for c in raw_candles],
                        },
                    })
            except Exception as _e:
                logger.debug(f"Candle history skipped for {sym}: {_e}")

        # 4. Alert history (last 15 across user's watchlist)
        try:
            all_alerts: list = []
            seen_ids:   set  = set()
            for sym in watchlist_symbols:
                raw_alerts = await client.lrange(f"alerts:recent:{sym}", 0, 14)
                for r in raw_alerts:
                    a   = json.loads(r)
                    uid = f"{a.get('symbol')}_{a.get('timestamp')}"
                    if uid not in seen_ids:
                        seen_ids.add(uid)
                        all_alerts.append(a)
            if all_alerts:
                all_alerts.sort(key=lambda a: a.get("timestamp", 0), reverse=True)
                await websocket.send_json({
                    "channel": "signals:alerts:history",
                    "data":    all_alerts[:15],
                })
                logger.info(f"Sent {min(len(all_alerts), 15)} cached alert(s) to {username}")
        except Exception as _e:
            logger.debug(f"Alert history send failed for {username}: {_e}")

    except Exception as e:
        logger.error(f"Failed to sync initial state to {username}: {e}")

    # ── Receive loop ────────────────────────────────────────────────
    try:
        while True:
            data_str = await websocket.receive_text()
            try:
                msg      = json.loads(data_str)
                msg_type = msg.get("type")

                if msg_type == "ping":
                    await websocket.send_json({"channel": "pong", "data": "pong"})

                elif msg_type == "search":
                    raw_sym = msg.get("symbol", "").upper().strip()
                    if not _valid_symbol(raw_sym):
                        logger.warning(f"Invalid symbol from {username}: {raw_sym!r}")
                        continue

                    async with async_session() as db:
                        existing = await db.scalar(
                            select(UserWatchlist).where(
                                UserWatchlist.user_id == user_id,
                                UserWatchlist.symbol  == raw_sym,
                            )
                        )
                        if not existing:
                            db.add(UserWatchlist(user_id=user_id, symbol=raw_sym))
                            await db.commit()

                    # Update per-user Redis cache
                    try:
                        await client.sadd(_user_cache_key(user_id), raw_sym)
                        await client.expire(_user_cache_key(user_id), _USER_CACHE_TTL)
                    except Exception:
                        pass

                    # Add to global set + signal feed
                    await _add_to_global(raw_sym)
                    await client.publish(
                        "market:control",
                        json.dumps({"action": "add", "symbol": raw_sym}),
                    )
                    logger.info(f"{username} added {raw_sym} via WebSocket.")

                    # Confirm to this client immediately
                    async with async_session() as db:
                        result = await db.execute(
                            select(UserWatchlist.symbol)
                            .where(UserWatchlist.user_id == user_id)
                            .order_by(UserWatchlist.added_at.asc())
                        )
                        updated = [r[0] for r in result.all()]
                    await websocket.send_json({
                        "channel": "watchlist:sync",
                        "data":    updated,
                    })

                elif msg_type == "remove":
                    raw_sym = msg.get("symbol", "").upper().strip()
                    if not _valid_symbol(raw_sym):
                        logger.warning(f"Invalid remove symbol from {username}: {raw_sym!r}")
                        continue

                    async with async_session() as db:
                        from sqlalchemy import delete
                        await db.execute(
                            delete(UserWatchlist).where(
                                UserWatchlist.user_id == user_id,
                                UserWatchlist.symbol  == raw_sym,
                            )
                        )
                        await db.commit()

                        # Update per-user Redis cache
                        try:
                            await client.srem(_user_cache_key(user_id), raw_sym)
                        except Exception:
                            pass

                        # Prune global set if no user is left watching this symbol
                        await _remove_from_global_if_unused(raw_sym, db)

                        updated_result = await db.execute(
                            select(UserWatchlist.symbol)
                            .where(UserWatchlist.user_id == user_id)
                            .order_by(UserWatchlist.added_at.asc())
                        )
                        updated = [r[0] for r in updated_result.all()]

                    logger.info(f"{username} removed {raw_sym} via WebSocket.")
                    await websocket.send_json({
                        "channel": "watchlist:sync",
                        "data":    updated,
                    })

            except (json.JSONDecodeError, TypeError) as ex:
                logger.warning(f"Malformed packet from {username}: {ex}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected cleanly: {username}.")
    except Exception as e:
        logger.error(f"WebSocket receiver error for {username}: {e}")
    finally:
        await websocket_broadcaster.disconnect(websocket)
        logger.info(f"WebSocket cleaned up for {username}.")
