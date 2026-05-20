import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.redis_client import redis_client

router = APIRouter()
logger = logging.getLogger("app.api.websocket")

# Subscribed channels
CHANNELS = ["market:ticks", "market:candles", "signals:alerts"]

@router.websocket("")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint connecting clients to the real-time Redis event stream.
    """
    await websocket.accept()
    logger.info("New WebSocket connection accepted.")

    # Create a subscriber channel
    pubsub = redis_client.client.pubsub()
    await pubsub.subscribe(*CHANNELS)
    logger.debug(f"Subscribed WebSocket client to: {CHANNELS}")

    # Flag to monitor receiver status
    running = True

    async def client_receiver():
        """
        Listen for incoming client packets, searches, or disconnect frames.
        """
        nonlocal running
        try:
            while running:
                # Blocks waiting for client packets
                data_str = await websocket.receive_text()
                try:
                    payload = json.loads(data_str)
                    if payload.get("type") == "search":
                        symbol = payload.get("symbol", "").upper().strip()
                        if symbol:
                            logger.info(f"Client requested dynamic subscription to: {symbol}")
                            # Broadcast to our real-time feed control plane in Redis
                            await redis_client.client.publish(
                                "market:control",
                                json.dumps({"action": "add", "symbol": symbol})
                            )
                except Exception as ex:
                    logger.warning(f"Failed to process client WebSocket control packet: {str(ex)}")
        except WebSocketDisconnect:
            logger.info("WebSocket connection closed by client.")
            running = False
        except Exception as e:
            logger.error(f"WebSocket client receiver error: {str(e)}")
            running = False

    # Spawn receiver task in background
    receiver_task = asyncio.create_task(client_receiver())

    try:
        # Loop listening to Redis messages and forwarding them
        while running:
            # Short timeout to avoid blocking forever on cancel
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            
            if message and message["type"] == "message":
                channel = message["channel"]
                data = json.loads(message["data"])
                
                # Construct combined payload
                payload = {
                    "channel": channel,
                    "data": data
                }
                
                # Forward to WebSocket client
                await websocket.send_json(payload)
                
            await asyncio.sleep(0.01)  # Yield CPU execution
            
    except Exception as e:
        logger.error(f"Error in WebSocket broadcasting loop: {str(e)}")
    finally:
        running = False
        receiver_task.cancel()
        await pubsub.unsubscribe(*CHANNELS)
        await pubsub.close()
        logger.info("WebSocket subscriber clean up finalized.")
