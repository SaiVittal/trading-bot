import asyncio
import json
import time
import logging
import httpx
import random
import websockets
from typing import Dict, Set
from app.core.config import settings
from app.core.redis_client import redis_client

logger = logging.getLogger("app.services.market_feed")

# Redis Channels
REDIS_TICK_CHANNEL = "market:ticks"
REDIS_CONTROL_CHANNEL = "market:control"

TICK_INTERVAL_SEC = 0.200  # 200ms ticks
FETCH_INTERVAL_SEC = 2.0   # Update baseline from Yahoo Finance every 2s

DEFAULT_FALLBACKS = {
    "TSLA": 412.68,
    "AAPL": 189.84,
    "NVDA": 942.50,
    "SPY": 530.10,
    "MSFT": 420.30,
    "QQQ": 451.20,
    "AMZN": 182.40,
    "META": 472.90
}


class MarketFeedManager:
    def __init__(self) -> None:
        # Dynamic active set of symbols to scrape and tick
        self.watchlist: Set[str] = {"TSLA", "AAPL", "NVDA", "SPY", "MSFT"}
        
        # Baselines and ticking prices (for Yahoo Finance Fallback)
        self.baselines: Dict[str, float] = {}
        self.current_prices: Dict[str, float] = {}
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    async def fetch_watchlist_baselines(self) -> None:
        """
        Polls the multi-symbol Yahoo Finance quote API to fetch current market baselines.
        Falls back to default baselines if throttling occurs.
        """
        symbols_str = ",".join(self.watchlist)
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols_str}"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self.headers, timeout=2.5)
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("quoteResponse", {}).get("result", [])
                    
                    for item in results:
                        symbol = item.get("symbol")
                        price = item.get("regularMarketPrice")
                        if symbol and price:
                            self.baselines[symbol] = float(price)
                            if symbol not in self.current_prices:
                                self.current_prices[symbol] = float(price)
                                
                    logger.debug(f"Successfully updated market baselines for: {symbols_str}")
                    return
                else:
                    logger.warning(f"Yahoo Multi-Quote returned status {response.status_code}. Throttling fallback active.")
        except Exception as e:
            logger.warning(f"Failed to fetch Yahoo Multi-Quote: {str(e)}. Fallback active.")

        # Apply fallback baselines for any missing symbols
        for sym in self.watchlist:
            if sym not in self.baselines:
                self.baselines[sym] = DEFAULT_FALLBACKS.get(sym, 150.00)
            if sym not in self.current_prices:
                self.current_prices[sym] = self.baselines[sym]

    async def listen_control_plane(self) -> None:
        """
        Subscribes to Redis control plane to dynamically handle incoming ticker search signals (for Yahoo Fallback).
        """
        async with redis_client.client.pubsub() as pubsub:
            await pubsub.subscribe(REDIS_CONTROL_CHANNEL)
            logger.info(f"Fallback control plane registered subscription to: {REDIS_CONTROL_CHANNEL}")
            
            try:
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    
                    payload = json.loads(message["data"])
                    action = payload.get("action")
                    symbol = payload.get("symbol", "").upper().strip()
                    
                    if action == "add" and symbol:
                        if symbol not in self.watchlist:
                            logger.info(f"Adding searched symbol {symbol} to fallback watchlist...")
                            self.watchlist.add(symbol)
                            await self.fetch_watchlist_baselines()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Error in fallback control plane handler: {str(e)}")

    async def run_alpaca_feed(self) -> None:
        """
        Connects directly to Alpaca Markets real-time low-latency IEX WebSockets.
        """
        url = "wss://stream.data.alpaca.markets/v2/iex"
        logger.info(f"Connecting to Alpaca Markets Real-Time IEX trade WebSocket: {url}")
        
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            # 1. Listen for connection success greeting
            greeting = await ws.recv()
            logger.info(f"Alpaca WebSocket greeting: {greeting}")
            
            # 2. Authenticate using configured settings credentials
            auth_payload = {
                "action": "auth",
                "key": settings.ALPACA_API_KEY_ID,
                "secret": settings.ALPACA_API_SECRET_KEY
            }
            await ws.send(json.dumps(auth_payload))
            
            # 3. Read authentication reply
            auth_status = await ws.recv()
            logger.info(f"Alpaca authentication status: {auth_status}")
            
            status_data = json.loads(auth_status)
            if status_data and status_data[0].get("msg") != "authenticated":
                raise ValueError(f"Alpaca Authentication Rejected: {auth_status}")

            # 4. Subscribe to active watchlist trades
            sub_payload = {
                "action": "subscribe",
                "trades": list(self.watchlist)
            }
            await ws.send(json.dumps(sub_payload))
            logger.info(f"Subscribed to Alpaca Trades for: {list(self.watchlist)}")
            
            # 5. Spawn background task to sync live UI searches directly with Alpaca subscriptions
            async def listen_searches_and_subscribe():
                async with redis_client.client.pubsub() as pubsub:
                    await pubsub.subscribe(REDIS_CONTROL_CHANNEL)
                    try:
                        async for message in pubsub.listen():
                            if message["type"] != "message":
                                continue
                            
                            payload = json.loads(message["data"])
                            action = payload.get("action")
                            symbol = payload.get("symbol", "").upper().strip()
                            
                            if action == "add" and symbol:
                                if symbol not in self.watchlist:
                                    logger.info(f"Alpaca subscribing dynamically to searched stock: {symbol}")
                                    self.watchlist.add(symbol)
                                    
                                    # Send live subscription payload
                                    resub = {
                                        "action": "subscribe",
                                        "trades": [symbol]
                                    }
                                    await ws.send(json.dumps(resub))
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.error(f"Alpaca control resubscription task error: {str(e)}")

            subscribe_task = asyncio.create_task(listen_searches_and_subscribe())
            
            try:
                # 6. Stream incoming trades and publish to local Redis channels
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    
                    for item in data:
                        if item.get("T") == "t":  # Trade event
                            symbol = item.get("S")
                            price = float(item.get("p"))
                            volume = int(item.get("s"))
                            
                            tick_payload = {
                                "symbol": symbol,
                                "price": price,
                                "volume": volume,
                                "timestamp": time.time()
                            }
                            
                            # Publish to local engine Websockets
                            if redis_client.client:
                                await redis_client.client.publish(
                                    REDIS_TICK_CHANNEL,
                                    json.dumps(tick_payload)
                                )
            finally:
                subscribe_task.cancel()

    async def run_yahoo_fallback(self) -> None:
        """
        Fall back to dynamic multi-ticker Yahoo Finance scraper with random-walk ticks.
        """
        logger.info("Initializing dynamic multi-ticker Yahoo Finance fallback scraper...")
        await self.fetch_watchlist_baselines()
        
        tick_count = 0
        ticks_per_fetch = int(FETCH_INTERVAL_SEC / TICK_INTERVAL_SEC)  # 10 ticks
        
        control_task = asyncio.create_task(self.listen_control_plane())
        
        try:
            while True:
                if tick_count >= ticks_per_fetch:
                    await self.fetch_watchlist_baselines()
                    tick_count = 0
                
                for symbol in list(self.watchlist):
                    baseline = self.baselines.get(symbol, DEFAULT_FALLBACKS.get(symbol, 150.00))
                    current = self.current_prices.get(symbol, baseline)
                    
                    micro_fluctuation = random.uniform(-0.15, 0.15)
                    current = round(current + micro_fluctuation, 2)
                    
                    if abs(current - baseline) > (baseline * 0.015):
                        current = baseline
                        
                    self.current_prices[symbol] = current
                    volume = int(random.uniform(50, 1500))
                    
                    tick_payload = {
                        "symbol": symbol,
                        "price": current,
                        "volume": volume,
                        "timestamp": time.time()
                    }
                    
                    if redis_client.client:
                        await redis_client.client.publish(
                            REDIS_TICK_CHANNEL,
                            json.dumps(tick_payload)
                        )
                
                tick_count += 1
                await asyncio.sleep(TICK_INTERVAL_SEC)
                
        except asyncio.CancelledError:
            pass
        finally:
            control_task.cancel()

    async def run(self) -> None:
        """
        Checks for active Alpaca developer keys to load live IEX trades,
        otherwise logs configurations and loads the Yahoo Finance fallback loop.
        """
        # Validate keys are not default templates
        has_keys = (
            settings.ALPACA_API_KEY_ID and 
            settings.ALPACA_API_SECRET_KEY and 
            settings.ALPACA_API_KEY_ID != "your_alpaca_key_id_here" and
            settings.ALPACA_API_KEY_ID != ""
        )
        
        if has_keys:
            try:
                await self.run_alpaca_feed()
                return
            except Exception as e:
                logger.error(f"Alpaca live WebSocket disconnected or failed: {str(e)}")
                logger.warning("Failing over to Yahoo Finance real-time fallback quote scraper.")
        else:
            logger.info("Alpaca API credentials missing inside .env configurations.")
            logger.info("Yahoo Finance active fallback quote engine selected.")

        # Fallback loop execution
        await self.run_yahoo_fallback()


# Monolith runner hook
async def start_market_feed_simulation():
    manager = MarketFeedManager()
    await manager.run()
