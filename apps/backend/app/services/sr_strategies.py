"""
=============================================================
  SUPPORT & RESISTANCE STRATEGY MODULE
  S20 · S21 · S22 · S23 · S24 · S25 · S26 · S27

  Compatible with: strategy_engine.py, ema9_strategies.py,
                   opening_drive.py, bot_upgrade.py
=============================================================
"""

import math
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, date, time as dtime, timedelta
import pytz

# ── pandas-ta optional ────────────────────────────────────────
try:
    import pandas_ta as ta
    USE_PTA = True
except ImportError:
    USE_PTA = False


# ══════════════════════════════════════════════════════════════
#  DATA CLASSES
# ══════════════════════════════════════════════════════════════

@dataclass
class SRLevel:
    """Represents one support or resistance level."""
    price:            float
    level_type:       str      # swing_high / swing_low / pivot_pp / pivot_r1 /
                               # pivot_r2 / pivot_s1 / pivot_s2 / pdh / pdl /
                               # round / orb_high / orb_low
    direction:        str      # support / resistance / both
    touches:          int      # how many times price touched this level
    strength_score:   int = 0  # confluence score 0–30+
    last_touch_idx:   int = 0  # bar index of last touch
    avg_volume_touch: float = 0.0  # avg volume at touch bars
    is_fresh:         bool = True  # not yet tested in current session
    is_premium:       bool = False # strength_score >= 7


@dataclass
class SRSignal:
    """Signal compatible with all existing bot signal types."""
    strategy_id:       str
    strategy_name:     str
    category:          str        # SR_BOUNCE / SR_BREAKOUT / PIVOT / ROUND / PDH_PDL
    direction:         str        # bullish / bearish
    ticker:            str
    price:             float
    entry:             float
    stop:              float
    t1:                float
    t2:                float
    t3:                float       # optional third target
    rr:                float
    confidence:        int
    conditions_met:    list
    conditions_missed: list
    score:             int
    max_score:         int
    sr_level_price:    float       # the S/R level that triggered
    sr_level_type:     str         # type of level
    sr_level_strength: int         # confluence score
    sr_level_touches:  int         # times tested
    premium_setup:     bool = False
    alert_text:        str  = ""
    options_guide:     dict = field(default_factory=dict)
    data:              dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
#  INDICATOR HELPERS (shared)
# ══════════════════════════════════════════════════════════════

def _ema(s, span): return s.ewm(span=span, adjust=False).mean()
def _rsi(s, p=14):
    d=s.diff(); g=d.clip(lower=0).ewm(alpha=1/p,adjust=False).mean()
    l=(-d.clip(upper=0)).ewm(alpha=1/p,adjust=False).mean()
    return 100-(100/(1+g/(l+1e-10)))
def _atr(df, p=14):
    hl=df["High"]-df["Low"]
    hpc=(df["High"]-df["Close"].shift()).abs()
    lpc=(df["Low"]-df["Close"].shift()).abs()
    return pd.concat([hl,hpc,lpc],axis=1).max(axis=1).rolling(p).mean()
def _vwap(df):
    df=df.copy(); df["_dt"]=df.index.date
    tp=(df["High"]+df["Low"]+df["Close"])/3; tpv=tp*df["Volume"]
    return tpv.groupby(df["_dt"]).cumsum()/df["Volume"].groupby(df["_dt"]).cumsum()
def _vol_ma(s, p=20): return s.rolling(p).mean()
def _get(df, col, idx=-1, default=None):
    try:
        v=df[col].iloc[idx]
        return float(v) if not (isinstance(v,float) and math.isnan(v)) else default
    except: return default

def prepare_sr_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA9"]   = _ema(df["Close"], 9)
    df["EMA21"]  = _ema(df["Close"], 21)
    df["EMA50"]  = _ema(df["Close"], 50)
    df["ATR14"]  = _atr(df, 14)
    df["RSI14"]  = _rsi(df["Close"], 14)
    df["VWAP"]   = _vwap(df)
    df["VOL_MA"] = _vol_ma(df["Volume"], 20)
    df["REL_VOL"]= df["Volume"] / (df["VOL_MA"] + 1e-10)
    df["EMA9_SLOPE"] = df["EMA9"] - df["EMA9"].shift(3)
    # Stochastic
    lo = df["Low"].rolling(5).min()
    hi = df["High"].rolling(5).max()
    rk = 100*(df["Close"]-lo)/(hi-lo+1e-10)
    df["STC_K"] = rk.rolling(3).mean()
    df["STC_D"] = df["STC_K"].rolling(3).mean()
    return df


# ══════════════════════════════════════════════════════════════
#  SR LEVEL DETECTOR
# ══════════════════════════════════════════════════════════════

class SRLevelDetector:
    """Detects all support and resistance levels from OHLCV data."""

    def find_swing_levels(self, df: pd.DataFrame,
                          window: int = 5,
                          min_touches: int = 2,
                          lookback: int = 100,
                          tolerance_pct: float = 0.003) -> list:
        """
        Find swing highs (resistance) and swing lows (support)
        from the last `lookback` bars with at least `min_touches` touches.
        """
        df_sub = df.tail(lookback).copy()
        n = len(df_sub)
        if n < window * 2 + 1:
            return []

        highs = []  # (bar_index, price)
        lows  = []

        for i in range(window, n - window):
            bar_h = float(df_sub["High"].iloc[i])
            bar_l = float(df_sub["Low"].iloc[i])
            window_h = df_sub["High"].iloc[i-window:i+window+1]
            window_l = df_sub["Low"].iloc[i-window:i+window+1]
            if bar_h == window_h.max():
                highs.append((i, bar_h))
            if bar_l == window_l.min():
                lows.append((i, bar_l))

        def cluster(raw, direction):
            clusters = []
            for idx, px in sorted(raw, key=lambda x: x[1]):
                matched = next(
                    (c for c in clusters
                     if abs(c["price"] - px) / (px + 1e-10) < tolerance_pct),
                    None,
                )
                if matched:
                    matched["prices"].append(px)
                    matched["indices"].append(idx)
                    matched["price"] = sum(matched["prices"]) / len(matched["prices"])
                else:
                    clusters.append({"price": px, "prices": [px],
                                      "indices": [idx], "direction": direction})
            return clusters

        result = []
        for c in cluster(highs, "resistance") + cluster(lows, "support"):
            lp = c["price"]
            touches = 0
            vol_sum = 0.0
            for i in range(n):
                hi = float(df_sub["High"].iloc[i])
                lo = float(df_sub["Low"].iloc[i])
                if lo <= lp * (1 + tolerance_pct) and hi >= lp * (1 - tolerance_pct):
                    touches += 1
                    if "Volume" in df_sub.columns:
                        vol_sum += float(df_sub["Volume"].iloc[i])
            if touches < min_touches:
                continue
            last_idx = max(c["indices"])
            # is_fresh: not touched in last 5 bars
            fresh = True
            for i in range(max(0, n - 5), n):
                hi = float(df_sub["High"].iloc[i])
                lo = float(df_sub["Low"].iloc[i])
                if lo <= lp * (1 + tolerance_pct) and hi >= lp * (1 - tolerance_pct):
                    fresh = False
                    break
            ltype = "swing_high" if c["direction"] == "resistance" else "swing_low"
            result.append(SRLevel(
                price=round(lp, 2),
                level_type=ltype,
                direction=c["direction"],
                touches=touches,
                last_touch_idx=n - 1 - last_idx,
                avg_volume_touch=vol_sum / max(touches, 1),
                is_fresh=fresh,
            ))
        return sorted(result, key=lambda l: l.price)

    def find_pivot_points(self, prior_high: float,
                          prior_low: float,
                          prior_close: float) -> dict:
        """
        Calculate standard daily pivot points from prior session.
        """
        pp = (prior_high + prior_low + prior_close) / 3
        r1 = 2*pp - prior_low
        r2 = pp + (prior_high - prior_low)
        r3 = prior_high + 2*(pp - prior_low)
        s1 = 2*pp - prior_high
        s2 = pp - (prior_high - prior_low)
        s3 = prior_low - 2*(prior_high - pp)
        return {
            "PP": SRLevel(round(pp,2), "pivot_pp",  "both",       1),
            "R1": SRLevel(round(r1,2), "pivot_r1",  "resistance", 1),
            "R2": SRLevel(round(r2,2), "pivot_r2",  "resistance", 1),
            "R3": SRLevel(round(r3,2), "pivot_r3",  "resistance", 1),
            "S1": SRLevel(round(s1,2), "pivot_s1",  "support",    1),
            "S2": SRLevel(round(s2,2), "pivot_s2",  "support",    1),
            "S3": SRLevel(round(s3,2), "pivot_s3",  "support",    1),
        }

    def find_pdh_pdl(self, prior_session_df: pd.DataFrame) -> dict:
        """
        Prior day high and low — strongest standalone S/R levels.
        Returns: {"pdh": SRLevel, "pdl": SRLevel}
        """
        if prior_session_df is None or prior_session_df.empty:
            return {}
        pdh = float(prior_session_df["High"].max())
        pdl = float(prior_session_df["Low"].min())
        return {
            "pdh": SRLevel(round(pdh,2), "pdh", "resistance", 1, is_fresh=True),
            "pdl": SRLevel(round(pdl,2), "pdl", "support",    1, is_fresh=True),
        }

    def find_round_levels(self, price: float, atr: float,
                          window_pct: float = 0.005) -> list:
        """
        Find round number levels near current price.
        Detects: whole dollars, half dollars, $25/$50 for high-price stocks.
        """
        levels = []
        # Whole dollar and half dollar
        for mult in range(int(price*2)-10, int(price*2)+11):
            candidate = mult / 2.0
            if abs(candidate - price) / price < window_pct:
                is_whole  = candidate == int(candidate)
                is_major  = (candidate % 10 == 0 or  # $X0 multiples
                             candidate % 25 == 0)     # $X25/$X50/$X75
                strength  = 3 if is_major else (2 if is_whole else 1)
                ltype     = "round_major" if is_major else "round_minor"
                direction = "resistance" if candidate > price else "support"
                levels.append(SRLevel(round(candidate,2), ltype,
                                      direction, 0, strength_score=strength))
        # For stocks > $200: $25 and $50 levels
        if price > 200:
            for mult in range(int(price/25)-3, int(price/25)+4):
                candidate = float(mult * 25)
                if abs(candidate - price) / price < window_pct:
                    direction = "resistance" if candidate > price else "support"
                    levels.append(SRLevel(round(candidate,2), "round_major",
                                          direction, 0, strength_score=4))
        return levels

    def find_orb_levels(self, df: pd.DataFrame,
                         orb_minutes: int = 30) -> dict:
        """
        Opening Range Breakout levels from first N minutes.
        Returns: {"orb_high": SRLevel, "orb_low": SRLevel, "orb_mid": SRLevel}
        """
        try:
            tz = pytz.timezone("America/New_York")
            if df.index.tzinfo is None:
                df = df.copy()
                df.index = df.index.tz_localize("UTC").tz_convert(tz)
            else:
                df = df.copy()
                df.index = df.index.tz_convert(tz)
        except Exception:
            pass

        session_open = dtime(9, 30)
        orb_end = dtime(9, 30 + orb_minutes) if orb_minutes <= 30 else dtime(10, 0)
        orb_mask = (df.index.time >= session_open) & (df.index.time < orb_end)
        orb_df   = df[orb_mask]

        if orb_df.empty:
            return {}

        orb_h = float(orb_df["High"].max())
        orb_l = float(orb_df["Low"].min())
        orb_m = (orb_h + orb_l) / 2

        return {
            "orb_high": SRLevel(round(orb_h,2), "orb_high", "resistance", 1),
            "orb_low":  SRLevel(round(orb_l,2), "orb_low",  "support",    1),
            "orb_mid":  SRLevel(round(orb_m,2), "orb_mid",  "both",       1),
        }


