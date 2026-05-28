import asyncio
import json
import random
import time
import logging
import os
from datetime import datetime, timezone
from typing import Set, Dict, Optional, Any, cast

import websockets
from websockets.exceptions import ConnectionClosed
import pytz

from app.core.config import settings
from app.core.redis_client import redis_client

logger = logging.getLogger("app.services.market_feed")

REDIS_TICK_CHANNEL    = "market:ticks"
REDIS_CONTROL_CHANNEL = "market:control"

# Alpaca streaming endpoint — SIP (paid, all US exchanges) or IEX (free)
_ALPACA_WS_URL = "wss://stream.data.alpaca.markets/v2/{feed}"

# Realistic seed prices for simulation fallback
_SEED_PRICES: Dict[str, float] = {
    "TSLA": 418.50, "AAPL": 213.20, "NVDA": 137.80,
    "SPY":  584.30, "MSFT": 472.10, "AMZN": 223.40,
    "GOOGL":183.90, "META": 692.50, "AMD":  167.30,
}


def is_market_hours() -> bool:
    """Return True if standard US equity market hours (9:30–16:00 ET) are active."""
    try:
        et  = pytz.timezone("America/New_York")
        now = datetime.now(et)
        if now.weekday() >= 5:
            return False
        start = now.replace(hour=9,  minute=30, second=0, microsecond=0)
        end   = now.replace(hour=16, minute=0,  second=0, microsecond=0)
        return start <= now <= end
    except Exception as e:
        logger.warning(f"Error checking market hours: {e}. Defaulting to True.")
        return True


def _parse_alpaca_ts(ts_str: str) -> float:
    """
    Convert Alpaca's RFC-3339 / ISO-8601 trade timestamp to a Unix float.
    Alpaca sends nanosecond precision: "2024-01-02T14:30:00.123456789Z"
    Python's fromisoformat handles up to microseconds, so we truncate nanos.
    """
    try:
        # Truncate sub-microsecond digits (nanoseconds → microseconds)
        # e.g. "2024-01-02T14:30:00.123456789Z" → "2024-01-02T14:30:00.123456Z"
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        # Strip sub-microsecond part if present (more than 6 decimal digits)
        if "." in ts_str:
            dot_pos = ts_str.index(".")
            plus_pos = ts_str.find("+", dot_pos)
            frac = ts_str[dot_pos + 1 : plus_pos if plus_pos > 0 else len(ts_str)]
            if len(frac) > 6:
                ts_str = ts_str[: dot_pos + 7] + ts_str[plus_pos if plus_pos > 0 else len(ts_str):]
        return datetime.fromisoformat(ts_str).timestamp()
    except Exception:
        return time.time()


