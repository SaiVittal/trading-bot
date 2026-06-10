import re
from typing import Any


def parse_alert(text: str) -> dict[str, Any] | None:
    """
    Parses all 5 alert formats into a standardized order dict.
    Returns None if the text doesn't match any known format.

    Supported formats:
      Open Drive:  "🔥 OPEN DRIVE BUY TSLA | Entry: 215.40 | T1: 218 | SL: 213"
      0DTE:        "⚡ 0DTE CALL SPY | Entry: 522 | Qty: 5 | Market"
      Momentum:    "📈 MOMENTUM BUY NVDA 10 shares | Limit 218.50"
      Swing:       "🌊 SWING BUY AAPL | $500 notional | Market"
      Scalp:       "⚡ SCALP SELL QQQ 3 shares | Stop 445.00"
    """
    text = text.strip()

    # --- Opening Drive ---
    m = re.search(
        r"OPEN\s+DRIVE\s+(BUY|SELL)\s+([A-Z]+).*?Entry:\s*([\d.]+).*?SL:\s*([\d.]+)",
        text, re.IGNORECASE,
    )
    if m:
        return {
            "symbol": m.group(2).upper(),
            "side": m.group(1).lower(),
            "order_type": "limit",
            "qty": None,
            "notional": None,
            "limit_price": float(m.group(3)),
            "stop_price": float(m.group(4)),
            "tif": "day",
            "signal_type": "open_drive",
        }

    # --- 0DTE (CALL → buy, PUT → sell) ---
    m = re.search(
        r"0DTE\s+(CALL|PUT)\s+([A-Z]+).*?Entry:\s*([\d.]+).*?Qty:\s*([\d.]+)",
        text, re.IGNORECASE,
    )
    if m:
        side = "buy" if m.group(1).upper() == "CALL" else "sell"
        order_type = "market" if re.search(r"\bmarket\b", text, re.IGNORECASE) else "limit"
        return {
            "symbol": m.group(2).upper(),
            "side": side,
            "order_type": order_type,
            "qty": float(m.group(4)),
            "notional": None,
            "limit_price": float(m.group(3)) if order_type == "limit" else None,
            "stop_price": None,
            "tif": "day",
            "signal_type": "0dte",
        }

    # --- Momentum: "MOMENTUM BUY NVDA 10 shares | Limit 218.50" ---
    m = re.search(
        r"MOMENTUM\s+(BUY|SELL)\s+([A-Z]+)\s+([\d.]+)\s+shares.*?(?:(Limit|Market)\s*([\d.]+)?)",
        text, re.IGNORECASE,
    )
    if m:
        order_type = "limit" if m.group(4).lower() == "limit" else "market"
        return {
            "symbol": m.group(2).upper(),
            "side": m.group(1).lower(),
            "order_type": order_type,
            "qty": float(m.group(3)),
            "notional": None,
            "limit_price": float(m.group(5)) if order_type == "limit" and m.group(5) else None,
            "stop_price": None,
            "tif": "day",
            "signal_type": "momentum",
        }

    # --- Swing: "SWING BUY AAPL | $500 notional | Market" ---
    m = re.search(
        r"SWING\s+(BUY|SELL)\s+([A-Z]+).*?\$([\d.]+)\s+notional",
        text, re.IGNORECASE,
    )
    if m:
        order_type = "market" if re.search(r"\bmarket\b", text, re.IGNORECASE) else "limit"
        return {
            "symbol": m.group(2).upper(),
            "side": m.group(1).lower(),
            "order_type": order_type,
            "qty": None,
            "notional": float(m.group(3)),
            "limit_price": None,
            "stop_price": None,
            "tif": "day",
            "signal_type": "swing",
        }

    # --- Scalp: "SCALP SELL QQQ 3 shares | Stop 445.00" ---
    m = re.search(
        r"SCALP\s+(BUY|SELL)\s+([A-Z]+)\s+([\d.]+)\s+shares.*?(?:Stop\s+([\d.]+))?",
        text, re.IGNORECASE,
    )
    if m:
        stop_price = float(m.group(4)) if m.group(4) else None
        order_type = "stop" if stop_price else "market"
        return {
            "symbol": m.group(2).upper(),
            "side": m.group(1).lower(),
            "order_type": order_type,
            "qty": float(m.group(3)),
            "notional": None,
            "limit_price": None,
            "stop_price": stop_price,
            "tif": "day",
            "signal_type": "scalp",
        }

    return None