# ══════════════════════════════════════════════════════════════
#  SR CONFLUENCE ENGINE
# ══════════════════════════════════════════════════════════════

class SRConfluenceEngine:
    """Scores each S/R level based on confluence with other level types."""

    PREMIUM_THRESHOLD = 7

    def score_level(self, level: SRLevel,
                    all_levels: list,
                    vwap: float = None,
                    ema9: float = None,
                    ema21: float = None,
                    price: float = None) -> int:
        """
        Calculate confluence score for a level.
        More S/R types aligning = higher score = higher confidence.
        """
        score     = 0
        tolerance = 0.003   # 0.3% = "same zone"

        # Own level quality
        if level.touches >= 3:   score += 3
        elif level.touches >= 2: score += 1

        # Level type bonuses
        type_scores = {
            "pdh": 3, "pdl": 3,
            "pivot_r1": 2, "pivot_s1": 2,
            "pivot_pp": 2, "pivot_r2": 1, "pivot_s2": 1,
            "round_major": 2, "round_minor": 1,
            "orb_high": 1, "orb_low": 1,
        }
        score += type_scores.get(level.level_type, 0)

        # Volume at prior touch
        if level.avg_volume_touch > 0:
            score += 2

        # Check for other level types in the same zone
        nearby_types = set()
        for other in all_levels:
            if other is level: continue
            if abs(other.price - level.price) / (level.price + 1e-10) < tolerance:
                nearby_types.add(other.level_type)
                score += type_scores.get(other.level_type, 0) // 2

        # VWAP nearby
        if vwap and abs(vwap - level.price) / level.price < 0.005:
            score += 2

        # EMA nearby
        for ema in [ema9, ema21]:
            if ema and abs(ema - level.price) / level.price < 0.003:
                score += 1

        # Triple confluence bonus
        all_nearby = nearby_types | {level.level_type}
        if len(all_nearby) >= 3:
            score += 5   # PREMIUM

        level.strength_score = score
        level.is_premium     = score >= self.PREMIUM_THRESHOLD
        return score


# ══════════════════════════════════════════════════════════════
#  CANDLE CHECKER
# ══════════════════════════════════════════════════════════════

class SRCandleChecker:
    """Validates candle patterns for S/R bounce and breakout entries."""

    def is_bullish_rejection(self, df: pd.DataFrame) -> tuple:
        """Returns (is_valid, pattern_name)."""
        if len(df) < 2: return False, "none"
        cur = df.iloc[-1]; prv = df.iloc[-2]
        o,h,l,cl = float(cur["Open"]),float(cur["High"]),float(cur["Low"]),float(cur["Close"])
        po,ph,pl,pc = float(prv["Open"]),float(prv["High"]),float(prv["Low"]),float(prv["Close"])
        body = abs(cl-o); rng = h-l if h-l>0 else 1e-10
        lw   = min(o,cl)-l; uw = h-max(o,cl)

        # Hammer
        if lw > body*1.5 and uw < body*0.5:
            return True, "hammer"
        # Bullish engulfing
        if cl > po and o < pc and cl > o:
            return True, "bullish_engulfing"
        # Pin bar
        if lw/rng > 0.60 and body/rng < 0.25:
            return True, "bullish_pin_bar"
        # General bullish
        if cl > o and body/rng > 0.50:
            return True, "bullish_bar"
        return False, "none"

    def is_bearish_rejection(self, df: pd.DataFrame) -> tuple:
        """Returns (is_valid, pattern_name)."""
        if len(df) < 2: return False, "none"
        cur = df.iloc[-1]; prv = df.iloc[-2]
        o,h,l,cl = float(cur["Open"]),float(cur["High"]),float(cur["Low"]),float(cur["Close"])
        po,ph,pl,pc = float(prv["Open"]),float(prv["High"]),float(prv["Low"]),float(prv["Close"])
        body = abs(cl-o); rng = h-l if h-l>0 else 1e-10
        lw   = min(o,cl)-l; uw = h-max(o,cl)

        # Shooting star
        if uw > body*1.5 and lw < body*0.5:
            return True, "shooting_star"
        # Bearish engulfing
        if cl < po and o > pc and cl < o:
            return True, "bearish_engulfing"
        # Pin bar
        if uw/rng > 0.60 and body/rng < 0.25:
            return True, "bearish_pin_bar"
        # General bearish
        if cl < o and body/rng > 0.50:
            return True, "bearish_bar"
        return False, "none"

    def is_breakout_candle(self, df: pd.DataFrame,
                            direction: str, level: float) -> bool:
        cur = df.iloc[-1]
        o,h,l,cl = float(cur["Open"]),float(cur["High"]),float(cur["Low"]),float(cur["Close"])
        body = abs(cl-o); rng = h-l if h-l>0 else 1e-10
        if direction == "bullish":
            return cl > level and body/rng > 0.55 and cl > o
        else:
            return cl < level and body/rng > 0.55 and cl < o

    def is_retest_hold(self, df: pd.DataFrame,
                        level: float, direction: str,
                        tolerance_pct: float = 0.0035) -> bool:
        cur = df.iloc[-1]
        o,h,l,cl = float(cur["Open"]),float(cur["High"]),float(cur["Low"]),float(cur["Close"])
        # Touched the level
        touched = (l <= level*(1+tolerance_pct) and h >= level*(1-tolerance_pct))
        if direction == "bullish":
            return touched and cl > level   # held above
        else:
            return touched and cl < level   # held below


# ══════════════════════════════════════════════════════════════
#  BASE STRATEGY
# ══════════════════════════════════════════════════════════════

class BaseSRStrategy:
    ID       = "SXX"
    NAME     = "Base S/R Strategy"
    CATEGORY = "SR"

    def check(self, ticker, df, sr_levels, pivot_levels,
              pdh_pdl, round_levels, orb_levels) -> Optional[SRSignal]:
        raise NotImplementedError

    def _risk(self, price, direction, atr, stop_mult=1.0,
              t1_mult=1.5, t2_mult=2.5, t3_mult=4.0):
        atr = atr if atr and atr > 0 else price * 0.005
        if direction == "bullish":
            stop = round(price - atr*stop_mult, 2)
            t1   = round(price + atr*t1_mult,   2)
            t2   = round(price + atr*t2_mult,   2)
            t3   = round(price + atr*t3_mult,   2)
        else:
            stop = round(price + atr*stop_mult, 2)
            t1   = round(price - atr*t1_mult,   2)
            t2   = round(price - atr*t2_mult,   2)
            t3   = round(price - atr*t3_mult,   2)
        risk   = abs(price - stop)
        reward = abs(t1 - price)
        rr     = round(reward/risk, 2) if risk > 0 else 0
        return stop, t1, t2, t3, rr

    def _options_guide(self, direction, entry, stop, t1, t2, level):
        if direction == "bullish":
            return {
                "type":   "Long Call",
                "strike": "ATM or 1-strike ITM",
                "expiry": "0DTE (scalp) or next-day (intraday hold)",
                "entry":  f"Bounce candle close @ ${entry} above ${level}",
                "exit":   f"T1 ${t1} (50%) → T2 ${t2} (50%)",
                "stop":   f"Close below ${level} on 5-min bar",
                "delta":  "0.50–0.65",
            }
        else:
            return {
                "type":   "Long Put",
                "strike": "ATM or 1-strike ITM",
                "expiry": "0DTE (scalp) or next-day (intraday hold)",
                "entry":  f"Rejection candle close @ ${entry} below ${level}",
                "exit":   f"T1 ${t1} (50%) → T2 ${t2} (50%)",
                "stop":   f"Close above ${level} on 5-min bar",
                "delta":  "0.50–0.65",
            }

    def _format_alert(self, ticker, direction, entry, stop,
                      t1, t2, t3, rr, conf, level, met, options,
                      premium=False):
        emoji = "🟢" if direction=="bullish" else "🔴"
        sep   = "═"*52
        conds = "\n".join(f"   ✅ {c}" for c in met)
        prem  = "\n  ⭐ PREMIUM SETUP — Triple S/R Confluence!" if premium else ""
        return (f"\n{sep}\n  {emoji}  [{self.ID}] {self.NAME} — {ticker}{prem}\n{sep}\n"
                f"  S/R Level: ${round(level.price,2):>8}  ({level.level_type.replace('_',' ').title()})\n"
                f"  Touches:   {level.touches}x          Strength: {level.strength_score}/10\n\n"
                f"  Entry: ${entry}    Stop:  ${stop}\n"
                f"  T1:    ${t1}    T2:    ${t2}\n"
                f"  R:R:   1:{rr}       Conf:  {conf}/100\n\n"
                f"  🎯 {options['type']} | {options['expiry']}\n"
                f"  Conditions ({len(met)}):\n{conds}\n"
                f"  ⚠  Educational only\n{sep}")


