import json
import pytest
from unittest.mock import patch, MagicMock

from alpaca.order_validator import validate_order
from alpaca.order_executor import place_order

_VALID_MARKET_ORDER = {
    "symbol": "TSLA",
    "side": "buy",
    "order_type": "market",
    "qty": 1.0,
    "notional": None,
    "limit_price": None,
    "stop_price": None,
    "tif": "day",
    "signal_type": "momentum",
}


# ── Validator tests ──────────────────────────────────────────────────────────

def test_validator_rejects_missing_qty_and_notional():
    order = {**_VALID_MARKET_ORDER, "qty": None, "notional": None}
    valid, reason = validate_order(order)
    assert not valid
    assert "qty or notional" in reason


def test_validator_rejects_both_qty_and_notional():
    order = {**_VALID_MARKET_ORDER, "qty": 5.0, "notional": 500.0}
    valid, reason = validate_order(order)
    assert not valid
    assert "not both" in reason


def test_validator_rejects_missing_limit_price_on_limit_order():
    order = {**_VALID_MARKET_ORDER, "order_type": "limit", "limit_price": None}
    valid, reason = validate_order(order)
    assert not valid
    assert "limit_price" in reason


def test_validator_rejects_missing_stop_price_on_stop_order():
    order = {**_VALID_MARKET_ORDER, "order_type": "stop", "stop_price": None}
    valid, reason = validate_order(order)
    assert not valid
    assert "stop_price" in reason


def test_validator_rejects_invalid_symbol():
    order = {**_VALID_MARKET_ORDER, "symbol": ""}
    valid, reason = validate_order(order)
    assert not valid

    order2 = {**_VALID_MARKET_ORDER, "symbol": "TS LA"}
    valid2, _ = validate_order(order2)
    assert not valid2


def test_validator_rejects_bad_side():
    order = {**_VALID_MARKET_ORDER, "side": "long"}
    valid, reason = validate_order(order)
    assert not valid
    assert "buy" in reason or "sell" in reason


def test_validator_accepts_valid_order():
    valid, reason = validate_order(_VALID_MARKET_ORDER)
    assert valid
    assert reason == ""


# ── Executor tests ───────────────────────────────────────────────────────────

def test_executor_uses_paper_url(tmp_path, monkeypatch):
    """Ensures the executor posts to the paper trading URL, never live."""
    monkeypatch.setenv("ALPACA_API_KEY", "test_key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-123")

    fake_response = MagicMock()
    fake_response.json.return_value = {"id": "abc123", "status": "accepted"}
    fake_response.raise_for_status = MagicMock()

    # Patch the log path so tests don't write to the real file
    log_file = tmp_path / "orders_log.json"
    import alpaca.order_logger as ol
    monkeypatch.setattr(ol, "LOG_PATH", str(log_file))

    with patch("alpaca.order_executor.requests.post", return_value=fake_response) as mock_post:
        result = place_order(_VALID_MARKET_ORDER, raw_alert="test alert")

    call_url = mock_post.call_args[0][0]
    assert "paper-api.alpaca.markets" in call_url
    assert "live" not in call_url
    assert result["id"] == "abc123"


def test_executor_raises_on_invalid_order():
    bad_order = {**_VALID_MARKET_ORDER, "qty": None, "notional": None}
    with pytest.raises(ValueError, match="qty or notional"):
        place_order(bad_order, raw_alert="bad alert")
