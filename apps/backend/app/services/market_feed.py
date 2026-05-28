import asyncio
import json
import random
import time
import logging
import os
from typing import Set, Dict, Optional, Any, cast
import pytz
from datetime import datetime

import websockets
from websockets.exceptions import ConnectionClosed
from app.core.config import settings
from app.core.redis_client import redis_client

logger = logging.getLogger("app.services.market_feed")

REDIS_TICK_CHANNEL   = "market:ticks"
REDIS_CONTROL_CHANNEL = "market:control"

# Realistic seed prices for simulation fallback
_SEED_PRICES: Dict[str, float] = {
    "TSLA": 418.50, "AAPL": 213.20, "NVDA": 137.80,
    "SPY":  584.30, "MSFT": 472.10, "AMZN": 223.40,
    "GOOGL":183.90, "META": 692.50, "AMD":  167.30,
}


def is_market_hours() -> bool:
    """Helper to detect if standard U.S. stock market hours (9:30 AM - 4:00 PM ET) are active."""
    try:
        et = pytz.timezone("America/New_York")
        now = datetime.now(et)
        if now.weekday() >= 5:  # Saturday or Sunday
            return False
        start_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
        end_time = now.replace(hour=16, minute=0, second=0, microsecond=0)
        return start_time <= now <= end_time
    except Exception as e:
        logger.warning(f"Error checking market hours: {e}. Defaulting to True.")
        return True