# ══════════════════════════════════════════════════════════════
#  STRATEGY S20 — SUPPORT BOUNCE LONG
# ══════════════════════════════════════════════════════════════

class SupportBounceLong(BaseSRStrategy):
    ID       = "S20"
    NAME     = "Support Bounce Long"
    CATEGORY = "SR_BOUNCE"

    def check(self, ticker, df, sr_levels, pivot_levels,
              pdh_pdl, round_levels, orb_levels):
        # Need at least 2 bars and a price
        if len(df) < 2:
            return None

        price = _get(df, "Close")
        if price is None:
            return None

        # Find best support level near price
        support_levels = [l for l in sr_levels if l.direction == "support"]
        if not support_levels:
            return None

        # Find closest support level within 0.5%
        nearby = [l for l in support_levels
                  if abs(l.price - price) / price < 0.005]
        if not nearby:
            return None

        best_support = min(nearby, key=lambda l: abs(l.price - price))
        level = best_support
        lp = level.price

        # Gather indicators
        atr     = _get(df, "ATR14") or price * 0.005
        rsi     = _get(df, "RSI14")
        vwap    = _get(df, "VWAP")
        rel_vol = _get(df, "REL_VOL")
        ema9    = _get(df, "EMA9")
        ema9_slope = _get(df, "EMA9_SLOPE")
        stc_k   = _get(df, "STC_K")
        stc_k_prev = _get(df, "STC_K", idx=-2)

        candle_checker = SRCandleChecker()
        is_bull_candle, pattern = candle_checker.is_bullish_rejection(df)

        met    = []
        missed = []

        # Condition 1: support level tested 2+ times
        c1 = level.touches >= 2
        if c1: met.append(f"Support level ${lp} tested {level.touches}x")
        else:  missed.append(f"Support level touches < 2 (got {level.touches})")

        # Condition 2: price within 0.25% of support
        c2 = abs(price - lp) / price < 0.0025
        if c2: met.append(f"Price within 0.25% of support (${lp})")
        else:  missed.append(f"Price not close enough to support (${lp})")

        # Condition 3: bullish rejection candle
        c3 = is_bull_candle
        if c3: met.append(f"Bullish {pattern} candle")
        else:  missed.append("No bullish rejection candle")

        # Condition 4: RSI 28–58
        c4 = rsi is not None and 28 <= rsi <= 58
        if c4: met.append(f"RSI {rsi:.1f} in bounce range")
        else:  missed.append(f"RSI {rsi:.1f if rsi else 'N/A'} out of range 28–58")

        # Condition 5: REL_VOL >= 1.2
        c5 = rel_vol is not None and rel_vol >= 1.2
        if c5: met.append(f"Volume {rel_vol:.1f}x avg on rejection bar")
        else:  missed.append(f"REL_VOL {rel_vol:.1f if rel_vol else 'N/A'} < 1.2")

        # Condition 6: EMA9_SLOPE > -0.15 * ATR14
        c6 = ema9_slope is not None and ema9_slope > -0.15 * atr
        if c6: met.append("EMA9 slope — not steep downtrend")
        else:  missed.append("EMA9 slope too negative (steep downtrend)")

        # Condition 7: price >= vwap * 0.998
        c7 = vwap is not None and price >= vwap * 0.998
        if c7: met.append("Price at/above VWAP")
        else:  missed.append("Price below VWAP")

        # Condition 8: STC_K < 40 and turning up
        c8 = (stc_k is not None and stc_k_prev is not None
              and stc_k < 40 and stc_k > stc_k_prev)
        if c8: met.append("Stochastic turning up from low")
        else:  missed.append("Stochastic not turning up from oversold")

        score = sum([c1, c2, c3, c4, c5, c6, c7, c8])
        if score < 5:
            return None

        # Confluence bonus
        conf_bonus = 0
        if level.touches >= 3: conf_bonus += 15
        elif level.touches >= 2: conf_bonus += 5
        # Check PDL within 0.3% of level
        if any(abs(l.price - lp) / lp < 0.003 for l in pdh_pdl.values()
               if l.level_type == "pdl"):
            conf_bonus += 10
        # Check pivot S1/S2 within 0.3%
        if any(abs(pl.price - lp) / lp < 0.003 for k, pl in pivot_levels.items()
               if k in ("S1", "S2")):
            conf_bonus += 10
        # Round number within 0.3%
        if any(abs(rl.price - lp) / lp < 0.003 for rl in round_levels):
            conf_bonus += 10
        if level.avg_volume_touch > 0: conf_bonus += 5
        if vwap and abs(vwap - lp) / lp < 0.005: conf_bonus += 5

        confidence = min(95, 50 + int(score / 8 * 35) + conf_bonus)

        stop, t1, t2, t3, rr = self._risk(price, "bullish", atr,
                                           stop_mult=1.0, t1_mult=1.5,
                                           t2_mult=2.5, t3_mult=4.0)
        premium = level.is_premium or level.strength_score >= 7
        options = self._options_guide("bullish", price, stop, t1, t2, lp)
        alert   = self._format_alert(ticker, "bullish", price, stop,
                                     t1, t2, t3, rr, confidence,
                                     level, met, options, premium)

        return SRSignal(
            strategy_id=self.ID,
            strategy_name=self.NAME,
            category=self.CATEGORY,
            direction="bullish",
            ticker=ticker,
            price=price,
            entry=price,
            stop=stop, t1=t1, t2=t2, t3=t3, rr=rr,
            confidence=confidence,
            conditions_met=met,
            conditions_missed=missed,
            score=score,
            max_score=8,
            sr_level_price=lp,
            sr_level_type=level.level_type,
            sr_level_strength=level.strength_score,
            sr_level_touches=level.touches,
            premium_setup=premium,
            alert_text=alert,
            options_guide=options,
        )


# ══════════════════════════════════════════════════════════════
#  STRATEGY S21 — RESISTANCE REJECTION SHORT
# ══════════════════════════════════════════════════════════════

class ResistanceBounceShort(BaseSRStrategy):
    ID       = "S21"
    NAME     = "Resistance Rejection Short"
    CATEGORY = "SR_BOUNCE"

    def check(self, ticker, df, sr_levels, pivot_levels,
              pdh_pdl, round_levels, orb_levels):
        if len(df) < 2:
            return None

        price = _get(df, "Close")
        if price is None:
            return None

        # Find best resistance level near price
        resist_levels = [l for l in sr_levels if l.direction == "resistance"]
        if not resist_levels:
            return None

        nearby = [l for l in resist_levels
                  if abs(l.price - price) / price < 0.005]
        if not nearby:
            return None

        best_resistance = min(nearby, key=lambda l: abs(l.price - price))
        level = best_resistance
        lp = level.price

        # Gather indicators
        atr        = _get(df, "ATR14") or price * 0.005
        rsi        = _get(df, "RSI14")
        vwap       = _get(df, "VWAP")
        rel_vol    = _get(df, "REL_VOL")
        ema9_slope = _get(df, "EMA9_SLOPE")
        stc_k      = _get(df, "STC_K")
        stc_k_prev = _get(df, "STC_K", idx=-2)

        candle_checker = SRCandleChecker()
        is_bear_candle, pattern = candle_checker.is_bearish_rejection(df)

        met    = []
        missed = []

        # Condition 1: resistance level tested 2+ times
        c1 = level.touches >= 2
        if c1: met.append(f"Resistance level tested {level.touches}x")
        else:  missed.append(f"Resistance level touches < 2 (got {level.touches})")

        # Condition 2: price within 0.25% of resistance (from below)
        c2 = abs(price - lp) / price < 0.0025 and price <= lp * 1.0025
        if c2: met.append(f"Price within 0.25% of resistance (${lp})")
        else:  missed.append(f"Price not close enough to resistance (${lp})")

        # Condition 3: bearish rejection candle
        c3 = is_bear_candle
        if c3: met.append(f"Bearish {pattern} candle")
        else:  missed.append("No bearish rejection candle")

        # Condition 4: RSI 42–72
        c4 = rsi is not None and 42 <= rsi <= 72
        if c4: met.append(f"RSI {rsi:.1f} in rejection range")
        else:  missed.append(f"RSI {rsi:.1f if rsi else 'N/A'} out of range 42–72")

        # Condition 5: REL_VOL >= 1.2
        c5 = rel_vol is not None and rel_vol >= 1.2
        if c5: met.append(f"Volume {rel_vol:.1f}x avg on rejection bar")
        else:  missed.append(f"REL_VOL {rel_vol:.1f if rel_vol else 'N/A'} < 1.2")

        # Condition 6: EMA9_SLOPE < 0.15 * ATR (not steep uptrend)
        c6 = ema9_slope is not None and ema9_slope < 0.15 * atr
        if c6: met.append("EMA9 — not steep uptrend")
        else:  missed.append("EMA9 slope too positive (steep uptrend)")

        # Condition 7: price <= vwap * 1.002
        c7 = vwap is not None and price <= vwap * 1.002
        if c7: met.append("Price at/below VWAP")
        else:  missed.append("Price above VWAP")

        # Condition 8: STC_K > 60 and turning down
        c8 = (stc_k is not None and stc_k_prev is not None
              and stc_k > 60 and stc_k < stc_k_prev)
        if c8: met.append("Stochastic turning down from high")
        else:  missed.append("Stochastic not turning down from overbought")

        score = sum([c1, c2, c3, c4, c5, c6, c7, c8])
        if score < 5:
            return None

        # Confluence bonus
        conf_bonus = 0
        if level.touches >= 3: conf_bonus += 15
        elif level.touches >= 2: conf_bonus += 5
        # PDH within 0.3%
        if any(abs(l.price - lp) / lp < 0.003 for l in pdh_pdl.values()
               if l.level_type == "pdh"):
            conf_bonus += 10
        # Pivot R1/R2 within 0.3%
        if any(abs(pl.price - lp) / lp < 0.003 for k, pl in pivot_levels.items()
               if k in ("R1", "R2")):
            conf_bonus += 10
        # Round number within 0.3%
        if any(abs(rl.price - lp) / lp < 0.003 for rl in round_levels):
            conf_bonus += 10
        if level.avg_volume_touch > 0: conf_bonus += 5
        if vwap and abs(vwap - lp) / lp < 0.005: conf_bonus += 5

        confidence = min(95, 50 + int(score / 8 * 35) + conf_bonus)

        stop, t1, t2, t3, rr = self._risk(price, "bearish", atr,
                                           stop_mult=1.0, t1_mult=1.5,
                                           t2_mult=2.5, t3_mult=4.0)
        premium = level.is_premium or level.strength_score >= 7
        options = self._options_guide("bearish", price, stop, t1, t2, lp)
        alert   = self._format_alert(ticker, "bearish", price, stop,
                                     t1, t2, t3, rr, confidence,
                                     level, met, options, premium)

        return SRSignal(
            strategy_id=self.ID,
            strategy_name=self.NAME,
            category=self.CATEGORY,
            direction="bearish",
            ticker=ticker,
            price=price,
            entry=price,
            stop=stop, t1=t1, t2=t2, t3=t3, rr=rr,
            confidence=confidence,
            conditions_met=met,
            conditions_missed=missed,
            score=score,
            max_score=8,
            sr_level_price=lp,
            sr_level_type=level.level_type,
            sr_level_strength=level.strength_score,
            sr_level_touches=level.touches,
            premium_setup=premium,
            alert_text=alert,
            options_guide=options,
        )


