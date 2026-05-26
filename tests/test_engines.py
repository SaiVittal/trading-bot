import os
import sys
import unittest
import pandas as pd
import numpy as np

# Add the backend app folder to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../apps/backend")))

from app.services.strategy_engine import _Indicators, StrategyScanner

class TestQuantitativeEngines(unittest.TestCase):
    def setUp(self):
        """Create a mock DataFrame with sample price actions (OHLCV) representing an uptrend."""
        np.random.seed(42)
        dates = pd.date_range(start="2026-05-26 09:30:00", periods=50, freq="1min")
        
        # Simulating a smooth upward trend starting from $100
        close_prices = 100.0 + np.cumsum(np.random.normal(0.15, 0.05, 50))
        open_prices = close_prices - np.random.normal(0, 0.02, 50)
        high_prices = np.maximum(open_prices, close_prices) + np.random.uniform(0.01, 0.05, 50)
        low_prices = np.minimum(open_prices, close_prices) - np.random.uniform(0.01, 0.05, 50)
        volumes = np.random.randint(100, 1000, 50)
        
        self.df = pd.DataFrame({
            "Open": open_prices,
            "High": high_prices,
            "Low": low_prices,
            "Close": close_prices,
            "Volume": volumes
        }, index=dates)

    def test_indicator_computation(self):
        """Verify that all indicators are computed correctly and values are within expected boundaries."""
        df_indicators = _Indicators.run(self.df)
        
        # Assert that all standard columns are present
        expected_cols = [
            "STC_K", "STC_D", "RSI", "VWAP", "ATR", 
            "EMA9", "EMA20", "EMA21", "EMA50", 
            "BB_MID", "BB_UP", "BB_LO", 
            "VOL_MA20", "REL_VOL", "EMA9_SLOPE"
        ]
        
        for col in expected_cols:
            self.assertIn(col, df_indicators.columns, f"Indicator column {col} was not computed.")

        # Math boundary verification
        # 1. Bollinger Bands order check
        mid = df_indicators["BB_MID"].dropna()
        up = df_indicators["BB_UP"].dropna()
        lo = df_indicators["BB_LO"].dropna()
        
        common_indices = mid.index.intersection(up.index).intersection(lo.index)
        for idx in common_indices[-5:]:
            self.assertTrue(up.loc[idx] >= mid.loc[idx], "Bollinger Band Upper fell below BB Mid.")
            self.assertTrue(mid.loc[idx] >= lo.loc[idx], "Bollinger Band Lower rose above BB Mid.")

        # 2. RSI limits check
        rsi = df_indicators["RSI"].dropna()
        for idx in rsi.index:
            val = rsi.loc[idx]
            self.assertTrue(0 <= val <= 100, f"RSI was calculated outside [0, 100] range: {val}")

        # 3. Relative Volume check
        rel_vol = df_indicators["REL_VOL"].dropna()
        self.assertTrue(all(rel_vol >= 0), "Relative volume contained negative values.")

    def test_strategy_scanner(self):
        """Verify that StrategyScanner runs successfully without crashing and filters results."""
        scanner = StrategyScanner(min_confidence=30)
        
        # Test scan
        signals = scanner.scan("TSLA", self.df, timeframe="1m")
        self.assertIsInstance(signals, list, "StrategyScanner scan did not return a list.")
        
        # If signals fired, make sure they have a valid structure
        for sig in signals:
            self.assertEqual(sig.ticker, "TSLA")
            self.assertIn(sig.direction, ["bullish", "bearish"])
            self.assertTrue(30 <= sig.confidence <= 100)
            self.assertIsNotNone(sig.entry)
            self.assertIsNotNone(sig.stop)
            self.assertIsNotNone(sig.t1)
            self.assertIsNotNone(sig.t2)
            self.assertTrue(sig.max_score > 0)

if __name__ == "__main__":
    unittest.main()
