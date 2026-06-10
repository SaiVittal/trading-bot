import requests
from typing import Any

from config.alpaca_config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL
from alpaca.order_validator import validate_order
from alpaca.order_logger import append_order

# Crypto symbols use GTC; equities use DAY
_CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "MATIC"}

ORDERS_URL = f"{ALPACA_BASE_URL}/v2/orders"


def _headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
        "Content-Type": "application/json",
    }


def place_order(order: dict[str, Any], raw_alert: str) -> dict[str, Any]:
    """
    Validates, submits to Alpaca Paper, logs, and returns the API response.
    Raises ValueError if validation fails (caller sends the rejection Telegram msg).
    """
    is_valid, reason = validate_order(order)
    if not is_valid:
        raise ValueError(reason)

    symbol: str = order["symbol"].upper()
    tif = order.get("tif") or ("gtc" if symbol in _CRYPTO_SYMBOLS else "day")

    payload: dict[str, Any] = {
        "symbol": symbol,
        "side": order["side"],
        "type": order.get("order_type", "market"),
        "time_in_force": tif,
    }

    if order.get("notional") is not None:
        payload["notional"] = str(order["notional"])
    else:
        payload["qty"] = str(order["qty"])

    if order.get("limit_price") is not None:
        payload["limit_price"] = str(order["limit_price"])
    if order.get("stop_price") is not None:
        payload["stop_price"] = str(order["stop_price"])

    resp = requests.post(ORDERS_URL, json=payload, headers=_headers(), timeout=10)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()

    append_order(
        signal_type=order.get("signal_type", "unknown"),
        symbol=symbol,
        side=order["side"],
        order_type=payload["type"],
        qty=order.get("qty"),
        notional=order.get("notional"),
        alpaca_response=result,
        raw_alert=raw_alert,
    )

    return result
