"""
Drop-in upgrade engines for the trading bot.
Adds: Candle Patterns · Price Range · Multi-TF · Volume Intelligence
      Volatility Regime · Probability Scoring · Dynamic Risk · AI Narrative
"""

import os
import math
import json
import requests
from datetime import datetime
import pandas as pd
import numpy as np
import pytz

try:
    import pandas_ta as ta
    USE_PTA = True
except ImportError:
    USE_PTA = False

# ══════════════════════════════════════════════════════════════
#  1. INDICATOR ENGINE
# ══════════════════════════════════════════════════════════════

class IndicatorEngine:
    """Calculate STC, RSI, VWAP, ATR, EMA9/21 on a DataFrame."""

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if USE_PTA:
            df.ta.stoch(k=5, d=3, smooth_k=3, append=True)
            df.ta.rsi(length=14, append=True)
            df.ta.vwap(append=True)
            df.ta.atr(length=14, append=True)
            df.ta.ema(length=9, append=True)
            df.ta.ema(length=21, append=True)
            stk  = [c for c in df.columns if c.startswith("STOCHk")]
            std  = [c for c in df.columns if c.startswith("STOCHd")]
            atr  = [c for c in df.columns if c.startswith("ATR")]
            vwap = [c for c in df.columns if "VWAP" in c.upper()]
            if stk:  df["STC_K"] = df[stk[0]]
            if std:  df["STC_D"] = df[std[0]]
            if atr:  df["ATR"]   = df[atr[0]]
            if vwap: df["VWAP"]  = df[vwap[0]]
            if "RSI_14" in df.columns: df["RSI"]   = df["RSI_14"]
            if "EMA_9"  in df.columns: df["EMA9"]  = df["EMA_9"]
            if "EMA_21" in df.columns: df["EMA21"] = df["EMA_21"]
        else:
            df = self._manual_indicators(df)
        return df

    def _manual_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        lo    = df["Low"].rolling(5).min()
        hi    = df["High"].rolling(5).max()
        raw_k = 100 * (df["Close"] - lo) / (hi - lo + 1e-10)
        df["STC_K"] = raw_k.rolling(3).mean()
        df["STC_D"] = df["STC_K"].rolling(3).mean()

        delta = df["Close"].diff()
        gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        df["RSI"] = 100 - (100 / (1 + gain / (loss + 1e-10)))

        df["_date"] = pd.DatetimeIndex(df.index).date
        tp  = (df["High"] + df["Low"] + df["Close"]) / 3
        tpv = tp * df["Volume"]
        df["VWAP"] = (
            tpv.groupby(df["_date"]).cumsum()
            / df["Volume"].groupby(df["_date"]).cumsum()
        )

        h_l  = df["High"] - df["Low"]
        h_pc = (df["High"] - df["Close"].shift()).abs()
        l_pc = (df["Low"]  - df["Close"].shift()).abs()
        df["ATR"]  = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1).rolling(14).mean()
        df["EMA9"]  = df["Close"].ewm(span=9,  adjust=False).mean()
        df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()
        return df


# ══════════════════════════════════════════════════════════════
#  2. CANDLE PATTERN ENGINE
# ══════════════════════════════════════════════════════════════

