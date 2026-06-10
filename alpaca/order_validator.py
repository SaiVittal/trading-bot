from typing import Any


def validate_order(order: dict[str, Any]) -> tuple[bool, str]:
    """Returns (is_valid, reason). reason is empty string on success."""
    symbol = order.get("symbol", "")
    if not symbol or not str(symbol).isalnum():
        return False, f"invalid symbol: '{symbol}'"

    side = order.get("side")
    if side not in ("buy", "sell"):
        return False, f"side must be 'buy' or 'sell', got: '{side}'"

    qty = order.get("qty")
    notional = order.get("notional")
    if qty is not None and notional is not None:
        return False, "specify qty OR notional, not both"
    if qty is None and notional is None:
        return False, "either qty or notional must be provided"
    if qty is not None and qty <= 0:
        return False, f"qty must be > 0, got: {qty}"
    if notional is not None and notional < 1.0:
        return False, f"notional must be >= 1.0, got: {notional}"

    order_type = order.get("order_type", "market")
    if order_type == "limit" and order.get("limit_price") is None:
        return False, "limit_price required for limit orders"
    if order_type == "stop" and order.get("stop_price") is None:
        return False, "stop_price required for stop orders"

    return True, ""