# ══════════════════════════════════════════════════════════════
#  STRATEGY S22 — S/R BREAKOUT LONG
# ══════════════════════════════════════════════════════════════

class SRBreakoutLong(BaseSRStrategy):
    ID       = "S22"
    NAME     = "S/R Breakout Long"
    CATEGORY = "SR_BREAKOUT"

    def check(self, ticker, df, sr_levels, pivot_levels,
              pdh_pdl, round_levels, orb_levels):
        if len(df) < 3:
            return None

        price = _get(df, "Close")
        if price is None:
            return None

        # Find resistance levels
        resist_levels = [l for l in sr_levels if l.direction == "resistance"]
        if not resist_levels:
            return None

        atr        = _get(df, "ATR14") or price * 0.005
        rsi        = _get(df, "RSI14")
        vwap       = _get(df, "VWAP")
        rel_vol    = _get(df, "REL_VOL")
        rel_vol_prev = _get(df, "REL_VOL", idx=-2)

        # Prior bar values
        prior_close = _get(df, "Close", idx=-2)
        prior_open  = _get(df, "Open",  idx=-2)
        prior_high  = _get(df, "High",  idx=-2)
        prior_low   = _get(df, "Low",   idx=-2)

        if any(v is None for v in [prior_close, prior_open, prior_high, prior_low]):
            return None

        prior_body  = abs(prior_close - prior_open)
        prior_range = prior_high - prior_low if prior_high - prior_low > 0 else 1e-10

        # Try each resistance level as candidate
        best_signal = None
        best_conf   = 0

        for level in resist_levels:
            if level.touches < 2:
                continue
            lp = level.price

            # Condition 2: prior bar broke above resistance with strong bull candle
            c2 = (prior_close > lp
                  and prior_body / prior_range > 0.55
                  and prior_close > prior_open)

            if not c2:
                continue

            # Condition 3: current bar retesting — within 0.35% of broken level
            c3 = abs(price - lp) / price < 0.0035

            # Condition 4: retest hold — current close still above level
            c4 = price > lp

            met    = []
            missed = []

            # Condition 1: resistance level identified (touches >= 2)
            c1 = True
            met.append(f"Resistance level ${lp} identified ({level.touches} touches)")

            if c2: met.append("Prior bar broke above resistance with strong bull candle")
            else:  missed.append("Prior bar did not break resistance strongly")

            if c3: met.append(f"Retesting broken level (within 0.35% of ${lp})")
            else:  missed.append("Not close enough to retest level")

            if c4: met.append("Retest holding above broken level (support flip)")
            else:  missed.append("Close below broken level — no support flip")

            # Condition 5: REL_VOL on prior bar >= 1.5
            c5 = rel_vol_prev is not None and rel_vol_prev >= 1.5
            if c5: met.append("Volume 1.5x+ on breakout candle")
            else:  missed.append(f"Breakout bar REL_VOL {rel_vol_prev:.1f if rel_vol_prev else 'N/A'} < 1.5")

            # Condition 6: current bar volume < prior bar volume
            c6 = (rel_vol is not None and rel_vol_prev is not None
                  and rel_vol < rel_vol_prev)
            if c6: met.append("Lower volume on retest (healthy pullback)")
            else:  missed.append("Retest volume not lower than breakout volume")

            # Condition 7: RSI > 50 and < 75
            c7 = rsi is not None and 50 < rsi < 75
            if c7: met.append(f"RSI {rsi:.1f} in breakout range")
            else:  missed.append(f"RSI {rsi:.1f if rsi else 'N/A'} not in 50–75")

            # Condition 8: VWAP below price
            c8 = vwap is not None and vwap < price
            if c8: met.append("VWAP below price (trend alignment)")
            else:  missed.append("VWAP not below price")

            score = sum([c1, c2, c3, c4, c5, c6, c7, c8])
            if score < 5:
                continue

            # Need c2 and c4 to be a real breakout-retest
            if not (c2 and c4):
                continue

            conf_bonus = 0
            if level.touches >= 3: conf_bonus += 10
            if any(abs(pl.price - lp) / lp < 0.003 for k, pl in pivot_levels.items()
                   if k in ("R1", "R2")):
                conf_bonus += 10
            if any(abs(rl.price - lp) / lp < 0.003 for rl in round_levels):
                conf_bonus += 10

            confidence = min(95, 50 + int(score / 8 * 35) + conf_bonus)
            if confidence > best_conf:
                best_conf = confidence
                stop, t1, t2, t3, rr = self._risk(price, "bullish", atr,
                                                    stop_mult=0.5, t1_mult=1.5,
                                                    t2_mult=2.5, t3_mult=4.0)
                premium = level.is_premium or level.strength_score >= 7
                options = self._options_guide("bullish", price, stop, t1, t2, lp)
                alert   = self._format_alert(ticker, "bullish", price, stop,
                                             t1, t2, t3, rr, confidence,
                                             level, met, options, premium)
                best_signal = SRSignal(
                    strategy_id=self.ID,
                    strategy_name=self.NAME,
                    category=self.CATEGORY,
                    direction="bullish",
                    ticker=ticker,
                    price=price,
                    entry=price,
                    stop=stop, t1=t1, t2=t2, t3=t3, rr=rr,
                    confidence=confidence,
                    conditions_met=met,
                    conditions_missed=missed,
                    score=score,
                    max_score=8,
                    sr_level_price=lp,
                    sr_level_type=level.level_type,
                    sr_level_strength=level.strength_score,
                    sr_level_touches=level.touches,
                    premium_setup=premium,
                    alert_text=alert,
                    options_guide=options,
                )

        return best_signal


# ══════════════════════════════════════════════════════════════
#  STRATEGY S23 — S/R BREAKDOWN SHORT
# ══════════════════════════════════════════════════════════════