class CandlePatternEngine:
    """Detect 10+ candlestick patterns and derive a price target."""

    def run(self, df: pd.DataFrame) -> dict:
        if len(df) < 3:
            return {"patterns": [], "bias": "neutral", "pattern_target": None,
                    "body_ratio": 0.5, "wick_ratio": 0.3, "candle_expansion": 1.0}

        c  = df.iloc[-1]
        p  = df.iloc[-2]
        p2 = df.iloc[-3]

        o, h, l, cl = float(c.Open), float(c.High), float(c.Low), float(c.Close)
        po, ph, pl, pc = float(p.Open), float(p.High), float(p.Low), float(p.Close)

        body     = abs(cl - o)
        rng      = (h - l) if (h - l) > 0 else 1e-10
        uw       = h - max(o, cl)
        lw       = min(o, cl) - l
        is_bull  = cl > o
        is_bear  = cl < o
        avg_body = abs(df["Close"] - df["Open"]).rolling(10).mean().iloc[-1]
        avg_body = avg_body if avg_body > 0 else 1e-10

        patterns: dict = {}
        patterns["shooting_star"]      = is_bull and uw > body * 2 and lw < body * 0.3 and uw / rng > 0.55
        patterns["bearish_engulfing"]  = is_bear and cl < po and o > pc and body > abs(pc - po) * 0.8
        patterns["bearish_pin_bar"]    = uw / rng > 0.60 and body / rng < 0.25
        patterns["dark_cloud_cover"]   = is_bear and po < pc and o > ph and cl < (po + pc) / 2
        patterns["hanging_man"]        = is_bear and lw > body * 2 and uw < body * 0.3 and lw / rng > 0.55
        patterns["hammer"]             = is_bear and lw > body * 2 and uw < body * 0.3 and lw / rng > 0.55
        patterns["bullish_engulfing"]  = is_bull and cl > po and o < pc and body > abs(pc - po) * 0.8
        patterns["bullish_pin_bar"]    = lw / rng > 0.60 and body / rng < 0.25
        patterns["morning_star_approx"] = (
            is_bull and abs(p.Close - p.Open) / rng < 0.15
            and float(p2.Close) > float(p2.Open) and body > avg_body * 0.8
        )
        patterns["doji"]         = body / rng < 0.08
        patterns["inside_bar"]   = h < ph and l > pl

        recent_high = df["High"].iloc[-6:-1].max()
        recent_low  = df["Low"].iloc[-6:-1].min()
        patterns["breakout_candle"]  = is_bull and body / rng > 0.72 and cl > recent_high
        patterns["breakdown_candle"] = is_bear and body / rng > 0.72 and cl < recent_low

        triggered = [k for k, v in patterns.items() if v]

        bearish_p = {"shooting_star", "bearish_engulfing", "bearish_pin_bar",
                     "dark_cloud_cover", "hanging_man", "breakdown_candle"}
        bullish_p = {"hammer", "bullish_engulfing", "bullish_pin_bar",
                     "morning_star_approx", "breakout_candle"}

        bull_count = sum(1 for t in triggered if t in bullish_p)
        bear_count = sum(1 for t in triggered if t in bearish_p)

        if bear_count > bull_count:    bias = "bearish"
        elif bull_count > bear_count:  bias = "bullish"
        else:                          bias = "neutral"

        pattern_target = None
        if bias == "bearish" and body > 0:
            pattern_target = {"T1": round(cl - body * 1.5, 2),
                               "T2": round(cl - body * 2.5, 2),
                               "method": "candle_body_projection"}
        elif bias == "bullish" and body > 0:
            pattern_target = {"T1": round(cl + body * 1.5, 2),
                               "T2": round(cl + body * 2.5, 2),
                               "method": "candle_body_projection"}

        return {
            "patterns":         triggered,
            "bias":             bias,
            "pattern_target":   pattern_target,
            "body_ratio":       round(body / rng, 2),
            "wick_ratio":       round((uw + lw) / rng, 2),
            "candle_expansion": round(body / avg_body, 2),
        }


# ══════════════════════════════════════════════════════════════
#  3. PRICE RANGE ENGINE
# ══════════════════════════════════════════════════════════════

