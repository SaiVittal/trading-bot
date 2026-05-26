import os
import sys
import unittest
import asyncio
import httpx
from datetime import datetime

# Add the backend app folder to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../apps/backend")))

# Load backend settings
from app.core.config import settings

class TestTelegramPosting(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_connection_and_dispatch(self):
        """Verify that the Telegram Bot Token and Chat ID are configured and able to receive messages."""
        token = settings.TELEGRAM_BOT_TOKEN
        chat_id = settings.TELEGRAM_CHAT_ID

        print(f"\n[TEST INFO] Telegram Bot Token: {token[:15]}... (length: {len(token) if token else 0})")
        print(f"[TEST INFO] Telegram Chat ID: {chat_id}")

        self.assertIsNotNone(token, "TELEGRAM_BOT_TOKEN must be configured.")
        self.assertIsNotNone(chat_id, "TELEGRAM_CHAT_ID must be configured.")
        self.assertNotEqual(token, "your_telegram_bot_token", "TELEGRAM_BOT_TOKEN cannot be placeholder value.")

        # Create a mock crossover signal to format and post
        mock_signal = {
            "symbol": "TSLA",
            "action": "BUY",
            "price": 182.50,
            "direction": "bullish",
            "strategy": "VWAP Crossover Long",
            "rsi": 42.1,
            "vwap": 181.25,
            "stop": 179.50,
            "t1": 185.50,
            "t2": 190.00,
            "rr": 1.7,
            "confidence": 78
        }

        emoji = "🟢" if mock_signal["action"] == "BUY" else "🔴"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S ET")

        message = (
            f"<b>{emoji} {mock_signal['symbol']} — QUANT {mock_signal['action']} TRIGGER</b>\n"
            f"<i>Strategy: {mock_signal['strategy']} (Confidence: {mock_signal['confidence']}/100)</i>\n"
            f"───────────────────────────────\n"
            f"<b>💡 Entry Price:</b> ${mock_signal['price']:.2f}\n"
            f"<b>🛡️ Stop Loss:</b> ${mock_signal['stop']:.2f}\n"
            f"<b>🎯 Target 1 (50%):</b> ${mock_signal['t1']:.2f}\n"
            f"<b>🎯 Target 2 (100%):</b> ${mock_signal['t2']:.2f}\n"
            f"<b>📊 Risk-to-Reward:</b> 1:{mock_signal['rr']}\n"
            f"───────────────────────────────\n"
            f"<b>⚡ Indicators Summary:</b>\n"
            f"  • RSI: {mock_signal['rsi']:.1f}\n"
            f"  • VWAP Floor: ${mock_signal['vwap']:.2f}\n"
            f"───────────────────────────────\n"
            f"⏱ <i>Dispatched at {now_str} | educational purpose only</i>"
        )

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        print("[TEST INFO] Dispatching test notification to Telegram...")
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10.0)
            print(f"[TEST INFO] Telegram API status code: {resp.status_code}")
            print(f"[TEST INFO] Telegram Response: {resp.text}")
            
            self.assertEqual(resp.status_code, 200, f"Telegram failed with response: {resp.text}")
            
            data = resp.json()
            self.assertTrue(data.get("ok"), "Telegram response 'ok' field was False.")
            print("[TEST SUCCESS] Integration test passed. Telegram posting successfully executed!")

if __name__ == "__main__":
    unittest.main()