class SRBreakdownShort(BaseSRStrategy):
    ID       = "S23"
    NAME     = "S/R Breakdown Short"
    CATEGORY = "SR_BREAKOUT"

    def check(self, ticker, df, sr_levels, pivot_levels,
              pdh_pdl, round_levels, orb_levels):
        if len(df) < 3:
            return None

        price = _get(df, "Close")
        if price is None:
            return None

        # Find support levels
        support_levels = [l for l in sr_levels if l.direction == "support"]
        if not support_levels:
            return None

        atr          = _get(df, "ATR14") or price * 0.005
        rsi          = _get(df, "RSI14")
        vwap         = _get(df, "VWAP")
        rel_vol      = _get(df, "REL_VOL")
        rel_vol_prev = _get(df, "REL_VOL", idx=-2)

        # Prior bar values
        prior_close = _get(df, "Close", idx=-2)
        prior_open  = _get(df, "Open",  idx=-2)
        prior_high  = _get(df, "High",  idx=-2)
        prior_low   = _get(df, "Low",   idx=-2)

        if any(v is None for v in [prior_close, prior_open, prior_high, prior_low]):
            return None

        prior_body  = abs(prior_close - prior_open)
        prior_range = prior_high - prior_low if prior_high - prior_low > 0 else 1e-10

        best_signal = None
        best_conf   = 0

        for level in support_levels:
            if level.touches < 2:
                continue
            lp = level.price

            # Condition 2: prior bar broke below support with strong bear candle
            c2 = (prior_close < lp
                  and prior_body / prior_range > 0.55
                  and prior_close < prior_open)

            if not c2:
                continue

            # Condition 3: current bar retesting within 0.35% of broken level
            c3 = abs(price - lp) / price < 0.0035

            # Condition 4: retest fail — current close still below level
            c4 = price < lp

            met    = []
            missed = []

            c1 = True
            met.append(f"Support level ${lp} identified ({level.touches} touches)")

            if c2: met.append("Prior bar broke below support with strong bear candle")
            else:  missed.append("Prior bar did not break support strongly")

            if c3: met.append(f"Retesting broken level (within 0.35% of ${lp})")
            else:  missed.append("Not close enough to retest level")

            if c4: met.append("Retest failing below broken level (resistance flip)")
            else:  missed.append("Close above broken level — no resistance flip")

            # Condition 5: REL_VOL on prior bar >= 1.5
            c5 = rel_vol_prev is not None and rel_vol_prev >= 1.5
            if c5: met.append("Volume 1.5x+ on breakdown candle")
            else:  missed.append(f"Breakdown bar REL_VOL < 1.5")

            # Condition 6: current bar REL_VOL < prior bar REL_VOL
            c6 = (rel_vol is not None and rel_vol_prev is not None
                  and rel_vol < rel_vol_prev)
            if c6: met.append("Lower volume on retest (healthy pullback)")
            else:  missed.append("Retest volume not lower than breakdown volume")

            # Condition 7: RSI < 50 and > 25
            c7 = rsi is not None and 25 < rsi < 50
            if c7: met.append(f"RSI {rsi:.1f} in breakdown range")
            else:  missed.append(f"RSI {rsi:.1f if rsi else 'N/A'} not in 25–50")

            # Condition 8: VWAP > current price
            c8 = vwap is not None and vwap > price
            if c8: met.append("VWAP above price (bearish alignment)")
            else:  missed.append("VWAP not above price")

            score = sum([c1, c2, c3, c4, c5, c6, c7, c8])
            if score < 5:
                continue

            if not (c2 and c4):
                continue

            conf_bonus = 0
            if level.touches >= 3: conf_bonus += 10
            if any(abs(pl.price - lp) / lp < 0.003 for k, pl in pivot_levels.items()
                   if k in ("S1", "S2")):
                conf_bonus += 10
            if any(abs(rl.price - lp) / lp < 0.003 for rl in round_levels):
                conf_bonus += 10

            confidence = min(95, 50 + int(score / 8 * 35) + conf_bonus)
            if confidence > best_conf:
                best_conf = confidence
                stop, t1, t2, t3, rr = self._risk(price, "bearish", atr,
                                                    stop_mult=0.5, t1_mult=1.5,
                                                    t2_mult=2.5, t3_mult=4.0)
                premium = level.is_premium or level.strength_score >= 7
                options = self._options_guide("bearish", price, stop, t1, t2, lp)
                alert   = self._format_alert(ticker, "bearish", price, stop,
                                             t1, t2, t3, rr, confidence,
                                             level, met, options, premium)
                best_signal = SRSignal(
                    strategy_id=self.ID,
                    strategy_name=self.NAME,
                    category=self.CATEGORY,
                    direction="bearish",
                    ticker=ticker,
                    price=price,
                    entry=price,
                    stop=stop, t1=t1, t2=t2, t3=t3, rr=rr,
                    confidence=confidence,
                    conditions_met=met,
                    conditions_missed=missed,
                    score=score,
                    max_score=8,
                    sr_level_price=lp,
                    sr_level_type=level.level_type,
                    sr_level_strength=level.strength_score,
                    sr_level_touches=level.touches,
                    premium_setup=premium,
                    alert_text=alert,
                    options_guide=options,
                )

        return best_signal


# ══════════════════════════════════════════════════════════════
#  STRATEGY S24 — PIVOT POINT BOUNCE
# ══════════════════════════════════════════════════════════════

class PivotPointBounce(BaseSRStrategy):
    ID       = "S24"
    NAME     = "Pivot Point Bounce"
    CATEGORY = "PIVOT"

    def check(self, ticker, df, sr_levels, pivot_levels,
              pdh_pdl, round_levels, orb_levels):
        if not pivot_levels or len(df) < 2:
            return None

        price = _get(df, "Close")
        if price is None:
            return None

        pp_level = pivot_levels.get("PP")
        pp_price = pp_level.price if pp_level else None

        # Determine if price is above or below PP
        above_pp = (pp_price is not None and price > pp_price)

        # Support pivots: S1, S2 (and PP when below it)
        # Resistance pivots: R1, R2 (and PP when above it)
        if above_pp:
            candidates = [
                (k, pivot_levels[k]) for k in ("R1", "R2", "PP")
                if k in pivot_levels
            ]
        else:
            candidates = [
                (k, pivot_levels[k]) for k in ("S1", "S2", "PP")
                if k in pivot_levels
            ]

        atr        = _get(df, "ATR14") or price * 0.005
        rsi        = _get(df, "RSI14")
        vwap       = _get(df, "VWAP")
        rel_vol    = _get(df, "REL_VOL")
        ema9_slope = _get(df, "EMA9_SLOPE")
        stc_k      = _get(df, "STC_K")
        stc_k_prev = _get(df, "STC_K", idx=-2)

        candle_checker = SRCandleChecker()

        best_signal = None
        best_conf   = 0

        for level_name, level in candidates:
            lp = level.price

            # Only check if price is within 0.25% of this pivot
            if abs(price - lp) / price >= 0.0025:
                continue

            # Determine direction based on which pivot we're at
            if level_name in ("S1", "S2") or (level_name == "PP" and not above_pp):
                direction = "bullish"
                is_bull, pattern = candle_checker.is_bullish_rejection(df)
            else:
                direction = "bearish"
                is_bull, pattern = False, "none"

            if direction == "bearish":
                is_bear, pattern = candle_checker.is_bearish_rejection(df)
            else:
                is_bear = False

            met    = []
            missed = []

            # Condition 1: pivot level identified
            c1 = True
            met.append(f"Pivot {level_name} at ${lp}")

            # Condition 2: price within 0.25% of pivot
            c2 = True  # already checked above
            met.append(f"Price within 0.25% of {level_name} (${lp})")

            # Condition 3: rejection candle
            if direction == "bullish":
                c3 = is_bull
                if c3: met.append(f"Bullish {pattern} candle at pivot")
                else:  missed.append("No bullish rejection candle")
            else:
                c3 = is_bear
                if c3: met.append(f"Bearish {pattern} candle at pivot")
                else:  missed.append("No bearish rejection candle")

            # Condition 4: RSI in valid range
            if direction == "bullish":
                c4 = rsi is not None and 28 <= rsi <= 58
                if c4: met.append(f"RSI {rsi:.1f} in bounce range")
                else:  missed.append(f"RSI {rsi:.1f if rsi else 'N/A'} out of 28–58")
            else:
                c4 = rsi is not None and 42 <= rsi <= 72
                if c4: met.append(f"RSI {rsi:.1f} in rejection range")
                else:  missed.append(f"RSI {rsi:.1f if rsi else 'N/A'} out of 42–72")

            # Condition 5: REL_VOL >= 1.2
            c5 = rel_vol is not None and rel_vol >= 1.2
            if c5: met.append(f"Volume {rel_vol:.1f}x avg on rejection bar")
            else:  missed.append(f"REL_VOL < 1.2")

            # Condition 6: EMA9 slope aligned
            if direction == "bullish":
                c6 = ema9_slope is not None and ema9_slope > -0.15 * atr
                if c6: met.append("EMA9 slope — not steep downtrend")
                else:  missed.append("EMA9 slope too negative")
            else:
                c6 = ema9_slope is not None and ema9_slope < 0.15 * atr
                if c6: met.append("EMA9 — not steep uptrend")
                else:  missed.append("EMA9 slope too positive")

            # Condition 7: VWAP alignment
            if direction == "bullish":
                c7 = vwap is not None and price >= vwap * 0.998
                if c7: met.append("Price at/above VWAP")
                else:  missed.append("Price below VWAP")
            else:
                c7 = vwap is not None and price <= vwap * 1.002
                if c7: met.append("Price at/below VWAP")
                else:  missed.append("Price above VWAP")

            # Condition 8: Stochastic
            if direction == "bullish":
                c8 = (stc_k is not None and stc_k_prev is not None
                      and stc_k < 40 and stc_k > stc_k_prev)
                if c8: met.append("Stochastic turning up from low")
                else:  missed.append("Stochastic not turning up from oversold")
            else:
                c8 = (stc_k is not None and stc_k_prev is not None
                      and stc_k > 60 and stc_k < stc_k_prev)
                if c8: met.append("Stochastic turning down from high")
                else:  missed.append("Stochastic not turning down from overbought")

            score = sum([c1, c2, c3, c4, c5, c6, c7, c8])
            if score < 5:
                continue

            # Pivot-specific targets
            t1_price = None
            t2_price = None
            if direction == "bullish":
                if level_name == "S1":
                    t1_price = pivot_levels.get("PP", SRLevel(0,"","",0)).price or None
                    r1 = pivot_levels.get("R1")
                    t2_price = r1.price if r1 else None
                elif level_name == "S2":
                    s1 = pivot_levels.get("S1")
                    t1_price = s1.price if s1 else None
                    t2_price = pivot_levels.get("PP", SRLevel(0,"","",0)).price or None
                elif level_name == "PP":
                    r1 = pivot_levels.get("R1")
                    t1_price = r1.price if r1 else None
                    r2 = pivot_levels.get("R2")
                    t2_price = r2.price if r2 else None
            else:
                if level_name == "R1":
                    t1_price = pivot_levels.get("PP", SRLevel(0,"","",0)).price or None
                    s1 = pivot_levels.get("S1")
                    t2_price = s1.price if s1 else None
                elif level_name == "R2":
                    r1 = pivot_levels.get("R1")
                    t1_price = r1.price if r1 else None
                    t2_price = pivot_levels.get("PP", SRLevel(0,"","",0)).price or None
                elif level_name == "PP":
                    s1 = pivot_levels.get("S1")
                    t1_price = s1.price if s1 else None
                    s2 = pivot_levels.get("S2")
                    t2_price = s2.price if s2 else None

            # Validate pivot targets make sense directionally
            if direction == "bullish":
                if t1_price is not None and t1_price <= price:
                    t1_price = None
                if t2_price is not None and t2_price <= price:
                    t2_price = None
            else:
                if t1_price is not None and t1_price >= price:
                    t1_price = None
                if t2_price is not None and t2_price >= price:
                    t2_price = None

            # Fall back to ATR-based if pivot targets not valid
            stop_atr, t1_atr, t2_atr, t3_atr, rr_atr = self._risk(
                price, direction, atr,
                stop_mult=1.0, t1_mult=1.5, t2_mult=2.5, t3_mult=4.0
            )
            t1 = t1_price if t1_price else t1_atr
            t2 = t2_price if t2_price else t2_atr
            t3 = t3_atr
            stop = stop_atr

            risk  = abs(price - stop)
            rw    = abs(t1 - price)
            rr    = round(rw / risk, 2) if risk > 0 else 0

            # Confluence bonus
            conf_bonus = 0
            if any(abs(l.price - lp) / lp < 0.003 for l in pdh_pdl.values()):
                conf_bonus += 10
            if any(abs(rl.price - lp) / lp < 0.003 for rl in round_levels):
                conf_bonus += 10
            if any(abs(sl.price - lp) / lp < 0.003 for sl in sr_levels):
                conf_bonus += 5
            if vwap and abs(vwap - lp) / lp < 0.005: conf_bonus += 5

            # Base confidence = 55 (pivot levels are reliable)
            confidence = min(95, 55 + int(score / 8 * 30) + conf_bonus)
            if confidence > best_conf:
                best_conf = confidence
                premium = level.is_premium or level.strength_score >= 7
                options = self._options_guide(direction, price, stop, t1, t2, lp)
                alert   = self._format_alert(ticker, direction, price, stop,
                                             t1, t2, t3, rr, confidence,
                                             level, met, options, premium)
                best_signal = SRSignal(
                    strategy_id=self.ID,
                    strategy_name=self.NAME,
                    category=self.CATEGORY,
                    direction=direction,
                    ticker=ticker,
                    price=price,
                    entry=price,
                    stop=stop, t1=t1, t2=t2, t3=t3, rr=rr,
                    confidence=confidence,
                    conditions_met=met,
                    conditions_missed=missed,
                    score=score,
                    max_score=8,
                    sr_level_price=lp,
                    sr_level_type=level.level_type,
                    sr_level_strength=level.strength_score,
                    sr_level_touches=level.touches,
                    premium_setup=premium,
                    alert_text=alert,
                    options_guide=options,
                )

        return best_signal