class PriceRangeEngine:
    """Expected move from ATR · candle projection · S/R · rolling volatility."""

    def run(self, df: pd.DataFrame, direction: str, candle_data: dict) -> dict:
        price = float(df["Close"].iloc[-1])
        atr   = float(df["ATR"].iloc[-1]) if "ATR" in df.columns else price * 0.005

        atr_range = {"low": round(price - atr * 1.5, 2), "high": round(price + atr * 1.5, 2)}

        candle_range = None
        if candle_data.get("pattern_target"):
            pt = candle_data["pattern_target"]
            candle_range = ({"low": pt["T2"], "high": pt["T1"]} if direction == "bearish"
                            else {"low": pt["T1"], "high": pt["T2"]})

        sr = self._find_sr_levels(df)
        sr_target = None
        if direction == "bearish":
            supports = [s for s in sr["support"] if s < price]
            if supports:
                ns = max(supports)
                sr_target = {"low": ns, "high": round(ns + atr * 0.5, 2)}
        else:
            resistances = [r for r in sr["resistance"] if r > price]
            if resistances:
                nr = min(resistances)
                sr_target = {"low": round(nr - atr * 0.5, 2), "high": nr}

        returns  = df["Close"].pct_change().dropna()
        vol_move = (price * returns.rolling(20).std().iloc[-1] * math.sqrt(5)
                    if len(returns) >= 20 else atr)
        vol_range = {"low": round(price - vol_move, 2), "high": round(price + vol_move, 2)}

        all_lows  = [atr_range["low"],  vol_range["low"]]
        all_highs = [atr_range["high"], vol_range["high"]]
        if candle_range:
            all_lows.append(candle_range["low"])
            all_highs.append(candle_range["high"])
        if sr_target:
            all_lows.append(sr_target["low"])
            all_highs.append(sr_target["high"])

        c_low  = round(sum(all_lows)  / len(all_lows),  2)
        c_high = round(sum(all_highs) / len(all_highs), 2)

        if direction == "bearish":
            exp_low, exp_high = min(c_low, c_high), price
        else:
            exp_low, exp_high = price, max(c_low, c_high)

        return {
            "expected_low":  round(exp_low,  2),
            "expected_high": round(exp_high, 2),
            "atr_range":     atr_range,
            "candle_range":  candle_range,
            "sr_target":     sr_target,
            "vol_range":     vol_range,
            "support":       sr["support"],
            "resistance":    sr["resistance"],
            "atr":           round(atr, 4),
        }

    def _find_sr_levels(self, df: pd.DataFrame, window: int = 5, max_levels: int = 4) -> dict:
        highs, lows = df["High"].values, df["Low"].values
        piv_h, piv_l = [], []
        for i in range(window, len(df) - window):
            seg_h = highs[i - window: i + window + 1]
            seg_l = lows[i - window:  i + window + 1]
            if highs[i] == max(seg_h): piv_h.append(round(highs[i], 2))
            if lows[i]  == min(seg_l): piv_l.append(round(lows[i],  2))
        price      = float(df["Close"].iloc[-1])
        resistance = sorted(set(h for h in piv_h if h > price))[:max_levels]
        support    = sorted(set(l for l in piv_l if l < price), reverse=True)[:max_levels]
        return {"resistance": resistance, "support": support}


# ══════════════════════════════════════════════════════════════
#  4. MULTI-TIMEFRAME ENGINE
# ══════════════════════════════════════════════════════════════

class MultiTimeframeEngine:
    """Resample to 5m / 15m / 1h and assess trend alignment."""

    def run(self, df_base: pd.DataFrame) -> dict:
        result = {}
        for tf, rule in [("base", None), ("5m", "5min"), ("15m", "15min"), ("1h", "1h")]:
            df = df_base if rule is None else self._resample(df_base, rule)
            if df is None or len(df) < 5:
                result[tf] = {"trend": "unknown", "rsi": None, "above_vwap": None,
                               "stoch_signal": "neutral", "stoch_k": None}
                continue
            df = IndicatorEngine().run(df)
            cur = df.iloc[-1]
            prv = df.iloc[-2]

            ema_bull          = (cur.get("EMA9") or 0) > (cur.get("EMA21") or 0)
            recent_close_bull = float(cur["Close"]) > float(df["Close"].iloc[-5])

            if ema_bull and recent_close_bull:         trend = "bullish"
            elif not ema_bull and not recent_close_bull: trend = "bearish"
            else:                                        trend = "neutral"

            stoch_signal = "neutral"
            k = cur.get("STC_K")
            d = cur.get("STC_D")
            pk = prv.get("STC_K")
            pd_ = prv.get("STC_D")
            if k is not None and d is not None and pk is not None and pd_ is not None:
                k_val = float(k)
                d_val = float(d)
                pk_val = float(pk)
                pd_val = float(pd_)
                if pk_val < pd_val and k_val > d_val and k_val < 30:  stoch_signal = "bullish_cross"
                elif pk_val > pd_val and k_val < d_val and k_val > 70: stoch_signal = "bearish_cross"

            rsi_val   = cur.get("RSI")
            vwap_val  = cur.get("VWAP")
            above_vwap = (float(cur["Close"]) > float(vwap_val)
                          if vwap_val is not None else None)

            result[tf] = {
                "trend":        trend,
                "rsi":          round(float(rsi_val), 1) if rsi_val is not None else None,
                "stoch_k":      round(float(k), 1) if k is not None and not math.isnan(float(k)) else None,
                "above_vwap":   above_vwap,
                "stoch_signal": stoch_signal,
            }

        trends   = [result[tf]["trend"] for tf in ["base", "5m", "15m", "1h"]]
        bull_n   = trends.count("bullish")
        bear_n   = trends.count("bearish")
        dominant = "bullish" if bull_n >= bear_n else "bearish"
        aligned  = max(bull_n, bear_n)

        return {
            "timeframes":    result,
            "dominant":      dominant,
            "aligned_tfs":   aligned,
            "alignment_pct": int(aligned / 4 * 100),
        }

    def _resample(self, df: pd.DataFrame, rule: str):
        try:
            agg  = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
            cols = {k: v for k, v in agg.items() if k in df.columns}
            out  = df.resample(rule).agg(cols).dropna()
            return out if len(out) >= 5 else None
        except Exception:
            return None


