import pytest
from telegram.alert_parser import parse_alert


def test_open_drive_buy():
    text = "🔥 OPEN DRIVE BUY TSLA | Entry: 215.40 | T1: 218 | SL: 213"
    order = parse_alert(text)
    assert order is not None
    assert order["symbol"] == "TSLA"
    assert order["side"] == "buy"
    assert order["order_type"] == "limit"
    assert order["limit_price"] == 215.40
    assert order["stop_price"] == 213.0
    assert order["signal_type"] == "open_drive"
    assert order["notional"] is None


def test_open_drive_sell():
    text = "🔥 OPEN DRIVE SELL AAPL | Entry: 190.00 | T1: 185 | SL: 193"
    order = parse_alert(text)
    assert order["side"] == "sell"
    assert order["symbol"] == "AAPL"


def test_0dte_call_market():
    text = "⚡ 0DTE CALL SPY | Entry: 522 | Qty: 5 | Market"
    order = parse_alert(text)
    assert order is not None
    assert order["symbol"] == "SPY"
    assert order["side"] == "buy"
    assert order["qty"] == 5.0
    assert order["order_type"] == "market"
    assert order["signal_type"] == "0dte"


def test_0dte_put():
    text = "⚡ 0DTE PUT QQQ | Entry: 440 | Qty: 3 | Market"
    order = parse_alert(text)
    assert order["side"] == "sell"
    assert order["symbol"] == "QQQ"


def test_momentum_limit():
    text = "📈 MOMENTUM BUY NVDA 10 shares | Limit 218.50"
    order = parse_alert(text)
    assert order is not None
    assert order["symbol"] == "NVDA"
    assert order["side"] == "buy"
    assert order["qty"] == 10.0
    assert order["order_type"] == "limit"
    assert order["limit_price"] == 218.50
    assert order["signal_type"] == "momentum"


def test_swing_notional_market():
    text = "🌊 SWING BUY AAPL | $500 notional | Market"
    order = parse_alert(text)
    assert order is not None
    assert order["symbol"] == "AAPL"
    assert order["side"] == "buy"
    assert order["notional"] == 500.0
    assert order["qty"] is None
    assert order["order_type"] == "market"
    assert order["signal_type"] == "swing"


def test_scalp_stop():
    text = "⚡ SCALP SELL QQQ 3 shares | Stop 445.00"
    order = parse_alert(text)
    assert order is not None
    assert order["symbol"] == "QQQ"
    assert order["side"] == "sell"
    assert order["qty"] == 3.0
    assert order["order_type"] == "stop"
    assert order["stop_price"] == 445.0
    assert order["signal_type"] == "scalp"


def test_unrecognized_returns_none():
    assert parse_alert("Market is looking bullish today!") is None
    assert parse_alert("") is None