# ══════════════════════════════════════════════════════════════
#  STRATEGY S25 — PIVOT POINT BREAKOUT
# ══════════════════════════════════════════════════════════════

class PivotPointBreakout(BaseSRStrategy):
    ID       = "S25"
    NAME     = "Pivot Point Breakout"
    CATEGORY = "PIVOT"

    def check(self, ticker, df, sr_levels, pivot_levels,
              pdh_pdl, round_levels, orb_levels):
        if not pivot_levels or len(df) < 3:
            return None

        price       = _get(df, "Close")
        prior_close = _get(df, "Close", idx=-2)
        if price is None or prior_close is None:
            return None

        atr     = _get(df, "ATR14") or price * 0.005
        rsi     = _get(df, "RSI14")
        vwap    = _get(df, "VWAP")
        rel_vol = _get(df, "REL_VOL")
        ema9    = _get(df, "EMA9")
        ema21   = _get(df, "EMA21")

        cur_o   = _get(df, "Open")
        cur_h   = _get(df, "High")
        cur_l   = _get(df, "Low")
        cur_body  = abs(price - cur_o) if cur_o else 0
        cur_range = (cur_h - cur_l) if (cur_h and cur_l and cur_h - cur_l > 0) else 1e-10

        best_signal = None
        best_conf   = 0

        # ── Bull: break above R1 ──────────────────────────────────
        r1_level = pivot_levels.get("R1")
        if r1_level:
            r1 = r1_level.price
            bull_break = (prior_close < r1 and price > r1
                          and cur_body / cur_range > 0.55)
            if bull_break:
                r2_level = pivot_levels.get("R2")
                r3_level = pivot_levels.get("R3")
                t1 = r2_level.price if r2_level else round(price + atr * 1.5, 2)
                t2 = r3_level.price if r3_level else round(price + atr * 2.5, 2)

                met    = []
                missed = []

                c1 = True
                met.append(f"Pivot R1 at ${r1} identified")

                c2 = cur_body / cur_range > 0.55
                if c2: met.append(f"Strong breakout candle (body/range {cur_body/cur_range:.0%})")
                else:  missed.append("Weak breakout candle")

                c3 = rel_vol is not None and rel_vol >= 2.0
                if c3: met.append(f"Volume {rel_vol:.1f}x confirms breakout")
                else:  missed.append(f"REL_VOL {rel_vol:.1f if rel_vol else 'N/A'} < 2.0")

                c4 = rsi is not None and rsi > 60
                if c4: met.append(f"RSI {rsi:.1f} above 60 (bullish momentum)")
                else:  missed.append(f"RSI {rsi:.1f if rsi else 'N/A'} < 60")

                c5 = vwap is not None and vwap < price
                if c5: met.append("VWAP below price (trend alignment)")
                else:  missed.append("VWAP not below price")

                c6 = ema9 is not None and ema21 is not None and ema9 > ema21
                if c6: met.append("EMA9 above EMA21 (uptrend)")
                else:  missed.append("EMA9 not above EMA21")

                score = sum([c1, c2, c3, c4, c5, c6])
                if score >= 4:
                    conf_bonus = 0
                    if any(abs(l.price - r1) / r1 < 0.003 for l in pdh_pdl.values()):
                        conf_bonus += 10
                    if any(abs(rl.price - r1) / r1 < 0.003 for rl in round_levels):
                        conf_bonus += 10

                    confidence = min(95, 60 + int(score / 6 * 25) + conf_bonus)

                    if confidence > best_conf:
                        best_conf = confidence
                        stop_atr, _, _, t3, _ = self._risk(price, "bullish", atr,
                                                            stop_mult=0.5,
                                                            t1_mult=1.5, t2_mult=2.5,
                                                            t3_mult=4.0)
                        stop = stop_atr
                        risk = abs(price - stop)
                        rw   = abs(t1 - price)
                        rr   = round(rw / risk, 2) if risk > 0 else 0

                        premium = r1_level.strength_score >= 7
                        options = self._options_guide("bullish", price, stop, t1, t2, r1)
                        alert   = self._format_alert(ticker, "bullish", price, stop,
                                                     t1, t2, t3, rr, confidence,
                                                     r1_level, met, options, premium)
                        best_signal = SRSignal(
                            strategy_id=self.ID,
                            strategy_name=self.NAME,
                            category=self.CATEGORY,
                            direction="bullish",
                            ticker=ticker,
                            price=price,
                            entry=price,
                            stop=stop, t1=t1, t2=t2, t3=t3, rr=rr,
                            confidence=confidence,
                            conditions_met=met,
                            conditions_missed=missed,
                            score=score,
                            max_score=6,
                            sr_level_price=r1,
                            sr_level_type=r1_level.level_type,
                            sr_level_strength=r1_level.strength_score,
                            sr_level_touches=r1_level.touches,
                            premium_setup=premium,
                            alert_text=alert,
                            options_guide=options,
                        )

        # ── Bear: break below S1 ──────────────────────────────────
        s1_level = pivot_levels.get("S1")
        if s1_level:
            s1 = s1_level.price
            bear_break = (prior_close > s1 and price < s1
                          and cur_body / cur_range > 0.55)
            if bear_break:
                s2_level = pivot_levels.get("S2")
                s3_level = pivot_levels.get("S3")
                t1 = s2_level.price if s2_level else round(price - atr * 1.5, 2)
                t2 = s3_level.price if s3_level else round(price - atr * 2.5, 2)

                met    = []
                missed = []

                c1 = True
                met.append(f"Pivot S1 at ${s1} identified")

                c2 = cur_body / cur_range > 0.55
                if c2: met.append(f"Strong breakdown candle (body/range {cur_body/cur_range:.0%})")
                else:  missed.append("Weak breakdown candle")

                c3 = rel_vol is not None and rel_vol >= 2.0
                if c3: met.append(f"Volume {rel_vol:.1f}x confirms breakdown")
                else:  missed.append(f"REL_VOL < 2.0")

                c4 = rsi is not None and rsi < 40
                if c4: met.append(f"RSI {rsi:.1f} below 40 (bearish momentum)")
                else:  missed.append(f"RSI {rsi:.1f if rsi else 'N/A'} > 40")

                c5 = vwap is not None and vwap > price
                if c5: met.append("VWAP above price (bearish alignment)")
                else:  missed.append("VWAP not above price")

                c6 = ema9 is not None and ema21 is not None and ema9 < ema21
                if c6: met.append("EMA9 below EMA21 (downtrend)")
                else:  missed.append("EMA9 not below EMA21")

                score = sum([c1, c2, c3, c4, c5, c6])
                if score >= 4:
                    conf_bonus = 0
                    if any(abs(l.price - s1) / s1 < 0.003 for l in pdh_pdl.values()):
                        conf_bonus += 10
                    if any(abs(rl.price - s1) / s1 < 0.003 for rl in round_levels):
                        conf_bonus += 10

                    confidence = min(95, 60 + int(score / 6 * 25) + conf_bonus)

                    if confidence > best_conf:
                        best_conf = confidence
                        stop_atr, _, _, t3, _ = self._risk(price, "bearish", atr,
                                                            stop_mult=0.5,
                                                            t1_mult=1.5, t2_mult=2.5,
                                                            t3_mult=4.0)
                        stop = stop_atr
                        risk = abs(price - stop)
                        rw   = abs(t1 - price)
                        rr   = round(rw / risk, 2) if risk > 0 else 0

                        premium = s1_level.strength_score >= 7
                        options = self._options_guide("bearish", price, stop, t1, t2, s1)
                        alert   = self._format_alert(ticker, "bearish", price, stop,
                                                     t1, t2, t3, rr, confidence,
                                                     s1_level, met, options, premium)
                        best_signal = SRSignal(
                            strategy_id=self.ID,
                            strategy_name=self.NAME,
                            category=self.CATEGORY,
                            direction="bearish",
                            ticker=ticker,
                            price=price,
                            entry=price,
                            stop=stop, t1=t1, t2=t2, t3=t3, rr=rr,
                            confidence=confidence,
                            conditions_met=met,
                            conditions_missed=missed,
                            score=score,
                            max_score=6,
                            sr_level_price=s1,
                            sr_level_type=s1_level.level_type,
                            sr_level_strength=s1_level.strength_score,
                            sr_level_touches=s1_level.touches,
                            premium_setup=premium,
                            alert_text=alert,
                            options_guide=options,
                        )

        return best_signal


