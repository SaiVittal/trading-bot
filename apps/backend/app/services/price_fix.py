"""
Price sanity guards for the trading bot.

Catches stale, split-adjusted, or otherwise invalid prices before they
reach the alert pipeline. Based on price_fix.py from user upload.

Integration:
  - validate_dataframe() called in _build_dataframe() to reject bad data early
  - is_price_sane() called in evaluate_strategy_checklist() before every alert
"""

import logging
import math
import pandas as pd

logger = logging.getLogger("app.services.price_fix")

# ── Expected live price ranges (update when stocks move significantly) ─────────
# Format: "TICKER": (min_reasonable, max_reasonable)
# Unknown tickers are allowed through with a warning — add them here as needed.
PRICE_RANGES: dict[str, tuple[float, float]] = {
    # Mega-cap tech
    "TSLA":  (150,  900),
    "NVDA":  (60,   200),
    "AAPL":  (130,  320),
    "AMZN":  (130,  300),
    "MSFT":  (280,  600),
    "META":  (300,  900),
    "GOOGL": (120,  280),
    "GOOG":  (120,  280),
    "NFLX":  (400, 1100),
    "AMD":   (50,   300),
    "INTC":  (15,    80),
    "QCOM":  (100,  250),
    "AVGO":  (100,  280),
    "TSM":   (80,   250),
    "SMCI":  (15,   120),
    "ARM":   (80,   250),
    "ASML":  (500,  1500),
    "COST":  (400,  1200),
    "MU":    (40,   300),
    "NBIS":  (2,    80),
    "SPX":   (3500, 7500),
    # Finance
    "JPM":   (150,  300),
    "GS":    (350,  700),
    "BAC":   (25,    65),
    "C":     (50,   100),
    # ETFs
    "SPY":   (420,  700),
    "QQQ":   (350,  600),
    "IWM":   (150,  280),
    "DIA":   (330,  510),
    "XLK":   (160,  310),
    "SQQQ":  (5,     60),
    "TQQQ":  (30,   120),
    # Futures proxies
    "ES=F":  (4000, 8000),
    "NQ=F":  (13000, 25000),
    "GC=F":  (1700, 4000),
    "CL=F":  (40,   150),
}


def is_price_sane(ticker: str, price: float, warn_pct: float = 0.20) -> bool:
    """
    Return True if price is plausible for the ticker.
    Return False if price looks wrong (stale, split-adjusted, bad feed).

    Unknown tickers always return True (can't validate without a range).
    """
    if price is None or not math.isfinite(price) or price <= 0:
        logger.warning("[%s] INVALID price: %s — rejected", ticker, price)
        return False

    key = ticker.upper()
    if key not in PRICE_RANGES:
        logger.debug("[%s] Not in PRICE_RANGES — skipping range check (price=$%.2f)", ticker, price)
        return True

    lo, hi = PRICE_RANGES[key]
    if not (lo <= price <= hi):
        logger.warning(
            "[%s] PRICE SANITY FAIL: got $%.2f, expected $%.2f–$%.2f — "
            "possible stale/split-adjusted data. Alert suppressed.",
            ticker, price, lo, hi,
        )
        return False

    # Warn if unusually far from the midpoint but still in range
    mid = (lo + hi) / 2
    if abs(price - mid) / mid > warn_pct:
        logger.info(
            "[%s] PRICE NOTE: $%.2f is far from typical midpoint ($%.2f) — OK, continuing",
            ticker, price, mid,
        )

    return True


def validate_dataframe(ticker: str, df: pd.DataFrame) -> bool:
    """
    Return True if the DataFrame looks like clean live data.
    Return False and log the reason if something is wrong.

    Checks:
      - Required columns present
      - Last price is sane
      - No NaN in last 5 bars
      - Price is not stuck (not constant for 10+ bars)
    """
    if df is None or df.empty:
        logger.debug("[%s] validate_dataframe: empty DataFrame", ticker)
        return False

    required = ["Open", "High", "Low", "Close", "Volume"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        logger.warning("[%s] validate_dataframe: missing columns %s", ticker, missing)
        return False

    last_price = float(df["Close"].iloc[-1])
    if not is_price_sane(ticker, last_price):
        return False

    # NaN check on last 5 bars
    if df.tail(5)[required].isnull().any().any():
        logger.warning("[%s] validate_dataframe: NaN values in last 5 bars — data incomplete", ticker)
        return False

    # Stuck-feed check
    n_unique = df["Close"].tail(10).nunique()
    if n_unique == 1:
        logger.warning("[%s] validate_dataframe: Close price constant for 10 bars — feed may be stuck", ticker)
        return False

    return True


def add_ticker_range(ticker: str, low: float, high: float) -> None:
    """Dynamically register a price range for a new ticker at runtime."""
    PRICE_RANGES[ticker.upper()] = (low, high)
    logger.info("[%s] Price range registered: $%.2f – $%.2f", ticker, low, high)
