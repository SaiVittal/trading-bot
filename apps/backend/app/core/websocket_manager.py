import asyncio
import json
import logging
from typing import Set, Optional
from fastapi import WebSocket, WebSocketDisconnect
from app.core.redis_client import redis_client

logger = logging.getLogger("app.core.websocket_manager")

CHANNELS = ["market:ticks", "market:candles", "signals:alerts", "market:status"]


class WebSocketBroadcaster:
    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()
        self.redis_listener_task: Optional[asyncio.Task] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Register a new active WebSocket client connection."""
        async with self.lock:
            self.active_connections.add(websocket)
            logger.info(
                f"Client connected. Active WebSocket connections: {len(self.active_connections)}"
            )
            
            # Start background async listener and heartbeat if they are not already running
            if not self.redis_listener_task or self.redis_listener_task.done():
                self.redis_listener_task = asyncio.create_task(self._listen_to_redis())
            
            if not self.heartbeat_task or self.heartbeat_task.done():
                self.heartbeat_task = asyncio.create_task(self._run_heartbeat())

    async def disconnect(self, websocket: WebSocket) -> None:
        """Unregister a disconnected WebSocket client."""
        async with self.lock:
            self.active_connections.discard(websocket)
            logger.info(
                f"Client disconnected. Active WebSocket connections: {len(self.active_connections)}"
            )
            
            # Clean up the background tasks if no clients are connected to save resources
            if not self.active_connections:
                if self.redis_listener_task and not self.redis_listener_task.done():
                    self.redis_listener_task.cancel()
                    self.redis_listener_task = None
                    logger.info("Cancelled global Redis WebSocket listener (0 clients).")
                
                if self.heartbeat_task and not self.heartbeat_task.done():
                    self.heartbeat_task.cancel()
                    self.heartbeat_task = None
                    logger.info("Cancelled global WebSocket heartbeat task (0 clients).")

    async def broadcast(self, message: dict) -> None:
        """Send a JSON payload to all registered active client connections in parallel."""
        if not self.active_connections:
            return

        dead_connections: Set[WebSocket] = set()
        
        # Capture current set snapshot to avoid mutation during iteration
        async with self.lock:
            targets = list(self.active_connections)

        # Broadcast in parallel using gather
        async def send_to_ws(ws: WebSocket):
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send message to client, pruning connection: {e}")
                dead_connections.add(ws)

        if targets:
            await asyncio.gather(*(send_to_ws(ws) for ws in targets), return_exceptions=True)

        if dead_connections:
            async with self.lock:
                for ws in dead_connections:
                    self.active_connections.discard(ws)
                logger.info(
                    f"Pruned {len(dead_connections)} dead client(s). Active connections remaining: {len(self.active_connections)}"
                )

    async def _listen_to_redis(self) -> None:
        """A single, global Redis Pub/Sub listener that broadcasts events to all clients."""
        logger.info("Starting global Redis WS Pub/Sub listener...")
        
        while True:
            client = redis_client.client
            if not client:
                logger.error("Redis client not initialized yet. Retrying listener in 2s...")
                await asyncio.sleep(2.0)
                continue

            try:
                async with client.pubsub() as pubsub:
                    await pubsub.subscribe(*CHANNELS)
                    logger.info(f"Global Redis listener subscribed to channels: {CHANNELS}")
                    
                    async for message in pubsub.listen():
                        if message["type"] != "message":
                            continue
                        
                        channel = message["channel"]
                        msg_data = message["data"]
                        
                        try:
                            data = json.loads(msg_data) if isinstance(msg_data, (str, bytes)) else msg_data
                            await self.broadcast({
                                "channel": channel,
                                "data": data
                            })
                        except Exception as e:
                            logger.error(f"Error parsing Redis message payload: {e}")
                            
            except asyncio.CancelledError:
                logger.info("Global Redis WS Pub/Sub listener task cancelled.")
                break
            except Exception as e:
                logger.error(f"Global Redis WS listener lost connection: {e}. Re-establishing in 2s...", exc_info=True)
                await asyncio.sleep(2.0)

    async def _run_heartbeat(self) -> None:
        """Periodic heartbeat loop. Sends ping to active clients to detect silent drops."""
        logger.info("Starting global WebSocket heartbeat ping task...")
        try:
            while True:
                await asyncio.sleep(20.0)
                if self.active_connections:
                    logger.debug(f"Broadcasting heartbeat ping to {len(self.active_connections)} clients.")
                    await self.broadcast({
                        "channel": "ping",
                        "data": "ping"
                    })
        except asyncio.CancelledError:
            logger.info("Global WebSocket heartbeat ping task cancelled.")


# Singleton global instance of the broadcaster
websocket_broadcaster = WebSocketBroadcaster()