# ══════════════════════════════════════════════════════════════
#  STRATEGY S26 — ROUND NUMBER S/R
# ══════════════════════════════════════════════════════════════

class RoundNumberSR(BaseSRStrategy):
    ID       = "S26"
    NAME     = "Round Number S/R"
    CATEGORY = "ROUND_NUMBER"

    def check(self, ticker, df, sr_levels, pivot_levels,
              pdh_pdl, round_levels, orb_levels):
        if not round_levels or len(df) < 2:
            return None

        price = _get(df, "Close")
        if price is None:
            return None

        # Find round level within 0.25% — prefer major
        nearby = [r for r in round_levels
                  if abs(r.price - price) / price < 0.0025]
        if not nearby:
            return None

        # Prefer round_major; among ties pick closest
        majors = [r for r in nearby if r.level_type == "round_major"]
        candidates = majors if majors else nearby
        level = min(candidates, key=lambda r: abs(r.price - price))
        lp = level.price

        # Only fire if round level has confluence with at least one other S/R type
        has_confluence = False
        tol = 0.003
        for sl in sr_levels:
            if abs(sl.price - lp) / lp < tol:
                has_confluence = True
                break
        if not has_confluence:
            for k, pl in pivot_levels.items():
                if abs(pl.price - lp) / lp < tol:
                    has_confluence = True
                    break
        if not has_confluence:
            for pdl in pdh_pdl.values():
                if abs(pdl.price - lp) / lp < tol:
                    has_confluence = True
                    break

        if not has_confluence:
            return None

        # Determine direction: approaching from below → long, from above → short
        # Use prior bar close to determine approach direction
        prior_close = _get(df, "Close", idx=-2)
        if prior_close is None:
            return None

        if prior_close < lp and price < lp:
            direction = "bullish"   # approaching support from below
        elif prior_close > lp and price > lp:
            direction = "bearish"   # approaching resistance from above
        else:
            # Price just crossed — skip (let breakout strategies handle)
            return None

        atr        = _get(df, "ATR14") or price * 0.005
        rsi        = _get(df, "RSI14")
        rel_vol    = _get(df, "REL_VOL")

        candle_checker = SRCandleChecker()
        if direction == "bullish":
            is_valid_candle, pattern = candle_checker.is_bullish_rejection(df)
        else:
            is_valid_candle, pattern = candle_checker.is_bearish_rejection(df)

        met    = []
        missed = []

        # Condition 1: price within 0.25% of round number
        c1 = True
        met.append(f"Price within 0.25% of round number ${lp}")

        # Condition 2: major round number
        c2 = level.level_type == "round_major"
        if c2: met.append(f"Major round number (${lp})")
        else:  missed.append("Not a major round number")

        # Condition 3: rejection candle
        c3 = is_valid_candle
        if c3: met.append(f"{'Bullish' if direction=='bullish' else 'Bearish'} {pattern} candle")
        else:  missed.append("No rejection candle at round number")

        # Condition 4: RSI 30–70
        c4 = rsi is not None and 30 <= rsi <= 70
        if c4: met.append(f"RSI {rsi:.1f} in valid range (30–70)")
        else:  missed.append(f"RSI {rsi:.1f if rsi else 'N/A'} out of 30–70")

        # Condition 5: REL_VOL >= 1.2
        c5 = rel_vol is not None and rel_vol >= 1.2
        if c5: met.append(f"Volume {rel_vol:.1f}x avg")
        else:  missed.append(f"REL_VOL < 1.2")

        # Condition 6: confluence
        c6 = has_confluence
        if c6: met.append("Confluence with other S/R level confirmed")
        else:  missed.append("No confluence — isolated round number")

        score = sum([c1, c2, c3, c4, c5, c6])
        if score < 4:
            return None

        # Confluence bonus
        conf_bonus = 0
        if level.level_type == "round_major": conf_bonus += 10
        if any(abs(l.price - lp) / lp < tol for l in sr_levels): conf_bonus += 5
        if any(abs(pl.price - lp) / lp < tol for pl in pivot_levels.values()): conf_bonus += 10
        if any(abs(pl.price - lp) / lp < tol for pl in pdh_pdl.values()): conf_bonus += 10

        confidence = min(95, 50 + int(score / 6 * 30) + conf_bonus)

        stop, t1, t2, t3, rr = self._risk(price, direction, atr,
                                           stop_mult=0.75, t1_mult=1.5,
                                           t2_mult=2.5, t3_mult=4.0)
        premium = level.strength_score >= 7
        options = self._options_guide(direction, price, stop, t1, t2, lp)
        alert   = self._format_alert(ticker, direction, price, stop,
                                     t1, t2, t3, rr, confidence,
                                     level, met, options, premium)

        return SRSignal(
            strategy_id=self.ID,
            strategy_name=self.NAME,
            category=self.CATEGORY,
            direction=direction,
            ticker=ticker,
            price=price,
            entry=price,
            stop=stop, t1=t1, t2=t2, t3=t3, rr=rr,
            confidence=confidence,
            conditions_met=met,
            conditions_missed=missed,
            score=score,
            max_score=6,
            sr_level_price=lp,
            sr_level_type=level.level_type,
            sr_level_strength=level.strength_score,
            sr_level_touches=level.touches,
            premium_setup=premium,
            alert_text=alert,
            options_guide=options,
        )


# ══════════════════════════════════════════════════════════════
#  STRATEGY S27 — PRIOR DAY HIGH / LOW
# ══════════════════════════════════════════════════════════════

class PriorDayHiLo(BaseSRStrategy):
    ID       = "S27"
    NAME     = "Prior Day High / Low"
    CATEGORY = "PDH_PDL"

    def check(self, ticker, df, sr_levels, pivot_levels,
              pdh_pdl, round_levels, orb_levels):
        if not pdh_pdl or len(df) < 2:
            return None

        price = _get(df, "Close")
        if price is None:
            return None

        atr        = _get(df, "ATR14") or price * 0.005
        rsi        = _get(df, "RSI14")
        rel_vol    = _get(df, "REL_VOL")
        vwap       = _get(df, "VWAP")
        ema9_slope = _get(df, "EMA9_SLOPE")

        candle_checker = SRCandleChecker()

        best_signal = None
        best_conf   = 0

        # ── PDL bounce (bullish) ──────────────────────────────────
        pdl_level = pdh_pdl.get("pdl")
        if pdl_level:
            pdl = pdl_level.price
            if abs(price - pdl) / price < 0.0025:
                is_bull, pattern = candle_checker.is_bullish_rejection(df)

                met    = []
                missed = []

                c1 = True
                met.append(f"PDL ${pdl} from prior session")

                c2 = True  # already checked above
                met.append(f"Price within 0.25% of PDL (${pdl})")

                c3 = is_bull
                if c3: met.append(f"Bullish {pattern} candle at PDL")
                else:  missed.append("No bullish rejection candle at PDL")

                c4 = rsi is not None and 28 <= rsi <= 60
                if c4: met.append(f"RSI {rsi:.1f} in valid bounce range")
                else:  missed.append(f"RSI {rsi:.1f if rsi else 'N/A'} out of 28–60")

                c5 = rel_vol is not None and rel_vol >= 1.2
                if c5: met.append(f"Volume {rel_vol:.1f}x avg")
                else:  missed.append("Volume < 1.2x avg")

                c6 = pdl_level.is_fresh
                if c6: met.append("Fresh test — first touch today")
                else:  missed.append("PDL already tested today")

                # Condition 7: no breakdown below PDL yet today
                # Approximate: if current close > PDL, hasn't broken down
                c7 = price >= pdl * 0.998
                if c7: met.append("Holding above PDL — no breakdown yet")
                else:  missed.append("Price has already broken below PDL")

                # Condition 8: confluence with pivot or round number
                tol = 0.003
                c8 = False
                for k, pl in pivot_levels.items():
                    if k in ("S1", "S2", "PP") and abs(pl.price - pdl) / pdl < tol:
                        c8 = True
                        break
                if not c8:
                    for rl in round_levels:
                        if abs(rl.price - pdl) / pdl < tol:
                            c8 = True
                            break
                if c8: met.append("PDL confluent with pivot or round number")
                else:  missed.append("No confluence with pivot/round")

                score = sum([c1, c2, c3, c4, c5, c6, c7, c8])
                if score >= 5:
                    conf_bonus = 0
                    if c6: conf_bonus += 20   # fresh test
                    if c8: conf_bonus += 20   # confluence

                    # Base confidence = 60
                    confidence = min(95, 60 + int(score / 8 * 25) + conf_bonus)

                    if confidence > best_conf:
                        best_conf = confidence
                        stop, t1, t2, t3, rr = self._risk(price, "bullish", atr,
                                                           stop_mult=1.0, t1_mult=1.5,
                                                           t2_mult=2.5, t3_mult=4.0)
                        premium = pdl_level.strength_score >= 7
                        options = self._options_guide("bullish", price, stop, t1, t2, pdl)
                        alert   = self._format_alert(ticker, "bullish", price, stop,
                                                     t1, t2, t3, rr, confidence,
                                                     pdl_level, met, options, premium)
                        best_signal = SRSignal(
                            strategy_id=self.ID,
                            strategy_name=self.NAME,
                            category=self.CATEGORY,
                            direction="bullish",
                            ticker=ticker,
                            price=price,
                            entry=price,
                            stop=stop, t1=t1, t2=t2, t3=t3, rr=rr,
                            confidence=confidence,
                            conditions_met=met,
                            conditions_missed=missed,
                            score=score,
                            max_score=8,
                            sr_level_price=pdl,
                            sr_level_type=pdl_level.level_type,
                            sr_level_strength=pdl_level.strength_score,
                            sr_level_touches=pdl_level.touches,
                            premium_setup=premium,
                            alert_text=alert,
                            options_guide=options,
                        )

        # ── PDH rejection (bearish) ───────────────────────────────
        pdh_level = pdh_pdl.get("pdh")
        if pdh_level:
            pdh = pdh_level.price
            if abs(price - pdh) / price < 0.0025:
                is_bear, pattern = candle_checker.is_bearish_rejection(df)

                met    = []
                missed = []

                c1 = True
                met.append(f"PDH ${pdh} from prior session")

                c2 = True
                met.append(f"Price within 0.25% of PDH (${pdh})")

                c3 = is_bear
                if c3: met.append(f"Bearish {pattern} candle at PDH")
                else:  missed.append("No bearish rejection candle at PDH")

                c4 = rsi is not None and 40 <= rsi <= 72
                if c4: met.append(f"RSI {rsi:.1f} in valid rejection range")
                else:  missed.append(f"RSI {rsi:.1f if rsi else 'N/A'} out of 40–72")

                c5 = rel_vol is not None and rel_vol >= 1.2
                if c5: met.append(f"Volume {rel_vol:.1f}x avg")
                else:  missed.append("Volume < 1.2x avg")

                c6 = pdh_level.is_fresh
                if c6: met.append("Fresh test — first touch today")
                else:  missed.append("PDH already tested today")

                # Condition 7: no breakout above PDH yet today
                c7 = price <= pdh * 1.002
                if c7: met.append("Holding below PDH — no breakout yet")
                else:  missed.append("Price has already broken above PDH")

                # Condition 8: confluence with pivot or round number
                tol = 0.003
                c8 = False
                for k, pl in pivot_levels.items():
                    if k in ("R1", "R2", "PP") and abs(pl.price - pdh) / pdh < tol:
                        c8 = True
                        break
                if not c8:
                    for rl in round_levels:
                        if abs(rl.price - pdh) / pdh < tol:
                            c8 = True
                            break
                if c8: met.append("PDH confluent with pivot or round number")
                else:  missed.append("No confluence with pivot/round")

                score = sum([c1, c2, c3, c4, c5, c6, c7, c8])
                if score >= 5:
                    conf_bonus = 0
                    if c6: conf_bonus += 20
                    if c8: conf_bonus += 20

                    confidence = min(95, 60 + int(score / 8 * 25) + conf_bonus)

                    if confidence > best_conf:
                        best_conf = confidence
                        stop, t1, t2, t3, rr = self._risk(price, "bearish", atr,
                                                           stop_mult=1.0, t1_mult=1.5,
                                                           t2_mult=2.5, t3_mult=4.0)
                        premium = pdh_level.strength_score >= 7
                        options = self._options_guide("bearish", price, stop, t1, t2, pdh)
                        alert   = self._format_alert(ticker, "bearish", price, stop,
                                                     t1, t2, t3, rr, confidence,
                                                     pdh_level, met, options, premium)
                        best_signal = SRSignal(
                            strategy_id=self.ID,
                            strategy_name=self.NAME,
                            category=self.CATEGORY,
                            direction="bearish",
                            ticker=ticker,
                            price=price,
                            entry=price,
                            stop=stop, t1=t1, t2=t2, t3=t3, rr=rr,
                            confidence=confidence,
                            conditions_met=met,
                            conditions_missed=missed,
                            score=score,
                            max_score=8,
                            sr_level_price=pdh,
                            sr_level_type=pdh_level.level_type,
                            sr_level_strength=pdh_level.strength_score,
                            sr_level_touches=pdh_level.touches,
                            premium_setup=premium,
                            alert_text=alert,
                            options_guide=options,
                        )

        return best_signal