class MarketFeedManager:
    def __init__(self) -> None:
        self.watchlist: Set[str] = set(settings.DEFAULT_WATCHLIST_SYMBOLS)
        self.last_message_time: float = time.time()

        env = os.getenv("APP_ENV") or settings.ENV
        self.is_production: bool = (env.lower() == "production")
        logger.info(
            f"MarketFeedManager initialized. Mode: "
            f"{'PRODUCTION' if self.is_production else 'DEVELOPMENT'}"
        )

    @property
    def redis(self) -> Any:
        return cast(Any, redis_client.client)

    async def _update_status(self, status: str, error: Optional[str] = None) -> None:
        payload = {
            "status": status,
            "feed":   f"alpaca-{settings.ALPACA_FEED}" if settings.ALPACA_API_KEY else "simulation",
            "error":  error,
            "timestamp": time.time(),
            # Publish thresholds so frontend can sync without hardcoding
            "stale_threshold_market_hours_ms": int(settings.FEED_STALE_MARKET_HOURS_SECS * 1000),
            "stale_threshold_off_hours_ms":    int(settings.FEED_STALE_OFF_HOURS_SECS    * 1000),
        }
        logger.info(
            f"Market Feed status → {status.upper()} "
            f"(Feed: {payload['feed']})"
        )
        if self.redis:
            try:
                payload_str = json.dumps(payload)
                await self.redis.publish("market:status", payload_str)
                await self.redis.set("market:status:last", payload_str)
            except Exception as e:
                logger.error(f"Failed to publish feed status to Redis: {e}")

    # ── Alpaca real-time feed ───────────────────────────────────────

    async def run_alpaca_feed(self) -> None:
        """
        Connect to Alpaca's real-time stocks WebSocket, authenticate,
        subscribe to trades for all watchlist symbols, and stream ticks.

        Protocol:
          1. Connect → server sends [{"T":"success","msg":"connected"}]
          2. Send auth  → server sends [{"T":"success","msg":"authenticated"}]
          3. Send subscribe(trades=[...]) → server confirms subscription
          4. Receive trade events: {"T":"t","S":"TSLA","p":418.5,"s":100,"t":"..."}
        """
        feed = (settings.ALPACA_FEED or "sip").lower()
        url  = _ALPACA_WS_URL.format(feed=feed)
        logger.info(f"Connecting to Alpaca Stocks WebSocket: {url}")
        await self._update_status("connecting")

        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            additional_headers={
                "APCA-API-KEY-ID":     settings.ALPACA_API_KEY or "",
                "APCA-API-SECRET-KEY": settings.ALPACA_API_SECRET or "",
            },
        ) as ws:
            self.last_message_time = time.time()

            # ── 1. Connected confirmation ──────────────────────────
            raw = await ws.recv()
            msgs = json.loads(raw)
            logger.info(f"Alpaca connected: {msgs}")

            # ── 2. Authenticate ────────────────────────────────────
            await ws.send(json.dumps({
                "action": "auth",
                "key":    settings.ALPACA_API_KEY,
                "secret": settings.ALPACA_API_SECRET,
            }))
            raw = await ws.recv()
            auth_msgs = json.loads(raw)
            logger.info(f"Alpaca auth response: {auth_msgs}")

            # Verify auth success
            auth_ok = any(
                m.get("T") == "success" and m.get("msg") == "authenticated"
                for m in auth_msgs
            )
            if not auth_ok:
                await self._update_status("disconnected", f"Auth rejected: {auth_msgs}")
                raise ValueError(f"Alpaca auth rejected: {auth_msgs}")

            await self._update_status("connected")

            # ── 3. Subscribe to trades for all watchlist symbols ───
            await ws.send(json.dumps({
                "action": "subscribe",
                "trades": list(self.watchlist),
            }))
            logger.info(f"Subscribed to trades: {list(self.watchlist)}")

            # Background: sync dynamic watchlist add/remove via Redis pub/sub
            async def sync_watchlist() -> None:
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
                            sym    = payload.get("symbol", "").upper().strip()
                            action = payload.get("action")
                            if action == "add" and sym and sym not in self.watchlist:
                                self.watchlist.add(sym)
                                if self.redis:
                                    await self.redis.sadd("watchlist:global:symbols", sym)
                                await ws.send(json.dumps({
                                    "action": "subscribe",
                                    "trades": [sym],
                                }))
                                logger.info(f"Dynamically subscribed to {sym}")
                            elif action == "remove" and sym and sym in self.watchlist:
                                self.watchlist.discard(sym)
                                if self.redis:
                                    await self.redis.srem("watchlist:global:symbols", sym)
                                await ws.send(json.dumps({
                                    "action": "unsubscribe",
                                    "trades": [sym],
                                }))
                                logger.info(f"Dynamically unsubscribed from {sym}")
                    except (asyncio.CancelledError, Exception):
                        pass

            # Background: watchdog for connection staleness
            async def connection_watchdog() -> None:
                try:
                    while True:
                        await asyncio.sleep(10.0)
                        is_active = is_market_hours()
                        threshold = (
                            settings.FEED_STALE_MARKET_HOURS_SECS
                            if is_active
                            else settings.FEED_STALE_OFF_HOURS_SECS
                        )
                        elapsed   = time.time() - self.last_message_time
                        if elapsed > threshold:
                            logger.warning(
                                f"Watchdog: no data in {elapsed:.1f}s "
                                f"(threshold {threshold}s). Forcing reset."
                            )
                            await self._update_status(
                                "stale", f"No data in {elapsed:.1f}s"
                            )
                            await ws.close()
                            break
                except asyncio.CancelledError:
                    pass
                except Exception as ex:
                    logger.error(f"Watchdog error: {ex}")

            sync_task     = asyncio.create_task(sync_watchlist())
            watchdog_task = asyncio.create_task(connection_watchdog())

            try:
                while True:
                    raw = await ws.recv()
                    self.last_message_time = time.time()
                    for item in json.loads(raw):
                        msg_type = item.get("T")
                        if msg_type == "t":
                            # Trade event
                            # Fields: S=symbol, p=price, s=size, t=timestamp(ISO8601)
                            sym   = item.get("S", "")
                            price = item.get("p")
                            size  = item.get("s", 0)
                            ts    = _parse_alpaca_ts(item["t"]) if "t" in item else time.time()
                            if sym and price is not None:
                                await self._publish(sym, float(price), int(size), ts)
                        elif msg_type == "error":
                            logger.error(
                                f"Alpaca stream error: code={item.get('code')} "
                                f"msg={item.get('msg')}"
                            )
                        elif msg_type in ("subscription", "success"):
                            logger.debug(f"Alpaca control msg: {item}")
            finally:
                sync_task.cancel()
                watchdog_task.cancel()

    # ── Simulation fallback ─────────────────────────────────────────

    async def run_simulation(self) -> None:
        """
        Realistic price-walk simulation used in development when Alpaca is
        not configured. Synthetic ticks, but all strategy + Telegram logic is live.
        """
        if self.is_production:
            logger.critical(
                "SIMULATION FALLBACK CALLED IN PRODUCTION. "
                "Blocking to prevent synthetic alerts."
            )
            await self._update_status(
                "disconnected", "Simulation mode disabled in production."
            )
            while True:
                await asyncio.sleep(3600.0)

        logger.warning(
            "Alpaca feed unavailable — running SIMULATION MODE. "
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

        prices: Dict[str, float] = {sym: get_seed_price(sym) for sym in self.watchlist}

        async def sync_watchlist_sim() -> None:
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
                        sym    = payload.get("symbol", "").upper().strip()
                        action = payload.get("action")
                        if action == "add" and sym and sym not in self.watchlist:
                            self.watchlist.add(sym)
                            if self.redis:
                                await self.redis.sadd("watchlist:global:symbols", sym)
                            prices[sym] = get_seed_price(sym)
                            logger.info(
                                f"Simulation: added {sym} @ ${prices[sym]}"
                            )
                        elif action == "remove" and sym and sym in self.watchlist:
                            self.watchlist.discard(sym)
                            if self.redis:
                                await self.redis.srem("watchlist:global:symbols", sym)
                            prices.pop(sym, None)
                            logger.info(f"Simulation: removed {sym}")
                except (asyncio.CancelledError, Exception):
                    pass

        sync_task    = asyncio.create_task(sync_watchlist_sim())
        tick_interval = 0.3

        try:
            while True:
                now_ts = time.time()
                for sym in list(self.watchlist):
                    px   = prices.get(sym, get_seed_price(sym))
                    seed = get_seed_price(sym)
                    px  += random.gauss(0, px * 0.0005)
                    px  += (seed - px) * 0.0002
                    px   = max(px * 0.98, min(px * 1.02, px))
                    prices[sym] = round(px, 2)

                    vol = random.randint(50, 500)
                    if random.random() < 0.02:
                        vol = random.randint(2000, 8000)

                    await self._publish(sym, prices[sym], vol, now_ts)

                await asyncio.sleep(tick_interval)
        finally:
            sync_task.cancel()

    # ── Shared publish helper ───────────────────────────────────────

    async def _publish(
        self, symbol: str, price: float, volume: int, ts: Optional[float] = None
    ) -> None:
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

    # ── Main run loop ───────────────────────────────────────────────

    async def run(self) -> None:
        # Restore persistent watchlist from Redis
        if self.redis:
            try:
                stored = await self.redis.smembers("watchlist:global:symbols")
                if stored:
                    self.watchlist = {s for s in stored if s}
                    logger.info(
                        f"Loaded persistent watchlist from Redis: {list(self.watchlist)}"
                    )
                else:
                    await self.redis.sadd("watchlist:global:symbols", *self.watchlist)
                    logger.info(
                        f"Seeded default watchlist to Redis: {list(self.watchlist)}"
                    )
            except Exception as e:
                logger.error(f"Failed to load watchlist from Redis: {e}")

        has_keys = bool(
            settings.ALPACA_API_KEY
            and settings.ALPACA_API_SECRET
            and settings.ALPACA_API_KEY not in ("", "your_alpaca_api_key_here")
        )

        if not has_keys:
            if self.is_production:
                logger.critical(
                    "MISSING ALPACA API CREDENTIALS IN PRODUCTION. "
                    "Real-time feed disabled."
                )
                await self._update_status(
                    "disconnected", "Missing Alpaca API credentials."
                )
                return
            else:
                logger.warning(
                    "No Alpaca API credentials configured — using simulation fallback."
                )
                await self.run_simulation()
                return

        feed_failures = 0
        while True:
            try:
                await self.run_alpaca_feed()
                # Clean watchdog-triggered exit: reconnect immediately
                feed_failures = 0
                logger.info("Alpaca feed exited cleanly. Reconnecting...")
            except ConnectionClosed as e:
                feed_failures = 0
                logger.warning(
                    f"Alpaca WebSocket closed "
                    f"(code={e.rcvd.code if e.rcvd else 'N/A'}). "
                    "Reconnecting immediately."
                )
                await self._update_status(
                    "reconnecting", "Connection reset — reconnecting."
                )
            except Exception as e:
                feed_failures += 1

                if self.is_production:
                    backoff = min(2 ** feed_failures, 60)
                    logger.error(
                        f"Alpaca feed failed in production (attempt {feed_failures}). "
                        f"Retrying in {backoff}s. Error: {e}"
                    )
                    await self._update_status(
                        "reconnecting",
                        f"Connection failed (attempt {feed_failures}): {e}",
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        f"Alpaca feed failed (attempt {feed_failures}): {e}"
                    )
                    if feed_failures >= 3:
                        logger.warning(
                            "Alpaca failed 3 times — switching to simulation mode."
                        )
                        await self.run_simulation()
                        return

                    backoff = min(5 * feed_failures, 30)
                    await self._update_status(
                        "reconnecting",
                        f"Alpaca feed offline. Retry in {backoff}s.",
                    )
                    await asyncio.sleep(backoff)


async def start_market_feed_simulation() -> None:
    manager = MarketFeedManager()
    await manager.run()