class MarketFeedManager:
    def __init__(self) -> None:
        # Dynamic active set of symbols to scrape and tick
        self.watchlist: Set[str] = {
            "TSLA", "NBIS", "COST", "SPX", "APPLOVIN"
        }
        self.last_message_time: float = time.time()
        
        # Check environment flag for production mode
        env = os.getenv("APP_ENV") or settings.ENV
        self.is_production: bool = (env.lower() == "production")
        logger.info(f"MarketFeedManager initialized. Mode: {'PRODUCTION' if self.is_production else 'DEVELOPMENT'}")

    @property
    def redis(self) -> Any:
        """Returns the Redis client casted to Any to resolve Pyright type-stub mismatches."""
        return cast(Any, redis_client.client)

    async def _update_status(self, status: str, error: Optional[str] = None) -> None:
        """Helper to publish feed status changes to Redis pub/sub and cache the state."""
        payload = {
            "status": status,
            "feed": "polygon" if settings.POLYGON_API_KEY else "simulation",
            "error": error,
            "timestamp": time.time()
        }
        logger.info(f"Market Feed status transition -> {status.upper()} (Feed Source: {payload['feed']})")
        if self.redis:
            try:
                payload_str = json.dumps(payload)
                await self.redis.publish("market:status", payload_str)
                await self.redis.set("market:status:last", payload_str)
            except Exception as e:
                logger.error(f"Failed to publish feed status to Redis: {e}")

    # ── Polygon real-time feed ──────────────────────────────────────

    async def run_polygon_feed(self) -> None:
        """Connect to Polygon.io real-time Stocks WebSocket, authenticate and stream trades."""
        url = "wss://socket.polygon.io/stocks"
        logger.info(f"Connecting to Polygon.io Stocks WebSocket: {url}")
        await self._update_status("connecting")

        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            self.last_message_time = time.time()
            greeting = await ws.recv()
            logger.info(f"Polygon greeting: {greeting}")

            await ws.send(json.dumps({
                "action": "auth",
                "params": settings.POLYGON_API_KEY,
            }))
            auth_status = await ws.recv()
            logger.info(f"Polygon auth response: {auth_status}")

            status_data = json.loads(auth_status)
            if status_data and status_data[0].get("status") != "auth_success":
                await self._update_status("disconnected", f"Auth rejected: {auth_status}")
                raise ValueError(f"Polygon auth rejected: {auth_status}")

            # Mark connected and authenticated
            await self._update_status("connected")

            # Recover subscriptions (re-subscribe to all symbols in the watchlist)
            await ws.send(json.dumps({
                "action": "subscribe",
                "params": ",".join(f"T.{sym}" for sym in self.watchlist),
            }))
            logger.info(f"Subscribed to trades: {list(self.watchlist)}")

            # Background task: sync dynamic UI watchlist additions and removals
            async def sync_watchlist():
                client = self.redis
                if not client:
                    return
                async with client.pubsub() as pubsub:
                    await pubsub.subscribe(REDIS_CONTROL_CHANNEL)
                    try:
                        async for msg in pubsub.listen():
                            if msg["type"] != "message":
                                continue
                            payload = json.loads(msg["data"])
                            sym = payload.get("symbol", "").upper().strip()
                            if payload.get("action") == "add" and sym:
                                if sym not in self.watchlist:
                                    self.watchlist.add(sym)
                                    if self.redis:
                                        await self.redis.sadd("watchlist:symbols", sym)
                                    await ws.send(json.dumps({"action": "subscribe", "params": f"T.{sym}"}))
                                    logger.info(f"Dynamically subscribed to {sym}")
                            elif payload.get("action") == "remove" and sym:
                                if sym in self.watchlist:
                                    self.watchlist.discard(sym)
                                    if self.redis:
                                        await self.redis.srem("watchlist:symbols", sym)
                                    await ws.send(json.dumps({"action": "unsubscribe", "params": f"T.{sym}"}))
                                    logger.info(f"Dynamically unsubscribed from {sym}")
                    except (asyncio.CancelledError, Exception):
                        pass

            # Background task: watchdog for connection staleness
            async def connection_watchdog():
                try:
                    while True:
                        await asyncio.sleep(10.0)
                        
                        # Active trading hours: tight timeout (45s)
                        # Off-market hours: loose timeout (30 min) to avoid disconnect storms when there's no volume
                        is_active = is_market_hours()
                        threshold = 45.0 if is_active else 1800.0
                        
                        elapsed = time.time() - self.last_message_time
                        if elapsed > threshold:
                            logger.warning(
                                f"Watchdog alert: No data received in {elapsed:.1f}s (Threshold: {threshold}s). "
                                "Forcing connection reset."
                            )
                            await self._update_status("stale", f"No data received in {elapsed:.1f}s")
                            await ws.close()
                            break
                except asyncio.CancelledError:
                    pass
                except Exception as ex:
                    logger.error(f"Error in connection watchdog: {ex}")

            sync_task = asyncio.create_task(sync_watchlist())
            watchdog_task = asyncio.create_task(connection_watchdog())
            
            try:
                while True:
                    raw = await ws.recv()
                    self.last_message_time = time.time()
                    for item in json.loads(raw):
                        if item.get("ev") == "T":
                            # Use Polygon's nanosecond trade timestamp (field "t", in ms) for accurate ordering.
                            # Falls back to server time only if missing, which should never happen on T events.
                            poly_ts = item.get("t")
                            ts = poly_ts / 1000.0 if poly_ts else time.time()
                            await self._publish(item["sym"], float(item["p"]), int(item["s"]), ts)
            finally:
                sync_task.cancel()
                watchdog_task.cancel()

    # ── Simulation fallback ────────────────────────────────────────

    async def run_simulation(self) -> None:
        """
        Realistic market simulation used when Polygon is unavailable in development.
        Generates ticks per symbol with a random price walk.
        """
        if self.is_production:
            logger.critical("SIMULATION FALLBACK CALLED IN PRODUCTION. Blocking execution to prevent synthetic alerts.")
            await self._update_status("disconnected", "Simulation mode disabled in production.")
            # Sleep indefinitely in production to avoid eating CPU cycles while offline
            while True:
                await asyncio.sleep(3600.0)

        logger.warning(
            "Polygon feed unavailable — running SIMULATION MODE. "
            "Prices are synthetic but all strategies and Telegram alerts are live."
        )
        await self._update_status("connected")

        from app.services.price_fix import PRICE_RANGES

        def get_seed_price(sym: str) -> float:
            if sym in _SEED_PRICES:
                return _SEED_PRICES[sym]
            if sym in PRICE_RANGES:
                return round((PRICE_RANGES[sym][0] + PRICE_RANGES[sym][1]) / 2, 2)
            return 200.0

        prices: Dict[str, float] = {}
        for sym in self.watchlist:
            prices[sym] = get_seed_price(sym)

        async def sync_watchlist_sim():
            client = self.redis
            if not client:
                return
            async with client.pubsub() as pubsub:
                await pubsub.subscribe(REDIS_CONTROL_CHANNEL)
                try:
                    async for msg in pubsub.listen():
                        if msg["type"] != "message":
                            continue
                        payload = json.loads(msg["data"])
                        sym = payload.get("symbol", "").upper().strip()
                        if payload.get("action") == "add" and sym:
                            if sym not in self.watchlist:
                                self.watchlist.add(sym)
                                if self.redis:
                                    await self.redis.sadd("watchlist:symbols", sym)
                                prices[sym] = get_seed_price(sym)
                                logger.info(f"Simulation: added {sym} to watchlist with price ${prices[sym]}")
                        elif payload.get("action") == "remove" and sym:
                            if sym in self.watchlist:
                                self.watchlist.discard(sym)
                                if self.redis:
                                    await self.redis.srem("watchlist:symbols", sym)
                                prices.pop(sym, None)
                                logger.info(f"Simulation: removed {sym} from watchlist")
                except (asyncio.CancelledError, Exception):
                    pass

        sync_task = asyncio.create_task(sync_watchlist_sim())
        tick_interval = 0.3

        try:
            while True:
                now_ts = time.time()
                for sym in list(self.watchlist):
                    px = prices.get(sym, _SEED_PRICES.get(sym, 200.0))

                    vol_per_tick = px * 0.0005
                    px += random.gauss(0, vol_per_tick)
                    seed = get_seed_price(sym)
                    px += (seed - px) * 0.0002
                    px = max(px * 0.98, min(px * 1.02, px))
                    prices[sym] = round(px, 2)

                    base_vol = random.randint(50, 500)
                    if random.random() < 0.02:
                        base_vol = random.randint(2000, 8000)

                    await self._publish(sym, prices[sym], base_vol, now_ts)

                await asyncio.sleep(tick_interval)
        finally:
            sync_task.cancel()

    # ── Shared publish helper ──────────────────────────────────────

    async def _publish(self, symbol: str, price: float,
                       volume: int, ts: Optional[float] = None) -> None:
        if self.redis:
            await self.redis.publish(
                REDIS_TICK_CHANNEL,
                json.dumps({
                    "symbol":    symbol,
                    "price":     price,
                    "volume":    volume,
                    "timestamp": ts or time.time(),
                }),
            )

    # ── Main run loop ──────────────────────────────────────────────

    async def run(self) -> None:
        if self.redis:
            try:
                stored = await self.redis.smembers("watchlist:symbols")
                if stored:
                    self.watchlist = {s for s in stored if s}
                    logger.info(f"Loaded persistent watchlist from Redis: {list(self.watchlist)}")
                else:
                    await self.redis.sadd("watchlist:symbols", *self.watchlist)
                    logger.info(f"Seeded default watchlist to Redis: {list(self.watchlist)}")
            except Exception as e:
                logger.error(f"Failed to load watchlist from Redis: {e}")

        has_keys = bool(
            settings.POLYGON_API_KEY
            and settings.POLYGON_API_KEY not in ("", "your_polygon_api_key_here")
        )

        if not has_keys:
            if self.is_production:
                logger.critical("MISSING POLYGON API KEY IN PRODUCTION ENVIRONMENT. Real-time feed disabled.")
                await self._update_status("disconnected", "Missing Polygon API key.")
                return
            else:
                logger.warning("No Polygon API keys configured — using simulation fallback.")
                await self.run_simulation()
                return

        polygon_failures = 0
        while True:
            try:
                await self.run_polygon_feed()
                # Clean exit (e.g. watchdog closed the connection intentionally): reset and reconnect immediately
                polygon_failures = 0
                logger.info("Polygon feed exited cleanly (watchdog reset). Reconnecting...")
            except ConnectionClosed as e:
                # ConnectionClosed from the watchdog or a server-side close — treat as a planned reconnect,
                # not a hard failure, so the failure counter doesn't increment and cause excessive backoff.
                polygon_failures = 0
                logger.warning(f"Polygon WebSocket closed (code={e.rcvd.code if e.rcvd else 'N/A'}). Reconnecting immediately.")
                await self._update_status("reconnecting", "Connection reset — reconnecting.")
            except Exception as e:
                polygon_failures += 1

                # In production, NEVER drop into simulation mode. Keep trying to reconnect forever.
                if self.is_production:
                    backoff = min(2 ** polygon_failures, 60)
                    logger.error(
                        f"Polygon feed failed in production (attempt {polygon_failures}). "
                        f"Retrying connection in {backoff} seconds... Error: {e}"
                    )
                    await self._update_status("reconnecting", f"Connection failed (attempt {polygon_failures}): {e}")
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        f"Polygon feed failed (attempt {polygon_failures}): {e}"
                    )
                    if polygon_failures >= 3:
                        logger.warning(
                            "Polygon failed 3 times — switching to simulation mode permanently."
                        )
                        await self.run_simulation()
                        return

                    backoff = min(5 * polygon_failures, 30)
                    await self._update_status("reconnecting", f"Polygon feed offline. Retry in {backoff}s.")
                    await asyncio.sleep(backoff)


async def start_market_feed_simulation():
    manager = MarketFeedManager()
    await manager.run()