# ══════════════════════════════════════════════════════════════
#  5. VOLUME INTELLIGENCE ENGINE
# ══════════════════════════════════════════════════════════════

class VolumeEngine:

    def run(self, df: pd.DataFrame) -> dict:
        if "Volume" not in df.columns or len(df) < 21:
            return {"rel_vol": 1.0, "spike": False, "exhaustion": False,
                    "confirmation": False, "vol_trend": "normal", "avg_volume": 0}

        vol    = df["Volume"].values
        cur_v  = float(vol[-1])
        avg_v  = max(float(np.mean(vol[-21:-1])), 1)
        rel_vol = round(cur_v / avg_v, 2)

        vol_declining = all(vol[-i] < vol[-i - 1] for i in range(1, 4))
        vol_ma5  = np.mean(vol[-5:])
        vol_ma10 = np.mean(vol[-10:])

        return {
            "rel_vol":      rel_vol,
            "spike":        rel_vol >= 1.8,
            "exhaustion":   bool(vol_declining),
            "confirmation": rel_vol >= 1.5,
            "vol_trend":    "rising" if vol_ma5 > vol_ma10 else "falling",
            "avg_volume":   int(avg_v),
        }


# ══════════════════════════════════════════════════════════════
#  6. VOLATILITY ENGINE
# ══════════════════════════════════════════════════════════════

class VolatilityEngine:

    def run(self, df: pd.DataFrame) -> dict:
        if len(df) < 21:
            return {"regime": "normal", "atr": 0, "atr_avg": 0,
                    "vwap_dist_pct": 0, "expanding": False, "rolling_vol": 0}

        atr     = float(df["ATR"].iloc[-1])   if "ATR"  in df.columns else 0
        atr_avg = float(df["ATR"].iloc[-21:-1].mean()) if "ATR" in df.columns else atr
        atr_avg = max(atr_avg, 1e-10)

        if atr > atr_avg * 1.5:    regime = "high"
        elif atr < atr_avg * 0.6:  regime = "low"
        else:                       regime = "normal"

        expanding = False
        if "ATR" in df.columns:
            vals = df["ATR"].dropna().values
            expanding = len(vals) >= 3 and all(vals[-i] > vals[-i - 1] for i in range(1, 3))

        price = float(df["Close"].iloc[-1])
        vwap  = float(df["VWAP"].iloc[-1]) if "VWAP" in df.columns else price
        vwap_dist_pct = round(abs(price - vwap) / vwap * 100, 2) if vwap > 0 else 0

        returns  = df["Close"].pct_change().dropna()
        roll_vol = float(returns.rolling(20).std().iloc[-1]) * math.sqrt(252) * 100

        return {
            "regime":        regime,
            "atr":           round(atr, 4),
            "atr_avg":       round(atr_avg, 4),
            "expanding":     bool(expanding),
            "vwap_dist_pct": vwap_dist_pct,
            "rolling_vol":   round(roll_vol, 2),
        }


# ══════════════════════════════════════════════════════════════
#  7. PROBABILITY ENGINE
# ══════════════════════════════════════════════════════════════

