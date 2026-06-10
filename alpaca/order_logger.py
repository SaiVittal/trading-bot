import json
import os
from datetime import datetime, timezone
from typing import Any

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "orders_log.json")


def append_order(
    signal_type: str,
    symbol: str,
    side: str,
    order_type: str,
    qty: float | None,
    notional: float | None,
    alpaca_response: dict[str, Any],
    raw_alert: str,
) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signal_type": signal_type,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "qty": qty,
        "notional": notional,
        "alpaca_order_id": alpaca_response.get("id"),
        "status": alpaca_response.get("status"),
        "raw_alert": raw_alert,
    }

    existing: list[dict] = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                existing = []

    existing.append(record)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)
