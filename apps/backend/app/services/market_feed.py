import asyncio
import json
import time
import logging
import websockets
from typing import Set
from app.core.config import settings
from app.core.redis_client import redis_client

logger = logging.getLogger("app.services.market_feed")

# Redis Channels
REDIS_TICK_CHANNEL = "market:ticks"
REDIS_CONTROL_CHANNEL = "market:control"



class MarketFeedManager:
    def __init__(self) -> None:
        # Dynamic active set of symbols to scrape and tick
        self.watchlist: Set[str] = {
            "TSLA", "SPY", "MSFT", 
            "NBIS", "META", "ASML", "COST", "AMD", "MU", "SPX"
        }
        


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
                client = redis_client.client
                if not client:
                    logger.error("Redis client is not initialized. Cannot subscribe to searches.")
                    return

                async with client.pubsub() as pubsub:
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

    async def run(self) -> None:
        """
        Establishes connection to Alpaca Markets real-time trade feed.
        Loops and reconnects with exponential backoff if disconnected.
        Does not fall back to Yahoo Finance.
        """
        while True:
            # Validate keys are not default templates
            has_keys = (
                settings.ALPACA_API_KEY_ID and 
                settings.ALPACA_API_SECRET_KEY and 
                settings.ALPACA_API_KEY_ID != "your_alpaca_key_id_here" and
                settings.ALPACA_API_KEY_ID != ""
            )
            
            if not has_keys:
                logger.error("Alpaca API credentials are missing or default placeholders. "
                             "Please configure ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in your .env file "
                             "to stream live market data.")
                await asyncio.sleep(10)
                continue
            
            try:
                await self.run_alpaca_feed()
            except Exception as e:
                logger.error(f"Alpaca live WebSocket disconnected or failed: {str(e)}. "
                             "Reconnecting in 5 seconds...")
                await asyncio.sleep(5)


# Monolith runner hook
async def start_market_feed_simulation():
    manager = MarketFeedManager()
    await manager.run()
