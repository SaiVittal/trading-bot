"""
User-specific watchlist management.

Architecture:
  • PostgreSQL (user_watchlists table) is the source of truth.
  • Redis provides two caches:
      - watchlist:user:{user_id}   → per-user symbol set  (TTL 24 h)
      - watchlist:global:symbols   → union of ALL users' symbols (no TTL)
        Used by the market feed to know which symbols to stream.
  • On add/remove the endpoint publishes to market:control so the feed
    subscribes / unsubscribes in real-time.
"""

import json
import logging
import re
from typing import Any, cast, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.config import settings
from app.core.redis_client import redis_client
from app.api.deps import get_current_active_user
from app.models.models import User, UserWatchlist

router = APIRouter()
logger = logging.getLogger("app.api.watchlist")

_SYMBOL_RE    = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_USER_CACHE_TTL = 86_400   # 24 h

# ── Helpers ────────────────────────────────────────────────────────


def _valid_symbol(raw: str) -> bool:
    return bool(raw) and bool(_SYMBOL_RE.match(raw))


def _redis() -> Any:
    return cast(Any, redis_client.client)


def _user_cache_key(user_id: UUID) -> str:
    return f"watchlist:user:{user_id}"


async def _rebuild_user_cache(user_id: UUID, symbols: List[str]) -> None:
    """Write (or refresh) the per-user Redis cache from a known symbol list."""
    client = _redis()
    if not client or not symbols:
        return
    key = _user_cache_key(user_id)
    try:
        await client.delete(key)
        await client.sadd(key, *symbols)
        await client.expire(key, _USER_CACHE_TTL)
    except Exception as e:
        logger.warning(f"Failed to rebuild user cache for {user_id}: {e}")


async def _add_to_global(symbol: str) -> None:
    client = _redis()
    if not client:
        return
    try:
        await client.sadd("watchlist:global:symbols", symbol)
    except Exception as e:
        logger.warning(f"Failed to add {symbol} to global set: {e}")


async def _remove_from_global_if_unused(symbol: str, db: AsyncSession) -> None:
    """
    Remove symbol from the global Redis set only when no other user
    still has it in their watchlist (prevents feed unsubscription while
    another user is actively watching that symbol).
    """
    client = _redis()
    if not client:
        return
    try:
        remaining = await db.scalar(
            select(func.count()).select_from(UserWatchlist).where(
                UserWatchlist.symbol == symbol
            )
        )
        if remaining == 0:
            await client.srem("watchlist:global:symbols", symbol)
            # Signal market feed to unsubscribe
            await client.publish(
                "market:control",
                json.dumps({"action": "remove", "symbol": symbol}),
            )
            logger.info(f"Global unsubscribed {symbol} — no users remaining.")
    except Exception as e:
        logger.warning(f"Failed to prune global set for {symbol}: {e}")


# ── Endpoints ──────────────────────────────────────────────────────


class WatchlistResponse(BaseModel):
    symbols: List[str]


class WatchlistItem(BaseModel):
    symbol:   str
    added_at: str


class WatchlistDetailResponse(BaseModel):
    items: List[WatchlistItem]