class ProbabilityEngine:
    """Weighted confidence 0-100: Trend 30% · Momentum 25% · Volume 20% · Volatility 15% · Pattern 10%"""

    WEIGHTS = {"trend": 0.30, "momentum": 0.25, "volume": 0.20,
               "volatility": 0.15, "pattern": 0.10}

    def run(self, mtf: dict, df: pd.DataFrame,
            vol: dict, volume: dict, candles: dict) -> dict:

        direction = mtf["dominant"]
        trend_score = mtf["alignment_pct"]

        rsi_5m = mtf["timeframes"].get("5m", {}).get("rsi") or 50
        stk_5m = mtf["timeframes"].get("5m", {}).get("stoch_k") or 50
        if direction == "bearish":
            rsi_score   = max(0, (rsi_5m - 50) / 50 * 100)
            stoch_score = max(0, (stk_5m - 50) / 50 * 100)
        else:
            rsi_score   = max(0, (50 - rsi_5m) / 50 * 100)
            stoch_score = max(0, (50 - stk_5m) / 50 * 100)
        momentum_score = int((rsi_score + stoch_score) / 2)

        vol_score = min(100, int(volume["rel_vol"] * 35))
        if volume["spike"]:        vol_score = min(100, vol_score + 20)
        if volume["exhaustion"]:   vol_score = max(0,   vol_score - 30)
        if volume["confirmation"]: vol_score = min(100, vol_score + 10)

        regime_scores    = {"high": 80, "normal": 60, "low": 35}
        volatility_score = regime_scores.get(vol["regime"], 60)
        if vol["expanding"]: volatility_score = min(100, volatility_score + 15)

        pattern_score = min(100, len(candles["patterns"]) * 30)
        if candles["bias"] == direction: pattern_score = min(100, pattern_score + 20)

        confidence = int(
            trend_score      * self.WEIGHTS["trend"]      +
            momentum_score   * self.WEIGHTS["momentum"]   +
            vol_score        * self.WEIGHTS["volume"]      +
            volatility_score * self.WEIGHTS["volatility"] +
            pattern_score    * self.WEIGHTS["pattern"]
        )
        confidence = max(0, min(100, confidence))
        conf_label = "HIGH" if confidence >= 70 else "MODERATE" if confidence >= 50 else "LOW"

        return {
            "direction":               direction,
            "confidence":              confidence,
            "confidence_label":        conf_label,
            "continuation_probability": confidence,
            "reversal_probability":     100 - confidence,
            "scores": {
                "trend":      int(trend_score),
                "momentum":   momentum_score,
                "volume":     int(vol_score),
                "volatility": int(volatility_score),
                "pattern":    int(pattern_score),
            },
        }


# ══════════════════════════════════════════════════════════════
#  8. DYNAMIC RISK ENGINE
# ══════════════════════════════════════════════════════════════

class RiskEngine:

    def run(self, price: float, direction: str, atr: float, price_range: dict) -> dict:
        atr = atr if atr > 0 else price * 0.005

        # Use pure ATR multiples — SR/expected-range overrides caused T2 < T1.
        # stop=1.0×ATR, T1=1.5×ATR, T2=4.0×ATR (realistic for TSLA: ~$2 ATR → T2 +$8)
        if direction == "bearish":
            stop = round(price + atr * 1.0, 2)
            t1   = round(price - atr * 1.5, 2)
            t2   = round(price - atr * 4.0, 2)
        else:
            stop = round(price - atr * 1.0, 2)
            t1   = round(price + atr * 1.5, 2)
            t2   = round(price + atr * 4.0, 2)

        risk   = abs(price - stop)
        reward = abs(price - t1)
        rr     = round(reward / risk, 2) if risk > 0 else 0

        return {
            "entry":  round(price, 2),
            "stop":   stop,
            "t1":     t1,
            "t2":     t2,
            "rr":     rr,
            "risk_$": round(risk, 2),
        }


# ══════════════════════════════════════════════════════════════
#  9. INSIGHT GENERATOR
# ══════════════════════════════════════════════════════════════

class InsightGenerator:

    def generate(self, ticker: str, prob: dict, mtf: dict,
                 vol: dict, volume: dict, candles: dict, price_range: dict) -> str:
        parts = []
        d     = prob["direction"]
        conf  = prob["confidence_label"]

        aligned = [tf for tf in ["base", "5m", "15m", "1h"]
                   if mtf["timeframes"].get(tf, {}).get("trend") == d]
        if len(aligned) >= 3:
            parts.append(f"{d.title()} trend confirmed on {len(aligned)}/4 timeframes")
        elif len(aligned) == 2:
            parts.append(f"Partial {d} alignment on 2/4 timeframes")

        above_5m = mtf["timeframes"].get("5m", {}).get("above_vwap")
        if d == "bearish" and above_5m is False:
            parts.append("VWAP rejection confirmed below VWAP")
        elif d == "bullish" and above_5m is True:
            parts.append("Price holding above VWAP")

        if candles["patterns"]:
            pats = ", ".join(p.replace("_", " ") for p in candles["patterns"][:2])
            # Avoid "hammer candle pattern" → "Hammer candle candle pattern"
            suffix = " pattern" if any("candle" in p for p in candles["patterns"][:2]) else " candle pattern"
            parts.append(f"{pats}{suffix}")

        if volume["spike"]:
            parts.append(f"volume spike at {volume['rel_vol']}× average")
        elif volume["exhaustion"]:
            parts.append("WARNING — volume exhaustion detected, move may stall")

        if vol["regime"] == "high":
            suffix = " and expanding" if vol["expanding"] else ""
            parts.append(f"ATR elevated{suffix} — fast move environment")

        low  = price_range.get("expected_low")
        high = price_range.get("expected_high")
        if low and high:
            parts.append(f"expected move toward ${low if d == 'bearish' else high}")

        narrative = ". ".join(p[0].upper() + p[1:] for p in parts if p) + "."
        return f"{conf} confidence {d} setup — {narrative}"