# ══════════════════════════════════════════════════════════════
#  SR STRATEGY MODULE — orchestrates all 8
# ══════════════════════════════════════════════════════════════

class SRStrategyModule:
    """
    Runs all 8 S/R strategies. Add to your existing bot with
    add_sr_to_scanner(scanner).

    Usage:
        module  = SRStrategyModule(min_confidence=55)
        signals = module.scan("TSLA", df_5m, df_prior_day)
        for sig in signals:
            print(sig.alert_text)
    """

    STRATEGIES = [
        SupportBounceLong(),
        ResistanceBounceShort(),
        SRBreakoutLong(),
        SRBreakdownShort(),
        PivotPointBounce(),
        PivotPointBreakout(),
        RoundNumberSR(),
        PriorDayHiLo(),
    ]

    def __init__(self, min_confidence: int = 55):
        self.min_confidence = min_confidence
        self.detector  = SRLevelDetector()
        self.confluence= SRConfluenceEngine()
        self.candle    = SRCandleChecker()

    def scan(self, ticker: str,
             df_5m: pd.DataFrame,
             df_prior_day: pd.DataFrame = None) -> list:
        """
        Full S/R scan:
          1. Detect all levels (swing, pivot, PDH/PDL, round, ORB)
          2. Score confluence for each level
          3. Run all 8 strategies
          4. Return signals above min_confidence
        """
        if df_5m is None or len(df_5m) < 20:
            return []

        df = prepare_sr_df(df_5m)
        price = _get(df, "Close")
        atr   = _get(df, "ATR14") or price * 0.005
        vwap  = _get(df, "VWAP")
        ema9  = _get(df, "EMA9")
        ema21 = _get(df, "EMA21")

        # Detect all level types
        sr_levels   = []
        pivot_levels= {}
        pdh_pdl     = {}
        round_levels= []
        orb_levels  = {}

        try:
            sr_levels = self.detector.find_swing_levels(df)
        except Exception:
            pass

        try:
            round_levels = self.detector.find_round_levels(price, atr)
        except Exception:
            pass

        try:
            orb_levels = self.detector.find_orb_levels(df_5m)
        except Exception:
            pass

        if df_prior_day is not None and not df_prior_day.empty:
            try:
                pdh_pdl = self.detector.find_pdh_pdl(df_prior_day)
                ph = float(df_prior_day["High"].max())
                pl = float(df_prior_day["Low"].min())
                pc = float(df_prior_day["Close"].iloc[-1])
                pivot_levels = self.detector.find_pivot_points(ph, pl, pc)
            except Exception:
                pass

        # Score confluence
        all_level_list = (sr_levels +
                          list(pivot_levels.values()) +
                          list(pdh_pdl.values()) +
                          round_levels +
                          list(orb_levels.values()))

        for level in all_level_list:
            self.confluence.score_level(level, all_level_list,
                                         vwap, ema9, ema21, price)

        # Run all strategies
        signals = []
        for strategy in self.STRATEGIES:
            try:
                sig = strategy.check(ticker, df, sr_levels,
                                     pivot_levels, pdh_pdl,
                                     round_levels, orb_levels)
                if sig and sig.confidence >= self.min_confidence:
                    signals.append(sig)
            except NotImplementedError:
                pass  # Strategy not yet implemented
            except Exception:
                pass

        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals

    def format_summary(self, ticker: str, signals: list) -> str:
        if not signals: return ""
        sep   = "═"*52
        lines = [f"\n{sep}",
                 f"  S/R STRATEGIES — {ticker} ({len(signals)} fired)",
                 sep]
        for s in signals:
            e = "🟢" if s.direction=="bullish" else "🔴"
            prem = " ⭐" if s.premium_setup else ""
            lines.append(f"  {e} [{s.strategy_id}] {s.strategy_name:30s} "
                         f"conf:{s.confidence:3d}{prem}")
        top = signals[0]
        lines += ["",
                  f"  Best: {top.strategy_name}",
                  f"  Level: ${top.sr_level_price} ({top.sr_level_type}) "
                  f"· {top.sr_level_touches} touches · strength {top.sr_level_strength}",
                  f"  Entry ${top.entry} | Stop ${top.stop} | "
                  f"T1 ${top.t1} | T2 ${top.t2} | R:R 1:{top.rr}",
                  sep]
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  PLUG-IN FUNCTION
# ══════════════════════════════════════════════════════════════

def add_sr_to_scanner(scanner, min_confidence: int = 55):
    """
    Adds S20–S27 to your existing StrategyScanner.
    """
    sr_module = SRStrategyModule(min_confidence=min_confidence)

    def scan_sr(ticker, df_5m, df_prior_day=None):
        return sr_module.scan(ticker, df_5m, df_prior_day)

    scanner.scan_sr   = scan_sr
    scanner.sr_module = sr_module

    print("S/R strategies registered: S20–S27")
    print("    Usage: scanner.scan_sr('TSLA', df_5m, df_prior_day)")
    return scanner


# ══════════════════════════════════════════════════════════════
#  SELF TEST
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("="*52)
    print("  S/R STRATEGY MODULE — TEST")
    print("="*52)

    det = SRLevelDetector()
    pivots = det.find_pivot_points(425.0, 410.0, 418.0)
    print(f"\nPivot points for PH=425, PL=410, PC=418:")
    for name, level in pivots.items():
        print(f"  {name}: ${level.price}")

    rounds = det.find_round_levels(418.5, 2.0)
    print(f"\nRound levels near $418.50:")
    for r in rounds[:5]:
        print(f"  ${r.price} ({r.level_type})")

    scorer = SRConfluenceEngine()
    test_level = SRLevel(418.0, "pdl", "support", 3)
    test_pivot = SRLevel(418.5, "pivot_s1", "support", 1)
    test_round = SRLevel(418.0, "round_major", "support", 0)
    score = scorer.score_level(test_level,
                               [test_level, test_pivot, test_round],
                               vwap=418.2)
    print(f"\nConfluence score for PDL+Pivot S1+Round $418: {score}")
    print(f"Premium setup: {test_level.is_premium}")

    checker = SRCandleChecker()
    idx = pd.date_range("2024-01-15 10:00", periods=2, freq="5min")
    df_test = pd.DataFrame({
        "Open":  [418.5, 418.2],
        "High":  [419.0, 418.8],
        "Low":   [416.0, 417.0],
        "Close": [418.3, 418.6],
        "Volume":[1_000_000, 1_500_000]
    }, index=idx)
    is_bull, pattern = checker.is_bullish_rejection(df_test)
    print(f"\nCandle check (hammer test): {is_bull} — {pattern}")

    print("\nS/R module loaded successfully.")