@router.get("", response_model=WatchlistDetailResponse)
async def get_watchlist(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> WatchlistDetailResponse:
    """Return the authenticated user's watchlist ordered by add time."""
    result = await db.execute(
        select(UserWatchlist)
        .where(UserWatchlist.user_id == current_user.id)
        .order_by(UserWatchlist.added_at.asc())
    )
    rows = result.scalars().all()

    # First-time users: seed default watchlist
    if not rows:
        items = await _seed_defaults(current_user, db)
        return WatchlistDetailResponse(items=items)

    return WatchlistDetailResponse(
        items=[
            WatchlistItem(symbol=r.symbol, added_at=r.added_at.isoformat())
            for r in rows
        ]
    )


@router.post("/{symbol}", status_code=status.HTTP_201_CREATED, response_model=WatchlistDetailResponse)
async def add_symbol(
    symbol: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> WatchlistDetailResponse:
    """Add a symbol to the user's watchlist (idempotent)."""
    sym = symbol.upper().strip()
    if not _valid_symbol(sym):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid symbol format: {symbol!r}",
        )

    # Idempotency check
    existing = await db.scalar(
        select(UserWatchlist).where(
            UserWatchlist.user_id == current_user.id,
            UserWatchlist.symbol  == sym,
        )
    )
    if not existing:
        db.add(UserWatchlist(user_id=current_user.id, symbol=sym))
        await db.commit()
        logger.info(f"User {current_user.username} added {sym} to watchlist.")

    # Update per-user Redis cache
    client = _redis()
    if client:
        try:
            key = _user_cache_key(current_user.id)
            await client.sadd(key, sym)
            await client.expire(key, _USER_CACHE_TTL)
        except Exception as e:
            logger.warning(f"Cache update failed for add {sym}: {e}")

    # Add to global set + signal market feed
    await _add_to_global(sym)
    if client:
        try:
            await client.publish(
                "market:control",
                json.dumps({"action": "add", "symbol": sym}),
            )
        except Exception as e:
            logger.warning(f"market:control publish failed for {sym}: {e}")

    return await get_watchlist(current_user=current_user, db=db)


@router.delete("/{symbol}", response_model=WatchlistDetailResponse)
async def remove_symbol(
    symbol: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> WatchlistDetailResponse:
    """Remove a symbol from the user's watchlist."""
    sym = symbol.upper().strip()
    if not _valid_symbol(sym):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid symbol format: {symbol!r}",
        )

    await db.execute(
        delete(UserWatchlist).where(
            UserWatchlist.user_id == current_user.id,
            UserWatchlist.symbol  == sym,
        )
    )
    await db.commit()
    logger.info(f"User {current_user.username} removed {sym} from watchlist.")

    # Update per-user Redis cache
    client = _redis()
    if client:
        try:
            await client.srem(_user_cache_key(current_user.id), sym)
        except Exception as e:
            logger.warning(f"Cache update failed for remove {sym}: {e}")

    # Prune global set if no other user is watching this symbol
    await _remove_from_global_if_unused(sym, db)

    return await get_watchlist(current_user=current_user, db=db)


@router.post("/reset", response_model=WatchlistDetailResponse)
async def reset_to_defaults(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> WatchlistDetailResponse:
    """Clear user's watchlist and restore the platform defaults."""
    # Get current symbols before deleting to clean up global set
    result = await db.execute(
        select(UserWatchlist).where(UserWatchlist.user_id == current_user.id)
    )
    old_symbols = [r.symbol for r in result.scalars().all()]

    await db.execute(
        delete(UserWatchlist).where(UserWatchlist.user_id == current_user.id)
    )
    await db.commit()

    # Seed defaults
    items = await _seed_defaults(current_user, db)

    # Prune any removed symbols from global set
    new_syms = {i.symbol for i in items}
    client   = _redis()
    for sym in old_symbols:
        if sym not in new_syms:
            await _remove_from_global_if_unused(sym, db)
        if client:
            try:
                await client.sadd("watchlist:global:symbols", sym)
                await client.publish(
                    "market:control",
                    json.dumps({"action": "add", "symbol": sym}),
                )
            except Exception:
                pass

    return WatchlistDetailResponse(items=items)


# ── Internal helpers ────────────────────────────────────────────────


async def _seed_defaults(user: User, db: AsyncSession) -> List[WatchlistItem]:
    """Insert default symbols for a user (called on first login / reset)."""
    for sym in settings.DEFAULT_WATCHLIST_SYMBOLS:
        existing = await db.scalar(
            select(UserWatchlist).where(
                UserWatchlist.user_id == user.id,
                UserWatchlist.symbol  == sym,
            )
        )
        if not existing:
            db.add(UserWatchlist(user_id=user.id, symbol=sym))

    await db.commit()
    logger.info(f"Seeded default watchlist for user {user.username}.")

    # Warm caches
    await _rebuild_user_cache(user.id, settings.DEFAULT_WATCHLIST_SYMBOLS)
    client = _redis()
    if client:
        try:
            await client.sadd("watchlist:global:symbols", *settings.DEFAULT_WATCHLIST_SYMBOLS)
            for sym in settings.DEFAULT_WATCHLIST_SYMBOLS:
                await client.publish(
                    "market:control",
                    json.dumps({"action": "add", "symbol": sym}),
                )
        except Exception as e:
            logger.warning(f"Global cache seed failed: {e}")

    result = await db.execute(
        select(UserWatchlist)
        .where(UserWatchlist.user_id == user.id)
        .order_by(UserWatchlist.added_at.asc())
    )
    return [
        WatchlistItem(symbol=r.symbol, added_at=r.added_at.isoformat())
        for r in result.scalars().all()
    ]


async def get_user_symbols_cached(user_id: UUID, db: AsyncSession) -> List[str]:
    """
    Fast path: read from Redis cache.
    Slow path: query DB and rebuild cache.
    Used by the WebSocket endpoint on client connect.
    """
    client = _redis()
    if client:
        try:
            cached = await client.smembers(_user_cache_key(user_id))
            if cached:
                return list(cached)
        except Exception as e:
            logger.warning(f"Cache read failed for {user_id}: {e}")

    # DB fallback
    result = await db.execute(
        select(UserWatchlist.symbol).where(UserWatchlist.user_id == user_id)
    )
    symbols = [row[0] for row in result.all()]

    if symbols:
        await _rebuild_user_cache(user_id, symbols)
    else:
        # Brand-new user — they'll be seeded on first GET /watchlist call
        symbols = settings.DEFAULT_WATCHLIST_SYMBOLS

    return symbols
