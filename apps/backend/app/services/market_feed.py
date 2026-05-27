import asyncio
import json
import math
import random
import time
import logging
import websockets
from typing import Set, Dict, Optional
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


class MarketFeedManager:
    def __init__(self) -> None:
        # Dynamic active set of symbols to scrape and tick
        self.watchlist: Set[str] = {
            "TSLA", "NBIS", "COST", "SPX", "APPLOVIN"
        }

    # ── Polygon real-time feed ──────────────────────────────────────

    async def run_polygon_feed(self) -> None:
        """Connect to Polygon.io real-time Stocks WebSocket and stream trades."""
        url = "wss://socket.polygon.io/stocks"
        logger.info(f"Connecting to Polygon.io Stocks WebSocket: {url}")

        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            greeting = await ws.recv()
            logger.info(f"Polygon greeting: {greeting}")

            await ws.send(json.dumps({
                "action": "auth",
                "params": settings.POLYGON_API_KEY,
            }))
            auth_status = await ws.recv()
            logger.info(f"Polygon auth: {auth_status}")

            status_data = json.loads(auth_status)
            if status_data and status_data[0].get("status") != "auth_success":
                raise ValueError(f"Polygon auth rejected: {auth_status}")

            await ws.send(json.dumps({
                "action": "subscribe",
                "params": ",".join(f"T.{sym}" for sym in self.watchlist),
            }))
            logger.info(f"Subscribed to trades: {list(self.watchlist)}")

            # Background task: sync dynamic UI watchlist additions
            async def sync_watchlist():
                client = redis_client.client
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
                            if payload.get("action") == "add" and sym and sym not in self.watchlist:
                                self.watchlist.add(sym)
                                await ws.send(json.dumps({"action": "subscribe", "params": f"T.{sym}"}))
                                logger.info(f"Dynamically subscribed to {sym}")
                    except (asyncio.CancelledError, Exception):
                        pass

            sync_task = asyncio.create_task(sync_watchlist())
            try:
                while True:
                    raw = await ws.recv()
                    for item in json.loads(raw):
                        if item.get("ev") == "T":
                            await self._publish(item["sym"], float(item["p"]), int(item["s"]))
            finally:
                sync_task.cancel()

    # ── Simulation fallback ────────────────────────────────────────

    async def run_simulation(self) -> None:
        """
        Realistic market simulation used when Alpaca is unavailable.
        Generates 5–15 ticks/second per symbol with GBM-style price walk,
        intraday volume curve, and occasional volume spikes to trigger
        the strategy engine and fire real Telegram alerts.
        """
        logger.warning(
            "Alpaca feed unavailable — running SIMULATION MODE. "
            "Prices are synthetic but all strategies and Telegram alerts are live."
        )

        prices: Dict[str, float] = {}
        for sym in self.watchlist:
            prices[sym] = _SEED_PRICES.get(sym, 200.0)

        # Control channel listener (supports dynamic watchlist from UI)
        async def sync_watchlist_sim():
            client = redis_client.client
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
                        if payload.get("action") == "add" and sym and sym not in self.watchlist:
                            self.watchlist.add(sym)
                            prices[sym] = _SEED_PRICES.get(sym, 200.0)
                            logger.info(f"Simulation: added {sym} to watchlist")
                except (asyncio.CancelledError, Exception):
                    pass

        sync_task = asyncio.create_task(sync_watchlist_sim())
        tick_interval = 0.3   # ~3 ticks/second per symbol

        try:
            while True:
                now_ts = time.time()
                for sym in list(self.watchlist):
                    px = prices.get(sym, _SEED_PRICES.get(sym, 200.0))

                    # GBM-style price step: drift 0, vol ~0.05% per tick
                    vol_per_tick = px * 0.0005
                    px += random.gauss(0, vol_per_tick)
                    # Mild mean-reversion toward seed price
                    seed = _SEED_PRICES.get(sym, px)
                    px += (seed - px) * 0.0002
                    px = max(px * 0.98, min(px * 1.02, px))  # clamp extreme swings
                    prices[sym] = round(px, 2)

                    # Volume: log-normal, occasional spikes (simulate RVOL)
                    base_vol = random.randint(50, 500)
                    if random.random() < 0.02:      # 2% chance of volume spike
                        base_vol = random.randint(2000, 8000)

                    await self._publish(sym, prices[sym], base_vol, now_ts)

                await asyncio.sleep(tick_interval)
        finally:
            sync_task.cancel()

    # ── Shared publish helper ──────────────────────────────────────

    async def _publish(self, symbol: str, price: float,
                       volume: int, ts: Optional[float] = None) -> None:
        if redis_client.client:
            await redis_client.client.publish(
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
        has_keys = bool(
            settings.POLYGON_API_KEY
            and settings.POLYGON_API_KEY not in ("", "your_polygon_api_key_here")
        )

        if not has_keys:
            logger.warning("No Polygon API keys configured — using simulation.")
            await self.run_simulation()
            return

        polygon_failures = 0
        while True:
            try:
                await self.run_polygon_feed()
                polygon_failures = 0
            except Exception as e:
                polygon_failures += 1
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
                await asyncio.sleep(backoff)


async def start_market_feed_simulation():
    manager = MarketFeedManager()
    await manager.run()
