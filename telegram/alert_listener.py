"""
Polls the Telegram Bot API for new messages and feeds them into
alert_parser → order_validator → order_executor.

Does NOT modify any existing Telegram sending logic. It only reads
incoming messages and acts as an independent consumer.

Run standalone:  python -m telegram.alert_listener
"""
import time
import logging
import requests

from config.alpaca_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from telegram.alert_parser import parse_alert
from alpaca.order_executor import place_order
from alpaca.order_validator import validate_order

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
POLL_INTERVAL = 5  # seconds
_last_update_id: int = 0


def _send_rejection(chat_id: str, reason: str) -> None:
    requests.post(
        f"{TELEGRAM_API}/sendMessage",
        json={"chat_id": chat_id, "text": f"⚠️ ORDER REJECTED: {reason}"},
        timeout=10,
    )


def _process_message(text: str, chat_id: str) -> None:
    order = parse_alert(text)
    if order is None:
        return  # not a trading alert — ignore silently

    is_valid, reason = validate_order(order)
    if not is_valid:
        logger.warning("Order rejected: %s | alert: %s", reason, text)
        _send_rejection(chat_id, reason)
        return

    try:
        result = place_order(order, raw_alert=text)
        logger.info(
            "Order placed: %s %s %s → id=%s status=%s",
            order["side"], order["symbol"], order["signal_type"],
            result.get("id"), result.get("status"),
        )
    except Exception as exc:
        logger.error("Order execution failed: %s", exc)
        _send_rejection(chat_id, str(exc))


def listen_forever() -> None:
    global _last_update_id
    logger.info("Alert listener started — polling every %ds", POLL_INTERVAL)

    while True:
        try:
            resp = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params={"offset": _last_update_id + 1, "timeout": 30},
                timeout=40,
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])

            for update in updates:
                _last_update_id = update["update_id"]
                msg = update.get("message") or update.get("channel_post")
                if not msg:
                    continue
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", TELEGRAM_CHAT_ID))
                if text:
                    _process_message(text, chat_id)

        except Exception as exc:
            logger.error("Polling error: %s", exc)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    listen_forever()