# ══════════════════════════════════════════════════════════════
#  10. ALERT FORMATTER
# ══════════════════════════════════════════════════════════════

class AlertFormatter:

    def format_console(self, ticker: str, prob: dict, mtf: dict,
                       vol: dict, volume: dict, candles: dict,
                       price_range: dict, risk: dict, insight: str) -> str:
        d      = prob["direction"]
        emoji  = "🔴" if d == "bearish" else "🟢"
        arrow  = "▼" if d == "bearish" else "▲"
        sep    = "─" * 52

        tf_lines = ""
        for tf in ["base", "5m", "15m", "1h"]:
            info  = mtf["timeframes"].get(tf, {})
            trend = info.get("trend", "?")
            rsi   = f"  RSI:{info['rsi']}" if info.get("rsi") else ""
            mark  = "✅" if trend == d else ("⚠" if trend == "neutral" else "❌")
            label = "1m" if tf == "base" else tf
            tf_lines += f"  {label:4s}: {trend.capitalize():8s} {mark}{rsi}\n"

        patterns_str    = (", ".join(p.replace("_", " ") for p in candles["patterns"])
                           if candles["patterns"] else "None detected")
        support_str     = " / ".join(f"${s}" for s in price_range.get("support", [])[:3])
        resistance_str  = " / ".join(f"${r}" for r in price_range.get("resistance", [])[:3])
        scores          = prob["scores"]

        return (
            f"\n{sep}\n"
            f"  {emoji}  {ticker} {d.upper()} ALERT  {arrow}\n"
            f"{sep}\n"
            f"  Price:   ${risk['entry']}      VWAP distance: {vol['vwap_dist_pct']}%\n\n"
            f"  Trend alignment:\n{tf_lines}\n"
            f"  ─ Expected price range ──────────────────────\n"
            f"  Low:  ${price_range['expected_low']}   →   High: ${price_range['expected_high']}\n\n"
            f"  ─ Trade levels ──────────────────────────────\n"
            f"  Entry:    ${risk['entry']}\n"
            f"  Stop:     ${risk['stop']}     (1× ATR = ${risk['risk_$']})\n"
            f"  Target 1: ${risk['t1']}    (exit 50%)\n"
            f"  Target 2: ${risk['t2']}    (exit 100%)\n"
            f"  R:R:      1:{risk['rr']}\n\n"
            f"  ─ Market structure ──────────────────────────\n"
            f"  Resistance: {resistance_str or 'N/A'}\n"
            f"  Support:    {support_str or 'N/A'}\n\n"
            f"  ─ Analysis ──────────────────────────────────\n"
            f"  Volatility:  {vol['regime'].upper()}  "
            f"(ATR {'expanding ▲' if vol['expanding'] else 'stable'})\n"
            f"  Volume:      {volume['rel_vol']}× avg  "
            f"{'🔥 spike' if volume['spike'] else ''}"
            f"{'⚠ exhaustion' if volume['exhaustion'] else ''}\n"
            f"  Patterns:    🕯 {patterns_str}\n\n"
            f"  ─ Probability ───────────────────────────────\n"
            f"  Confidence:       {prob['confidence_label']}  ({prob['confidence']}/100)\n"
            f"  Continuation:     {prob['continuation_probability']}%\n"
            f"  Reversal risk:    {prob['reversal_probability']}%\n"
            f"  Scores: Trend {scores['trend']} | Mom {scores['momentum']} "
            f"| Vol {scores['volume']} | Pattern {scores['pattern']}\n\n"
            f"  ─ AI Insight ────────────────────────────────\n"
            f"  {insight}\n\n"
            f"  ⚠  Educational only — not financial advice\n"
            f"{sep}"
        )
