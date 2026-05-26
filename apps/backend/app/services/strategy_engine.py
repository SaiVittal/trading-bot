"""
=============================================================
  COMPLETE INTRADAY STRATEGY ENGINE
  15 Strategies — All signal types covered

  Strategies included:
  ─────────────────────────────────────────────
  VWAP STRATEGIES
  01. VWAP Bounce Long       — price dips to VWAP, bounces up
  02. VWAP Bounce Short      — price rises to VWAP, rejects down
  03. VWAP Breakout Long     — price breaks above VWAP with volume
  04. VWAP Breakdown Short   — price breaks below VWAP with volume
  05. VWAP Mean Reversion    — price too far from VWAP, snaps back

  REVERSAL STRATEGIES
  06. Market Reversal Long   — downtrend exhaustion → reversal up
  07. Market Reversal Short  — uptrend exhaustion → reversal down
  08. Opening Range Breakout — ORB long / short
  09. Failed Breakout Short  — fake breakout above resistance
  10. Failed Breakdown Long  — fake breakdown below support

  TREND STRATEGIES
  11. Trend Pullback Long    — buy dip in uptrend (EMA pullback)
  12. Trend Pullback Short   — sell rally in downtrend
  13. Momentum Breakout      — breakout with volume + candle

  SQUEEZE / VOLATILITY
  14. Volatility Squeeze     — low ATR squeeze → expansion trade
  15. High-of-Day / Low-of-Day Break — HOD/LOD breakout

=============================================================
"""

import math
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, time as dtime
import pytz

# ── Try pandas-ta ─────────────────────────────────────────────
try:
    import pandas_ta as ta
    USE_PTA = True
except ImportError:
    USE_PTA = False


# ══════════════════════════════════════════════════════════════
#  DATA CLASSES
# ══════════════════════════════════════════════════════════════

@dataclass
class StrategySignal:
    """One fired strategy signal with full context."""
    strategy_id:    str
    strategy_name:  str
    category:       str          # VWAP / REVERSAL / TREND / SQUEEZE
    direction:      str          # bullish / bearish
    ticker:         str
    price:          float
    entry:          float
    stop:           float
    t1:             float
    t2:             float
    rr:             float
    confidence:     int          # 0–100
    conditions_met: list         # which conditions triggered
    conditions_missed: list      # which conditions were not met
    score:          int          # conditions met count
    max_score:      int          # total possible conditions
    alert_text:     str          # formatted alert string
    data:           dict = field(default_factory=dict)   # raw values


# ══════════════════════════════════════════════════════════════
#  INDICATOR HELPER (shared across all strategies)
# ══════════════════════════════════════════════════════════════

class _Indicators:
    """Compute all indicators needed by every strategy."""

    @staticmethod
    def run(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if USE_PTA:
            df.ta.stoch(k=5, d=3, smooth_k=3, append=True)
            df.ta.rsi(length=14, append=True)
            df.ta.vwap(append=True)
            df.ta.atr(length=14, append=True)
            df.ta.ema(length=9,  append=True)
            df.ta.ema(length=20, append=True)
            df.ta.ema(length=21, append=True)
            df.ta.ema(length=50, append=True)
            df.ta.bbands(length=20, append=True)
            # normalise column names
            for src, dst in [("RSI_14","RSI"),("EMA_9","EMA9"),
                              ("EMA_20","EMA20"),("EMA_21","EMA21"),("EMA_50","EMA50")]:
                if src in df.columns: df[dst] = df[src]
            stk = [c for c in df.columns if c.startswith("STOCHk")]
            std = [c for c in df.columns if c.startswith("STOCHd")]
            atr = [c for c in df.columns if c.startswith("ATR")]
            vwp = [c for c in df.columns if "VWAP" in c.upper()
                   and "BAND" not in c.upper()]
            bbu = [c for c in df.columns if c.startswith("BBU_")]
            bbl = [c for c in df.columns if c.startswith("BBL_")]
            bbm = [c for c in df.columns if c.startswith("BBM_")]
            if stk: df["STC_K"] = df[stk[0]]
            if std: df["STC_D"] = df[std[0]]
            if atr: df["ATR"]   = df[atr[0]]
            if vwp: df["VWAP"]  = df[vwp[0]]
            if bbu: df["BB_UP"] = df[bbu[0]]
            if bbl: df["BB_LO"] = df[bbl[0]]
            if bbm: df["BB_MID"]= df[bbm[0]]
        else:
            df = _Indicators._manual(df)

        # Always add these helpers
        df["VOL_MA20"] = df["Volume"].rolling(20).mean()
        df["REL_VOL"]  = df["Volume"] / (df["VOL_MA20"] + 1e-10)
        df["EMA9_SLOPE"] = df["EMA9"] - df["EMA9"].shift(3)
        # ATR-based S/R proximity
        return df

    @staticmethod
    def _manual(df):
        lo = df["Low"].rolling(5).min()
        hi = df["High"].rolling(5).max()
        rk = 100 * (df["Close"] - lo) / (hi - lo + 1e-10)
        df["STC_K"] = rk.rolling(3).mean()
        df["STC_D"] = df["STC_K"].rolling(3).mean()
        d = df["Close"].diff()
        g = d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
        l = (-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
        df["RSI"] = 100 - (100/(1+g/(l+1e-10)))
        df["_dt"] = pd.DatetimeIndex(df.index).date
        tp  = (df["High"]+df["Low"]+df["Close"])/3
        tpv = tp * df["Volume"]
        df["VWAP"] = (tpv.groupby(df["_dt"]).cumsum() /
                      df["Volume"].groupby(df["_dt"]).cumsum())
        hl  = df["High"]-df["Low"]
        hpc = (df["High"]-df["Close"].shift()).abs()
        lpc = (df["Low"] -df["Close"].shift()).abs()
        df["ATR"] = pd.concat([hl,hpc,lpc],axis=1).max(axis=1).rolling(14).mean()
        for sp,nm in [(9,"EMA9"),(20,"EMA20"),(21,"EMA21"),(50,"EMA50")]:
            df[nm] = df["Close"].ewm(span=sp,adjust=False).mean()
        # Bollinger
        mid = df["Close"].rolling(20).mean()
        std = df["Close"].rolling(20).std()
        df["BB_MID"] = mid; df["BB_UP"] = mid+2*std; df["BB_LO"] = mid-2*std
        df["EMA9_SLOPE"] = df["EMA9"] - df["EMA9"].shift(3)
        return df

    @staticmethod
    def get_val(df, col, idx=-1, default=0.0) -> float:
        try:
            v = df[col].iloc[idx]
            if isinstance(v, float) and math.isnan(v):
                return float(default) if default is not None else 0.0
            return float(v)
        except Exception:
            return float(default) if default is not None else 0.0


I = _Indicators   # shorthand


# ══════════════════════════════════════════════════════════════
#  BASE STRATEGY CLASS
# ══════════════════════════════════════════════════════════════

class BaseStrategy:
    ID       = "BASE"
    NAME     = "Base Strategy"
    CATEGORY = "GENERIC"

    def check(self, ticker: str, df: pd.DataFrame) -> Optional[StrategySignal]:
        """Override in subclass. Return StrategySignal or None."""
        raise NotImplementedError

    def _risk(self, price, direction, atr, t1_mult=1.5, t2_mult=2.5,
              stop_mult=1.0):
        atr = atr if atr and atr > 0 else price * 0.005
        if direction == "bullish":
            stop = round(price - atr * stop_mult, 2)
            t1   = round(price + atr * t1_mult,   2)
            t2   = round(price + atr * t2_mult,   2)
        else:
            stop = round(price + atr * stop_mult, 2)
            t1   = round(price - atr * t1_mult,   2)
            t2   = round(price - atr * t2_mult,   2)
        risk   = abs(price - stop)
        reward = abs(t1 - price)
        rr     = round(reward / risk, 2) if risk > 0 else 0
        return stop, t1, t2, rr

    def _score_to_confidence(self, score, max_score):
        return min(100, int(score / max_score * 100))

    def _signal(self, ticker, df, direction, conditions_met,
                conditions_missed, all_conditions, data, stop_mult=1.0,
                t1_mult=1.5, t2_mult=2.5):
        price = I.get_val(df, "Close")
        atr   = I.get_val(df, "ATR") or price * 0.005
        stop, t1, t2, rr = self._risk(price, direction, atr,
                                       t1_mult, t2_mult, stop_mult)
        score      = len(conditions_met)
        max_score  = len(all_conditions)
        confidence = self._score_to_confidence(score, max_score)
        alert_text = self._format(ticker, direction, price, stop, t1, t2,
                                  rr, confidence, conditions_met, data)
        return StrategySignal(
            strategy_id      = self.ID,
            strategy_name    = self.NAME,
            category         = self.CATEGORY,
            direction        = direction,
            ticker           = ticker,
            price            = round(price, 2),
            entry            = round(price, 2),
            stop             = stop,
            t1               = t1,
            t2               = t2,
            rr               = rr,
            confidence       = confidence,
            conditions_met   = conditions_met,
            conditions_missed= conditions_missed,
            score            = score,
            max_score        = max_score,
            alert_text       = alert_text,
            data             = data,
        )

    def _format(self, ticker, direction, price, stop, t1, t2,
                rr, confidence, conditions_met, data):
        emoji = "🟢" if direction == "bullish" else "🔴"
        conds = "\n".join(f"   ✅ {c}" for c in conditions_met)
        return (
            f"{emoji} [{self.NAME}] {ticker} {direction.upper()}\n"
            f"   Price: ${price} | Confidence: {confidence}/100\n"
            f"   Entry: ${price} | Stop: ${stop}\n"
            f"   T1: ${t1} | T2: ${t2} | R:R 1:{rr}\n"
            f"   Conditions:\n{conds}\n"
        )


# ══════════════════════════════════════════════════════════════
#  STRATEGY 01 — VWAP BOUNCE LONG
# ══════════════════════════════════════════════════════════════

class VWAPBounceLong(BaseStrategy):
    """
    Price in uptrend, pulls back to VWAP, shows rejection candle.
    Classic institutional support at VWAP.
    """
    ID       = "S01"
    NAME     = "VWAP Bounce Long"
    CATEGORY = "VWAP"

    def check(self, ticker, df):
        if len(df) < 30: return None
        cur = df.iloc[-1]; prv = df.iloc[-2]

        price = I.get_val(df,"Close"); vwap = I.get_val(df,"VWAP")
        atr   = I.get_val(df,"ATR")  or price*0.005
        rsi   = I.get_val(df,"RSI")
        ema9  = I.get_val(df,"EMA9"); ema21 = I.get_val(df,"EMA21")
        rel_v = I.get_val(df,"REL_VOL")
        if not all([price,vwap,rsi,ema9,ema21]): return None

        # Candle wicks
        o,h,l,cl = float(cur.Open),float(cur.High),float(cur.Low),float(cur.Close)
        lower_wick = min(o,cl) - l
        body       = abs(cl - o)

        ALL = [
            "Price near VWAP (within 0.3%)",
            "Price bounced OFF VWAP (low touched, closed above)",
            "Uptrend intact (EMA9 > EMA21)",
            "RSI not overbought (< 65)",
            "Bullish rejection wick (lower wick > body)",
            "Volume confirmation (rel_vol > 1.2x)",
        ]
        met   = []
        missed= []

        near_vwap    = abs(price - vwap) / vwap < 0.003
        touched_vwap = l <= vwap <= cl              # wick through, closed above
        uptrend      = ema9 > ema21
        rsi_ok       = 30 < rsi < 65
        wick_reject  = lower_wick > body * 1.2
        vol_ok       = (rel_v or 0) > 1.2

        checks = [near_vwap, touched_vwap, uptrend, rsi_ok, wick_reject, vol_ok]
        for c, n in zip(checks, ALL):
            (met if c else missed).append(n)

        if not (near_vwap or touched_vwap): return None
        if not uptrend: return None
        if len(met) < 3: return None

        data = {"vwap":round(vwap,2),"rsi":rsi,"rel_vol":round(rel_v or 1,2),
                "lower_wick":round(lower_wick,4)}
        return self._signal(ticker, df, "bullish", met, missed, ALL, data)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 02 — VWAP BOUNCE SHORT
# ══════════════════════════════════════════════════════════════

class VWAPBounceShort(BaseStrategy):
    """
    Price in downtrend, rallies up to VWAP, gets rejected.
    VWAP acts as resistance.
    """
    ID       = "S02"
    NAME     = "VWAP Bounce Short"
    CATEGORY = "VWAP"

    def check(self, ticker, df):
        if len(df) < 30: return None
        cur = df.iloc[-1]

        price = I.get_val(df,"Close"); vwap = I.get_val(df,"VWAP")
        atr   = I.get_val(df,"ATR")  or (price or 100)*0.005
        rsi   = I.get_val(df,"RSI")
        ema9  = I.get_val(df,"EMA9"); ema21 = I.get_val(df,"EMA21")
        rel_v = I.get_val(df,"REL_VOL")
        if not all([price,vwap,rsi,ema9,ema21]): return None

        o,h,l,cl = float(cur.Open),float(cur.High),float(cur.Low),float(cur.Close)
        upper_wick = h - max(o,cl)
        body       = abs(cl - o)

        ALL = [
            "Price near VWAP from below (within 0.3%)",
            "Rejected at VWAP (high touched, closed below)",
            "Downtrend intact (EMA9 < EMA21)",
            "RSI not oversold (> 35)",
            "Bearish rejection wick (upper wick > body)",
            "Volume confirmation (rel_vol > 1.2x)",
        ]
        met=[]; missed=[]

        near_vwap   = abs(price-vwap)/vwap < 0.003
        hit_vwap    = cl < vwap <= h             # high touched, closed below
        downtrend   = ema9 < ema21
        rsi_ok      = 35 < rsi < 70
        wick_reject = upper_wick > body * 1.2
        vol_ok      = (rel_v or 0) > 1.2

        checks = [near_vwap, hit_vwap, downtrend, rsi_ok, wick_reject, vol_ok]
        for c,n in zip(checks, ALL):
            (met if c else missed).append(n)

        if not (near_vwap or hit_vwap): return None
        if not downtrend: return None
        if len(met) < 3: return None

        data = {"vwap":round(vwap,2),"rsi":rsi,"rel_vol":round(rel_v or 1,2),
                "upper_wick":round(upper_wick,4)}
        return self._signal(ticker, df, "bearish", met, missed, ALL, data)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 03 — VWAP BREAKOUT LONG
# ══════════════════════════════════════════════════════════════

class VWAPBreakoutLong(BaseStrategy):
    """
    Price was below VWAP, now breaks above with strong volume.
    Signals shift from bearish to bullish intraday bias.
    """
    ID       = "S03"
    NAME     = "VWAP Breakout Long"
    CATEGORY = "VWAP"

    def check(self, ticker, df):
        if len(df) < 30: return None
        cur = df.iloc[-1]; prv = df.iloc[-2]

        price = I.get_val(df,"Close"); vwap = I.get_val(df,"VWAP")
        rsi   = I.get_val(df,"RSI")
        rel_v = I.get_val(df,"REL_VOL")
        ema9  = I.get_val(df,"EMA9")
        prv_close = float(prv["Close"]); prv_vwap = I.get_val(df,"VWAP",-2) or vwap
        if not all([price,vwap,rsi]): return None

        ALL = [
            "Previous close was BELOW VWAP",
            "Current close is ABOVE VWAP (breakout)",
            "RSI crossing above 50 (momentum shift)",
            "Strong volume (rel_vol > 1.5x)",
            "Candle closed near its high (body > 60% of range)",
        ]
        met=[]; missed=[]
        o,h,l,cl = (float(df.iloc[-1][c]) for c in ["Open","High","Low","Close"])
        body = abs(cl-o); rng = h-l if h-l>0 else 1e-10

        prev_below   = prv_close < prv_vwap
        curr_above   = price > vwap
        rsi_50       = 48 <= rsi <= 70
        strong_vol   = (rel_v or 0) > 1.5
        strong_close = body/rng > 0.60

        checks = [prev_below, curr_above, rsi_50, strong_vol, strong_close]
        for c,n in zip(checks, ALL):
            (met if c else missed).append(n)

        if not (prev_below and curr_above): return None
        if len(met) < 3: return None

        data = {"vwap":round(vwap,2),"rsi":rsi,"rel_vol":round(rel_v or 1,2)}
        return self._signal(ticker, df, "bullish", met, missed, ALL, data)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 04 — VWAP BREAKDOWN SHORT
# ══════════════════════════════════════════════════════════════

class VWAPBreakdownShort(BaseStrategy):
    """
    Price was above VWAP, breaks below with volume.
    Signals intraday bias shift to bearish.
    """
    ID       = "S04"
    NAME     = "VWAP Breakdown Short"
    CATEGORY = "VWAP"

    def check(self, ticker, df):
        if len(df) < 30: return None
        cur = df.iloc[-1]; prv = df.iloc[-2]

        price = I.get_val(df,"Close"); vwap = I.get_val(df,"VWAP")
        rsi   = I.get_val(df,"RSI");   rel_v = I.get_val(df,"REL_VOL")
        prv_close = float(prv["Close"]); prv_vwap = I.get_val(df,"VWAP",-2) or vwap
        if not all([price,vwap,rsi]): return None

        o,h,l,cl = (float(df.iloc[-1][c]) for c in ["Open","High","Low","Close"])
        body = abs(cl-o); rng = h-l if h-l>0 else 1e-10

        ALL = [
            "Previous close was ABOVE VWAP",
            "Current close is BELOW VWAP (breakdown)",
            "RSI crossing below 50 (momentum shift)",
            "Strong volume (rel_vol > 1.5x)",
            "Candle closed near its low (bear body > 60% of range)",
        ]
        met=[]; missed=[]

        prev_above   = prv_close > prv_vwap
        curr_below   = price < vwap
        rsi_50       = 30 <= rsi <= 52
        strong_vol   = (rel_v or 0) > 1.5
        strong_close = body/rng > 0.60 and cl < o   # bearish body

        checks = [prev_above, curr_below, rsi_50, strong_vol, strong_close]
        for c,n in zip(checks, ALL):
            (met if c else missed).append(n)

        if not (prev_above and curr_below): return None
        if len(met) < 3: return None

        data = {"vwap":round(vwap,2),"rsi":rsi,"rel_vol":round(rel_v or 1,2)}
        return self._signal(ticker, df, "bearish", met, missed, ALL, data)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 05 — VWAP MEAN REVERSION
# ══════════════════════════════════════════════════════════════

class VWAPMeanReversion(BaseStrategy):
    """
    Price has stretched too far from VWAP (> 1.5x ATR).
    Trades the snap-back toward VWAP.
    """
    ID       = "S05"
    NAME     = "VWAP Mean Reversion"
    CATEGORY = "VWAP"

    def check(self, ticker, df):
        if len(df) < 30: return None

        price = I.get_val(df,"Close"); vwap = I.get_val(df,"VWAP")
        atr   = I.get_val(df,"ATR");   rsi  = I.get_val(df,"RSI")
        rel_v = I.get_val(df,"REL_VOL")
        if not all([price,vwap,atr,rsi]): return None

        dist     = price - vwap
        dist_atr = abs(dist) / atr if atr > 0 else 0
        direction = "bullish" if dist < 0 else "bearish"  # snap back

        ALL = [
            "Price stretched > 1.5× ATR from VWAP",
            "RSI in extreme zone (< 30 or > 70)",
            "Volume declining (exhaustion)",
            "Candle showing reversal wick",
        ]
        met=[]; missed=[]

        stretched = dist_atr > 1.5
        rsi_ext   = rsi < 32 or rsi > 68
        # Volume declining last 2 bars
        v = df["Volume"].values
        vol_decline = len(v) >= 3 and v[-1] < v[-2] < v[-3]
        # Wick in direction of reversion
        o,h,l,cl = (float(df.iloc[-1][c]) for c in ["Open","High","Low","Close"])
        wick = (l - min(o,cl)) if direction=="bullish" else (h - max(o,cl))
        body = abs(cl-o) if abs(cl-o)>0 else 1e-10
        wick_ok = wick > body * 0.8

        checks = [stretched, rsi_ext, vol_decline, wick_ok]
        for c,n in zip(checks, ALL):
            (met if c else missed).append(n)

        if not stretched: return None
        if len(met) < 2: return None

        # Target is VWAP itself
        t1 = round(vwap, 2)
        t2 = round(vwap + (vwap - price) * 0.3, 2) if direction=="bullish" else round(vwap - (price - vwap)*0.3, 2)
        stop = round(price - atr*1.0 if direction=="bullish" else price + atr*1.0, 2)
        risk = abs(price-stop); reward = abs(t1-price)
        rr   = round(reward/risk,2) if risk>0 else 0
        confidence = self._score_to_confidence(len(met), len(ALL))

        data = {"vwap":round(vwap,2),"dist_atr":round(dist_atr,2),"rsi":rsi}
        alert = self._format(ticker, direction, price, stop, t1, t2, rr,
                             confidence, met, data)
        return StrategySignal(self.ID, self.NAME, self.CATEGORY, direction,
            ticker, round(price,2), round(price,2), stop, t1, t2, rr,
            confidence, met, missed, len(met), len(ALL), alert, data)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 06 — MARKET REVERSAL LONG (Bottom Reversal)
# ══════════════════════════════════════════════════════════════

class MarketReversalLong(BaseStrategy):
    """
    Downtrend shows exhaustion signs — volume climax, RSI extreme,
    bullish candle pattern → reversal long.
    """
    ID       = "S06"
    NAME     = "Market Reversal Long"
    CATEGORY = "REVERSAL"

    def check(self, ticker, df):
        if len(df) < 30: return None
        cur = df.iloc[-1]; prv = df.iloc[-2]

        price = I.get_val(df,"Close"); rsi = I.get_val(df,"RSI")
        stk   = I.get_val(df,"STC_K"); std = I.get_val(df,"STC_D")
        pstk  = I.get_val(df,"STC_K",-2); pstd = I.get_val(df,"STC_D",-2)
        atr   = I.get_val(df,"ATR");   rel_v = I.get_val(df,"REL_VOL")
        ema9  = I.get_val(df,"EMA9");  ema21 = I.get_val(df,"EMA21")
        vwap  = I.get_val(df,"VWAP")
        if not all([price,rsi,stk,std]): return None

        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        lw = min(o,cl)-l; body = abs(cl-o) if abs(cl-o)>0 else 1e-10
        rng = h-l if h-l>0 else 1e-10

        # Prior trend was bearish
        prior_trend_bear = (ema9 or 999) < (ema21 or 998)
        # Stochastic crosses up from oversold
        stoch_cross = (pstk and pstd and stk and std and
                       pstk < pstd and stk > std and (pstk or 99) < 25)
        # RSI oversold
        rsi_os = rsi < 35
        # Climactic volume
        vol_spike = (rel_v or 0) > 1.8
        # Bullish reversal candle (hammer / pin bar)
        bull_candle = lw > body * 1.5 or (cl > o and body/rng > 0.5)
        # Price near support (lower BB or 3% below VWAP)
        bbl  = I.get_val(df,"BB_LO")
        near_support = ((bbl and price <= bbl * 1.005) or
                        (vwap and price < vwap * 0.97))

        ALL = [
            "Prior downtrend (EMA9 < EMA21)",
            "Stochastic %K crossed above %D from oversold (< 25)",
            "RSI oversold (< 35)",
            "Volume climax spike (rel_vol > 1.8x)",
            "Bullish reversal candle (hammer or strong bull bar)",
            "Price at support (lower BB or far below VWAP)",
        ]
        met=[]; missed=[]
        checks = [prior_trend_bear, stoch_cross, rsi_os, vol_spike,
                  bull_candle, near_support]
        for c,n in zip(checks, ALL):
            (met if c else missed).append(n)

        if not (stoch_cross or rsi_os): return None
        if len(met) < 3: return None

        data = {"rsi":rsi,"stoch_k":round(stk,1),"rel_vol":round(rel_v or 1,2),
                "vwap":round(vwap,2) if vwap else None}
        return self._signal(ticker, df, "bullish", met, missed, ALL, data,
                            stop_mult=1.2, t1_mult=1.8, t2_mult=3.0)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 07 — MARKET REVERSAL SHORT (Top Reversal)
# ══════════════════════════════════════════════════════════════

class MarketReversalShort(BaseStrategy):
    """
    Uptrend shows exhaustion — RSI extreme, bearish candle,
    volume spike → reversal short.
    """
    ID       = "S07"
    NAME     = "Market Reversal Short"
    CATEGORY = "REVERSAL"

    def check(self, ticker, df):
        if len(df) < 30: return None

        price = I.get_val(df,"Close"); rsi = I.get_val(df,"RSI")
        stk   = I.get_val(df,"STC_K"); std = I.get_val(df,"STC_D")
        pstk  = I.get_val(df,"STC_K",-2); pstd = I.get_val(df,"STC_D",-2)
        atr   = I.get_val(df,"ATR"); rel_v = I.get_val(df,"REL_VOL")
        ema9  = I.get_val(df,"EMA9"); ema21 = I.get_val(df,"EMA21")
        vwap  = I.get_val(df,"VWAP")
        if not all([price,rsi,stk,std]): return None

        cur = df.iloc[-1]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        uw   = h - max(o,cl); body = abs(cl-o) if abs(cl-o)>0 else 1e-10
        rng  = h-l if h-l>0 else 1e-10

        prior_trend_bull = (ema9 or 0) > (ema21 or 1)
        stoch_cross = (pstk and pstd and stk and std and
                       pstk > pstd and stk < std and (pstk or 0) > 75)
        rsi_ob      = rsi > 65
        vol_spike   = (rel_v or 0) > 1.8
        bear_candle = uw > body * 1.5 or (cl < o and body/rng > 0.5)
        bbu         = I.get_val(df,"BB_UP")
        near_resist = ((bbu and price >= bbu * 0.995) or
                       (vwap and price > vwap * 1.03))

        ALL = [
            "Prior uptrend (EMA9 > EMA21)",
            "Stochastic %K crossed below %D from overbought (> 75)",
            "RSI overbought (> 65)",
            "Volume climax spike (rel_vol > 1.8x)",
            "Bearish reversal candle (shooting star or strong bear bar)",
            "Price at resistance (upper BB or far above VWAP)",
        ]
        met=[]; missed=[]
        checks = [prior_trend_bull, stoch_cross, rsi_ob, vol_spike,
                  bear_candle, near_resist]
        for c,n in zip(checks, ALL):
            (met if c else missed).append(n)

        if not (stoch_cross or rsi_ob): return None
        if len(met) < 3: return None

        data = {"rsi":rsi,"stoch_k":round(stk,1),"rel_vol":round(rel_v or 1,2),
                "vwap":round(vwap,2) if vwap else None}
        return self._signal(ticker, df, "bearish", met, missed, ALL, data,
                            stop_mult=1.2, t1_mult=1.8, t2_mult=3.0)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 08 — OPENING RANGE BREAKOUT (ORB)
# ══════════════════════════════════════════════════════════════

class OpeningRangeBreakout(BaseStrategy):
    """
    Break above/below the first 30-minute range with volume.
    One of the highest win-rate intraday strategies.
    """
    ID       = "S08"
    NAME     = "Opening Range Breakout"
    CATEGORY = "REVERSAL"

    def check(self, ticker, df):
        if len(df) < 8: return None

        # First 6 bars on 5m = 30 minutes = opening range
        orb_candles = df.iloc[:6]
        orb_high = float(orb_candles["High"].max())
        orb_low  = float(orb_candles["Low"].min())
        orb_range = orb_high - orb_low
        if orb_range <= 0: return None

        price = I.get_val(df,"Close"); rel_v = I.get_val(df,"REL_VOL")
        rsi   = I.get_val(df,"RSI");   vwap  = I.get_val(df,"VWAP")
        atr   = I.get_val(df,"ATR") or price*0.005
        cur   = df.iloc[-1]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])

        broke_above = cl > orb_high and o <= orb_high
        broke_below = cl < orb_low  and o >= orb_low

        if not (broke_above or broke_below): return None
        direction = "bullish" if broke_above else "bearish"

        # Conditions
        strong_vol   = (rel_v or 0) > 1.5
        body = abs(cl-o); rng = h-l if h-l>0 else 1e-10
        strong_close = body/rng > 0.60
        rsi_aligned  = (rsi > 50 if direction=="bullish" else rsi < 50) if rsi else False
        vwap_confirm = ((cl > vwap) if direction=="bullish"
                        else (cl < vwap)) if vwap else False
        orb_size_ok  = orb_range > atr * 0.5   # ORB not too narrow

        ALL = [
            f"Price broke {'above ORB high $'+str(round(orb_high,2)) if broke_above else 'below ORB low $'+str(round(orb_low,2))}",
            "Strong volume on breakout candle (rel_vol > 1.5x)",
            "Strong candle close (body > 60% of range)",
            "RSI aligned with direction (> 50 bull / < 50 bear)",
            "VWAP confirms direction",
            "ORB range is meaningful (> 0.5× ATR)",
        ]
        met=[]; missed=[]
        checks = [True, strong_vol, strong_close, rsi_aligned, vwap_confirm, orb_size_ok]
        for c,n in zip(checks, ALL):
            (met if c else missed).append(n)

        if len(met) < 3: return None

        # Targets: ORB range projected
        if direction == "bullish":
            stop = round(orb_high - atr*0.5, 2)
            t1   = round(cl + orb_range * 1.0, 2)
            t2   = round(cl + orb_range * 2.0, 2)
        else:
            stop = round(orb_low + atr*0.5, 2)
            t1   = round(cl - orb_range * 1.0, 2)
            t2   = round(cl - orb_range * 2.0, 2)

        risk = abs(price-stop); reward = abs(t1-price)
        rr   = round(reward/risk,2) if risk>0 else 0
        conf = self._score_to_confidence(len(met), len(ALL))
        data = {"orb_high":round(orb_high,2),"orb_low":round(orb_low,2),
                "orb_range":round(orb_range,2),"rel_vol":round(rel_v or 1,2)}
        alert = self._format(ticker, direction, price, stop, t1, t2, rr, conf, met, data)
        return StrategySignal(self.ID, self.NAME, self.CATEGORY, direction,
            ticker, round(price,2), round(price,2), stop, t1, t2, rr,
            conf, met, missed, len(met), len(ALL), alert, data)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 09 — FAILED BREAKOUT SHORT
# ══════════════════════════════════════════════════════════════

class FailedBreakoutShort(BaseStrategy):
    """
    Price broke above resistance but failed to hold → sharp reversal.
    Bull trap setup — one of the most powerful intraday shorts.
    """
    ID       = "S09"
    NAME     = "Failed Breakout Short"
    CATEGORY = "REVERSAL"

    def check(self, ticker, df):
        if len(df) < 20: return None

        price = I.get_val(df,"Close")
        atr   = I.get_val(df,"ATR") or price*0.005
        rsi   = I.get_val(df,"RSI")
        rel_v = I.get_val(df,"REL_VOL")

        # Recent resistance = highest high of prior 20 bars (excluding last 3)
        recent_high = float(df["High"].iloc[-23:-3].max()) if len(df)>=23 else float(df["High"].iloc[:-3].max())
        cur = df.iloc[-1]; prv = df.iloc[-2]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        ph = float(prv["High"]); pc = float(prv["Close"])

        # Prior bar broke above resistance, current bar rejected
        prior_broke  = ph > recent_high
        current_fail = cl < recent_high and cl < pc   # closed back below

        if not (prior_broke and current_fail): return None

        upper_wick = h - max(o,cl)
        body = abs(cl-o) if abs(cl-o)>0 else 1e-10
        rng  = h-l if h-l>0 else 1e-10

        ALL = [
            f"Prior bar broke above resistance ${round(recent_high,2)}",
            "Current bar rejected and closed back below resistance",
            "Bearish wick or strong bear candle",
            "RSI was overbought (> 68) at breakout",
            "Volume spike on failed breakout bar",
        ]
        met=[]; missed=[]
        bear_candle = upper_wick > body or (cl < o and body/rng > 0.5)
        rsi_ob      = (rsi or 50) > 62
        vol_spike   = (rel_v or 0) > 1.5

        checks = [True, True, bear_candle, rsi_ob, vol_spike]
        for c,n in zip(checks, ALL):
            (met if c else missed).append(n)

        if len(met) < 3: return None
        data = {"resistance":round(recent_high,2),"rsi":rsi,
                "rel_vol":round(rel_v or 1,2)}
        return self._signal(ticker, df, "bearish", met, missed, ALL, data)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 10 — FAILED BREAKDOWN LONG
# ══════════════════════════════════════════════════════════════

class FailedBreakdownLong(BaseStrategy):
    """Bear trap — price broke below support but snapped back."""
    ID       = "S10"
    NAME     = "Failed Breakdown Long"
    CATEGORY = "REVERSAL"

    def check(self, ticker, df):
        if len(df) < 20: return None

        price = I.get_val(df,"Close")
        atr   = I.get_val(df,"ATR") or price*0.005
        rsi   = I.get_val(df,"RSI"); rel_v = I.get_val(df,"REL_VOL")

        recent_low = float(df["Low"].iloc[-23:-3].min()) if len(df)>=23 else float(df["Low"].iloc[:-3].min())
        cur = df.iloc[-1]; prv = df.iloc[-2]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        pl = float(prv["Low"]); pc = float(prv["Close"])

        prior_broke  = pl < recent_low
        current_snap = cl > recent_low and cl > pc

        if not (prior_broke and current_snap): return None

        lower_wick = min(o,cl)-l; body = abs(cl-o) if abs(cl-o)>0 else 1e-10
        rng = h-l if h-l>0 else 1e-10

        ALL = [
            f"Prior bar broke below support ${round(recent_low,2)}",
            "Current bar snapped back above support",
            "Bullish wick or strong bull candle",
            "RSI was oversold (< 32) at breakdown",
            "Volume spike on failed breakdown bar",
        ]
        met=[]; missed=[]
        bull_candle = lower_wick > body or (cl > o and body/rng > 0.5)
        rsi_os      = (rsi or 50) < 38
        vol_spike   = (rel_v or 0) > 1.5

        checks = [True, True, bull_candle, rsi_os, vol_spike]
        for c,n in zip(checks, ALL):
            (met if c else missed).append(n)

        if len(met) < 3: return None
        data = {"support":round(recent_low,2),"rsi":rsi,"rel_vol":round(rel_v or 1,2)}
        return self._signal(ticker, df, "bullish", met, missed, ALL, data)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 11 — TREND PULLBACK LONG
# ══════════════════════════════════════════════════════════════

class TrendPullbackLong(BaseStrategy):
    """
    Strong uptrend, price pulls back to EMA9/21, resumes.
    Classic 'buy the dip' in a trending market.
    """
    ID       = "S11"
    NAME     = "Trend Pullback Long"
    CATEGORY = "TREND"

    def check(self, ticker, df):
        if len(df) < 30: return None

        price = I.get_val(df,"Close"); ema9  = I.get_val(df,"EMA9")
        ema21 = I.get_val(df,"EMA21"); ema50 = I.get_val(df,"EMA50")
        stk   = I.get_val(df,"STC_K"); std   = I.get_val(df,"STC_D")
        pstk  = I.get_val(df,"STC_K",-2); pstd = I.get_val(df,"STC_D",-2)
        rsi   = I.get_val(df,"RSI"); rel_v = I.get_val(df,"REL_VOL")
        vwap  = I.get_val(df,"VWAP"); atr = I.get_val(df,"ATR") or price*0.005
        if not all([price,ema9,ema21,stk,std]): return None

        uptrend_strong = ema9 > ema21 > (ema50 or ema21*0.99)
        # Pulled back to EMA9 or EMA21
        pulled_to_ema = abs(price - ema9) < atr*0.6 or abs(price - ema21) < atr*0.8
        # Stochastic reset to 40-60 zone and starting to turn up
        stoch_reset = 30 <= (stk or 0) <= 60
        stoch_up    = pstk and stk and stk > pstk
        above_vwap  = price > (vwap or price*0.99)
        vol_pickup  = (rel_v or 0) >= 1.0

        cur = df.iloc[-1]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        bull_resume = cl > o and (cl - o) > (h - l)*0.4

        ALL = [
            "Strong uptrend (EMA9 > EMA21 > EMA50)",
            "Price pulled back to EMA9 or EMA21",
            "Stochastic reset to neutral (30–60)",
            "Stochastic turning back up",
            "Price above VWAP",
            "Bullish resumption candle",
        ]
        met=[]; missed=[]
        checks=[uptrend_strong, pulled_to_ema, stoch_reset, stoch_up,
                above_vwap, bull_resume]
        for c,n in zip(checks,ALL):
            (met if c else missed).append(n)

        if not uptrend_strong: return None
        if not pulled_to_ema:  return None
        if len(met) < 3: return None

        data={"ema9":round(ema9,2),"ema21":round(ema21,2),"stoch_k":round(stk,1),
              "rsi":rsi,"vwap":round(vwap,2) if vwap else None}
        return self._signal(ticker, df, "bullish", met, missed, ALL, data,
                            stop_mult=0.8, t1_mult=1.5, t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 12 — TREND PULLBACK SHORT
# ══════════════════════════════════════════════════════════════

class TrendPullbackShort(BaseStrategy):
    """
    Strong downtrend, price bounces to EMA9/21, resumes down.
    'Sell the rip' in a downtrending market.
    """
    ID       = "S12"
    NAME     = "Trend Pullback Short"
    CATEGORY = "TREND"

    def check(self, ticker, df):
        if len(df) < 30: return None

        price = I.get_val(df,"Close"); ema9  = I.get_val(df,"EMA9")
        ema21 = I.get_val(df,"EMA21"); ema50 = I.get_val(df,"EMA50")
        stk   = I.get_val(df,"STC_K"); std   = I.get_val(df,"STC_D")
        pstk  = I.get_val(df,"STC_K",-2)
        rsi   = I.get_val(df,"RSI"); rel_v = I.get_val(df,"REL_VOL")
        vwap  = I.get_val(df,"VWAP"); atr = I.get_val(df,"ATR") or price*0.005
        if not all([price,ema9,ema21,stk,std]): return None

        downtrend_strong = ema9 < ema21 < (ema50 or ema21*1.01)
        bounced_to_ema   = abs(price-ema9) < atr*0.6 or abs(price-ema21) < atr*0.8
        stoch_reset      = 40 <= (stk or 50) <= 70
        stoch_down       = pstk and stk and stk < pstk
        below_vwap       = price < (vwap or price*1.01)

        cur = df.iloc[-1]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        bear_resume = cl < o and (o - cl) > (h-l)*0.4

        ALL = [
            "Strong downtrend (EMA9 < EMA21 < EMA50)",
            "Price bounced to EMA9 or EMA21",
            "Stochastic reset to neutral (40–70)",
            "Stochastic turning back down",
            "Price below VWAP",
            "Bearish resumption candle",
        ]
        met=[]; missed=[]
        checks=[downtrend_strong, bounced_to_ema, stoch_reset, stoch_down,
                below_vwap, bear_resume]
        for c,n in zip(checks,ALL):
            (met if c else missed).append(n)

        if not downtrend_strong: return None
        if not bounced_to_ema:   return None
        if len(met) < 3: return None

        data={"ema9":round(ema9,2),"ema21":round(ema21,2),"stoch_k":round(stk,1),
              "rsi":rsi,"vwap":round(vwap,2) if vwap else None}
        return self._signal(ticker, df, "bearish", met, missed, ALL, data,
                            stop_mult=0.8, t1_mult=1.5, t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 13 — MOMENTUM BREAKOUT
# ══════════════════════════════════════════════════════════════

class MomentumBreakout(BaseStrategy):
    """
    Price consolidates in tight range, then breaks out with
    expanding volume and strong candle. High-velocity move.
    """
    ID       = "S13"
    NAME     = "Momentum Breakout"
    CATEGORY = "TREND"

    def check(self, ticker, df):
        if len(df) < 20: return None

        price = I.get_val(df,"Close"); atr = I.get_val(df,"ATR") or price*0.005
        rel_v = I.get_val(df,"REL_VOL"); rsi = I.get_val(df,"RSI")
        ema9  = I.get_val(df,"EMA9");   ema21 = I.get_val(df,"EMA21")
        if not price: return None

        # Consolidation: ATR was low for prior 5 bars
        atr_vals = df["ATR"].dropna().values if "ATR" in df.columns else []
        prior_atr_avg = float(np.mean(atr_vals[-10:-1])) if len(atr_vals)>=10 else atr
        low_atr = atr < prior_atr_avg * 0.8 if prior_atr_avg>0 else False

        # Breakout candle
        cur = df.iloc[-1]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        body = abs(cl-o); rng = h-l if h-l>0 else 1e-10
        strong_candle = body/rng > 0.70
        recent_high   = float(df["High"].iloc[-11:-1].max())
        recent_low    = float(df["Low"].iloc[-11:-1].min())
        bull_break    = cl > recent_high and cl > o
        bear_break    = cl < recent_low  and cl < o
        if not (bull_break or bear_break): return None
        direction = "bullish" if bull_break else "bearish"

        vol_expand  = (rel_v or 0) > 2.0
        rsi_aligned = ((rsi or 50) > 52 if direction=="bullish"
                       else (rsi or 50) < 48)
        ema_aligned = ((ema9 or 0) > (ema21 or 0) if direction=="bullish"
                       else (ema9 or 999) < (ema21 or 998)) if ema9 and ema21 else False

        ALL = [
            f"Price broke out of {10}-bar range",
            "Strong breakout candle (body > 70% of range)",
            "Volume expansion (rel_vol > 2.0x)",
            "RSI aligned with direction",
            "EMA trend aligned",
        ]
        met=[]; missed=[]
        checks=[True, strong_candle, vol_expand, rsi_aligned, ema_aligned]
        for c,n in zip(checks,ALL):
            (met if c else missed).append(n)

        if len(met) < 3: return None

        data={"recent_high":round(recent_high,2),"recent_low":round(recent_low,2),
              "rel_vol":round(rel_v or 1,2),"rsi":rsi}
        return self._signal(ticker, df, direction, met, missed, ALL, data,
                            stop_mult=0.8, t1_mult=1.5, t2_mult=2.8)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 14 — VOLATILITY SQUEEZE BREAKOUT
# ══════════════════════════════════════════════════════════════

class VolatilitySqueezeBreakout(BaseStrategy):
    """
    Bollinger Bands narrow (squeeze) → ATR expands → trade the direction.
    Catches explosive moves after consolidation.
    """
    ID       = "S14"
    NAME     = "Volatility Squeeze Breakout"
    CATEGORY = "SQUEEZE"

    def check(self, ticker, df):
        if len(df) < 25: return None

        price = I.get_val(df,"Close")
        bbu   = I.get_val(df,"BB_UP"); bbl = I.get_val(df,"BB_LO")
        bbm   = I.get_val(df,"BB_MID")
        atr   = I.get_val(df,"ATR")
        rel_v = I.get_val(df,"REL_VOL"); rsi = I.get_val(df,"RSI")
        if not all([price,bbu,bbl,bbm,atr]): return None

        # Band width relative to prior
        bw_cur  = (bbu - bbl) / bbm if bbm>0 else 0
        # Prior 10-bar avg band width
        if "BB_UP" in df.columns and "BB_LO" in df.columns and "BB_MID" in df.columns:
            bw_hist = ((df["BB_UP"].iloc[-11:-1] - df["BB_LO"].iloc[-11:-1]) /
                       df["BB_MID"].iloc[-11:-1].replace(0,1)).mean()
        else:
            bw_hist = bw_cur

        squeeze   = bw_cur < bw_hist * 0.85   # bands narrowed vs recent
        # ATR expanding now
        atr_vals  = df["ATR"].dropna().values if "ATR" in df.columns else [atr]
        prior_atr = float(np.mean(atr_vals[-6:-1])) if len(atr_vals)>=6 else atr
        expanding = atr > prior_atr * 1.1

        # Direction: where is price relative to BB mid
        direction = "bullish" if price > bbm else "bearish"

        # Candle direction aligns
        cur = df.iloc[-1]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        candle_dir = cl > o if direction=="bullish" else cl < o

        vol_up    = (rel_v or 0) > 1.3
        rsi_align = ((rsi or 50) > 50 if direction=="bullish"
                     else (rsi or 50) < 50)

        ALL = [
            "Bollinger Band squeeze detected (bands narrowing)",
            "ATR expanding (volatility increasing)",
            "Price direction aligned with breakout side",
            "Volume increasing (rel_vol > 1.3x)",
            "RSI aligned with direction",
        ]
        met=[]; missed=[]
        checks=[squeeze, expanding, candle_dir, vol_up, rsi_align]
        for c,n in zip(checks,ALL):
            (met if c else missed).append(n)

        if not (squeeze and expanding): return None
        if len(met) < 3: return None

        data={"band_width":round(bw_cur,4),"bw_avg":round(bw_hist,4),
              "atr":round(atr,4),"rel_vol":round(rel_v or 1,2)}
        return self._signal(ticker, df, direction, met, missed, ALL, data,
                            stop_mult=1.0, t1_mult=2.0, t2_mult=3.5)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 15 — HIGH-OF-DAY / LOW-OF-DAY BREAK
# ══════════════════════════════════════════════════════════════

class HODLODBreak(BaseStrategy):
    """
    Price breaks the session high (HOD) or low (LOD).
    Strong momentum signal — price discovery in progress.
    """
    ID       = "S15"
    NAME     = "HOD / LOD Breakout"
    CATEGORY = "TREND"

    def check(self, ticker, df):
        if len(df) < 10: return None

        price = I.get_val(df,"Close")
        atr   = I.get_val(df,"ATR") or price*0.005
        rel_v = I.get_val(df,"REL_VOL"); rsi = I.get_val(df,"RSI")
        vwap  = I.get_val(df,"VWAP")
        if not price: return None

        # HOD/LOD = max/min of entire session so far (exclude last bar)
        hod = float(df["High"].iloc[:-1].max())
        lod = float(df["Low"].iloc[:-1].min())

        cur = df.iloc[-1]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        broke_hod = cl > hod and o <= hod
        broke_lod = cl < lod and o >= lod

        if not (broke_hod or broke_lod): return None
        direction = "bullish" if broke_hod else "bearish"

        body     = abs(cl-o); rng = h-l if h-l>0 else 1e-10
        vol_conf = (rel_v or 0) > 1.5
        strong_c = body/rng > 0.60
        vwap_ok  = ((cl > vwap) if direction=="bullish"
                    else (cl < vwap)) if vwap else False
        rsi_ok   = ((rsi or 50) > 52 if direction=="bullish"
                    else (rsi or 50) < 48) if rsi else False

        ALL = [
            f"Price broke session {'HOD $'+str(round(hod,2)) if broke_hod else 'LOD $'+str(round(lod,2))}",
            "Strong volume on breakout (rel_vol > 1.5x)",
            "Strong close candle (body > 60% of range)",
            "VWAP confirms direction",
            "RSI aligned",
        ]
        met=[]; missed=[]
        checks=[True, vol_conf, strong_c, vwap_ok, rsi_ok]
        for c,n in zip(checks,ALL):
            (met if c else missed).append(n)

        if len(met) < 3: return None

        # HOD/LOD target = extend the breakout
        level = hod if broke_hod else lod
        data  = {"hod":round(hod,2),"lod":round(lod,2),
                 "rel_vol":round(rel_v or 1,2),"vwap":round(vwap,2) if vwap else None}
        return self._signal(ticker, df, direction, met, missed, ALL, data,
                            stop_mult=0.7, t1_mult=1.3, t2_mult=2.2)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 16 — 9 EMA PULLBACK LONG (INTRADAY + SCALPING)
# ══════════════════════════════════════════════════════════════

class EMA9PullbackLong(BaseStrategy):
    """
    10-min chart: price above 9 EMA, pulls back to touch EMA,
    bullish bounce candle. Scalp + calls. 9:45–11:30am and 2–3:30pm ET.
    """
    ID       = "S16"
    NAME     = "9 EMA Pullback Long"
    CATEGORY = "EMA"

    def check(self, ticker, df):
        if len(df) < 20: return None

        price     = I.get_val(df, "Close");    ema9  = I.get_val(df, "EMA9")
        ema20     = I.get_val(df, "EMA20");    atr   = I.get_val(df, "ATR") or (price or 1)*0.005
        rsi       = I.get_val(df, "RSI");      rel_v = I.get_val(df, "REL_VOL")
        vwap      = I.get_val(df, "VWAP");     slope = I.get_val(df, "EMA9_SLOPE")
        if not all([price, ema9, ema20, atr, rsi]): return None

        cur = df.iloc[-1]; prv = df.iloc[-2]
        o,h,l,cl = float(cur["Open"]),float(cur["High"]),float(cur["Low"]),float(cur["Close"])

        ALL = [
            "Price above 9 EMA (uptrend confirmed)",
            "9 EMA sloping upward (3-bar slope positive)",
            "Price pulled back to within 0.15× ATR of EMA",
            "Bullish bounce candle closes above EMA",
            "RSI between 38–68 (momentum intact, not overbought)",
            "EMA9 above EMA20 (trend alignment)",
            "Volume on bounce ≥ 1.2× average",
            "Price above VWAP (session bullish bias)",
        ]
        c0 = price > ema9
        c1 = (slope or 0) > 0
        c2 = (abs(float(prv["Low"]) - ema9) <= atr*0.15 or
              abs(price - ema9) <= atr*0.20)
        c3 = cl > ema9 and cl > o
        c4 = 38 <= (rsi or 50) <= 68
        c5 = ema9 > ema20
        c6 = (rel_v or 0) >= 1.2
        c7 = price > (vwap or price*0.99)

        checks = [c0, c1, c2, c3, c4, c5, c6, c7]
        met = []; missed = []
        for c, n in zip(checks, ALL):
            (met if c else missed).append(n)

        if not (c0 and c2 and c3): return None
        if len(met) < 4: return None

        data = {"ema9": round(ema9,2), "ema20": round(ema20,2),
                "rsi": rsi, "vwap": round(vwap,2) if vwap else None,
                "options": "Long Call | ATM | 0DTE or next-day | Delta 0.50–0.65"}
        return self._signal(ticker, df, "bullish", met, missed, ALL, data,
                            stop_mult=1.0, t1_mult=1.5, t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 17 — 9 EMA REJECTION SHORT (INTRADAY + PUTS)
# ══════════════════════════════════════════════════════════════

class EMA9RejectionShort(BaseStrategy):
    """
    10-min chart: price below 9 EMA, rallies to touch EMA,
    bearish rejection candle. Scalp + puts. Mirror of S16.
    """
    ID       = "S17"
    NAME     = "9 EMA Rejection Short"
    CATEGORY = "EMA"

    def check(self, ticker, df):
        if len(df) < 20: return None

        price     = I.get_val(df, "Close");    ema9  = I.get_val(df, "EMA9")
        ema20     = I.get_val(df, "EMA20");    atr   = I.get_val(df, "ATR") or (price or 1)*0.005
        rsi       = I.get_val(df, "RSI");      rel_v = I.get_val(df, "REL_VOL")
        vwap      = I.get_val(df, "VWAP");     slope = I.get_val(df, "EMA9_SLOPE")
        if not all([price, ema9, ema20, atr, rsi]): return None

        cur = df.iloc[-1]; prv = df.iloc[-2]
        o,h,l,cl = float(cur["Open"]),float(cur["High"]),float(cur["Low"]),float(cur["Close"])

        ALL = [
            "Price below 9 EMA (downtrend confirmed)",
            "9 EMA sloping downward (3-bar slope negative)",
            "Price rallied to within 0.15× ATR of EMA",
            "Bearish rejection candle closes below EMA",
            "RSI between 32–62 (momentum intact, not oversold)",
            "EMA9 below EMA20 (trend alignment)",
            "Volume on rejection ≥ 1.2× average",
            "Price below VWAP (session bearish bias)",
        ]
        c0 = price < ema9
        c1 = (slope or 0) < 0
        c2 = (abs(float(prv["High"]) - ema9) <= atr*0.15 or
              abs(price - ema9) <= atr*0.20)
        c3 = cl < ema9 and cl < o
        c4 = 32 <= (rsi or 50) <= 62
        c5 = ema9 < ema20
        c6 = (rel_v or 0) >= 1.2
        c7 = price < (vwap or price*1.01)

        checks = [c0, c1, c2, c3, c4, c5, c6, c7]
        met = []; missed = []
        for c, n in zip(checks, ALL):
            (met if c else missed).append(n)

        if not (c0 and c2 and c3): return None
        if len(met) < 4: return None

        data = {"ema9": round(ema9,2), "ema20": round(ema20,2),
                "rsi": rsi, "vwap": round(vwap,2) if vwap else None,
                "options": "Long Put | ATM | 0DTE or next-day | Delta 0.50–0.65"}
        return self._signal(ticker, df, "bearish", met, missed, ALL, data,
                            stop_mult=1.0, t1_mult=1.5, t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  STRATEGY 18 — 9 EMA CROSS MOMENTUM (0DTE OPTIONS)
# ══════════════════════════════════════════════════════════════

class EMA9CrossMomentum(BaseStrategy):
    """
    10-min chart: price crosses 9 EMA decisively, retest holds,
    momentum continuation. Best for 0DTE options. Both directions.
    """
    ID       = "S18"
    NAME     = "9 EMA Cross Momentum"
    CATEGORY = "EMA"

    def check(self, ticker, df):
        if len(df) < 25: return None

        price  = I.get_val(df, "Close");    ema9  = I.get_val(df, "EMA9")
        ema20  = I.get_val(df, "EMA20");    atr   = I.get_val(df, "ATR") or (price or 1)*0.005
        rsi    = I.get_val(df, "RSI");      rel_v = I.get_val(df, "REL_VOL")
        vwap   = I.get_val(df, "VWAP");    slope1 = I.get_val(df, "EMA9_SLOPE")
        prv_close  = I.get_val(df, "Close", -2)
        prv2_close = I.get_val(df, "Close", -3)
        prv_ema9   = I.get_val(df, "EMA9", -2)
        prv2_ema9  = I.get_val(df, "EMA9", -3)
        if not all([price, ema9, prv_close, prv2_close, prv_ema9, prv2_ema9]): return None

        # Detect cross: prv2 on one side, prv crossed, cur holds
        bull_cross = (prv2_close < prv2_ema9 and
                      prv_close  > prv_ema9  and
                      price      > ema9)
        bear_cross = (prv2_close > prv2_ema9 and
                      prv_close  < prv_ema9  and
                      price      < ema9)
        if not (bull_cross or bear_cross): return None

        direction = "bullish" if bull_cross else "bearish"
        cur = df.iloc[-1]; prv = df.iloc[-2]
        o,h,l,cl = float(cur["Open"]),float(cur["High"]),float(cur["Low"]),float(cur["Close"])
        body = abs(cl-o); rng = (h-l) if h-l>0 else 1e-10

        prv_rsi    = I.get_val(df, "RSI", -2) or 50
        rsi_x50    = ((prv_rsi < 50 and (rsi or 50) > 50) if bull_cross
                      else (prv_rsi > 50 and (rsi or 50) < 50))
        strong_body = body/rng > 0.60
        vol_expand  = (rel_v or 0) > 1.5
        ema_curl    = ((slope1 or 0) > 0 if bull_cross else (slope1 or 0) < 0)
        vwap_ok     = ((price > vwap) if bull_cross else (price < vwap)) if vwap else False
        lo_prv = float(prv["Low"]); hi_prv = float(prv["High"])
        retest = (abs(lo_prv - (prv_ema9 or ema9)) <= atr*0.3 if bull_cross
                  else abs(hi_prv - (prv_ema9 or ema9)) <= atr*0.3)

        ALL = [
            f"{'Bullish' if bull_cross else 'Bearish'} cross through 9 EMA confirmed",
            "Price holding on correct side after cross",
            "Strong confirmation candle body (> 60% of range)",
            "RSI crossed 50 (momentum shift confirmed)",
            "Volume expanded on cross (rel_vol > 1.5×)",
            "9 EMA curling in new direction",
            "VWAP confirms direction (highest confidence)",
            "Retest of EMA held (higher conviction)",
        ]
        checks = [True, True, strong_body, rsi_x50, vol_expand,
                  ema_curl, vwap_ok, retest]
        met = []; missed = []
        for c, n in zip(checks, ALL):
            (met if c else missed).append(n)

        if len(met) < 4: return None

        conf = min(100, int(len(met)/len(ALL)*100) + 10)
        if vwap_ok: conf = min(100, conf + 10)   # VWAP combo bonus

        data = {"ema9": round(ema9,2), "direction": direction,
                "rsi": rsi, "vwap": round(vwap,2) if vwap else None,
                "vwap_combo": vwap_ok,
                "options": "Long Call (bull) / Long Put (bear) | ATM | 0DTE ONLY"}
        return self._signal(ticker, df, direction, met, missed, ALL, data,
                            stop_mult=0.5, t1_mult=2.0, t2_mult=3.5)


# ══════════════════════════════════════════════════════════════
#  MASTER STRATEGY SCANNER — runs all 18 strategies
# ══════════════════════════════════════════════════════════════

class StrategyScanner:
    """
    Runs all 18 strategies against every ticker on every bar.
    Returns list of fired signals sorted by confidence.

    Usage:
        scanner = StrategyScanner(min_confidence=55)
        signals = scanner.scan("TSLA", df_1m)
        for sig in signals:
            print(sig.alert_text)
    """

    STRATEGIES = [
        VWAPBounceLong(),
        VWAPBounceShort(),
        VWAPBreakoutLong(),
        VWAPBreakdownShort(),
        VWAPMeanReversion(),
        MarketReversalLong(),
        MarketReversalShort(),
        OpeningRangeBreakout(),
        FailedBreakoutShort(),
        FailedBreakdownLong(),
        TrendPullbackLong(),
        TrendPullbackShort(),
        MomentumBreakout(),
        VolatilitySqueezeBreakout(),
        HODLODBreak(),
        EMA9PullbackLong(),
        EMA9RejectionShort(),
        EMA9CrossMomentum(),
    ]

    CATEGORY_EMOJI = {
        "VWAP":     "💧",
        "REVERSAL": "🔄",
        "TREND":    "📈",
        "SQUEEZE":  "💥",
        "EMA":      "📉",
    }

    def __init__(self, min_confidence: int = 55):
        self.min_confidence = min_confidence

    def scan(self, ticker: str, df_1m: pd.DataFrame,
             timeframe: str = "5m") -> list[StrategySignal]:
        """
        Scan all strategies. Returns fired signals above min_confidence.
        df_1m: raw 1-minute OHLCV DataFrame. Will be resampled internally.
        """
        df = self._prepare(df_1m, timeframe)
        if df is None or len(df) < 20:
            return []

        # Add all indicators once (shared across strategies)
        df = _Indicators.run(df)

        fired = []
        for strategy in self.STRATEGIES:
            try:
                sig = strategy.check(ticker, df)
                if sig and sig.confidence >= self.min_confidence:
                    fired.append(sig)
            except Exception as e:
                pass   # never crash on one strategy

        # Sort: highest confidence first
        fired.sort(key=lambda s: s.confidence, reverse=True)
        return fired

    def scan_all_timeframes(self, ticker: str,
                            df_1m: pd.DataFrame) -> dict:
        """
        Run scanner on 1m, 5m, 10m, 15m candles and return combined results.
        10m is added specifically for the EMA-9 strategies (S16/S17/S18).
        """
        results = {}
        for tf in ["1m", "5m", "10m", "15m"]:
            signals = self.scan(ticker, df_1m, tf)
            if signals:
                results[tf] = signals
        return results

    def format_combined_alert(self, ticker: str,
                               all_signals: list[StrategySignal]) -> str:
        """Build a rich combined alert from multiple fired strategies."""
        if not all_signals:
            return ""

        top     = all_signals[0]
        emoji   = "🟢" if top.direction == "bullish" else "🔴"
        cat_em  = self.CATEGORY_EMOJI.get(top.category, "📊")
        sep     = "═" * 54

        # Strategy list
        strat_lines = "\n".join(
            f"   {self.CATEGORY_EMOJI.get(s.category,'📊')} [{s.strategy_id}] "
            f"{s.strategy_name:28s} {s.confidence:3d}/100"
            for s in all_signals
        )

        # Consensus direction
        bull = sum(1 for s in all_signals if s.direction=="bullish")
        bear = sum(1 for s in all_signals if s.direction=="bearish")
        consensus = "BULLISH" if bull>bear else "BEARISH"

        conditions = "\n".join(f"   ✅ {c}" for c in top.conditions_met)
        missed     = "\n".join(f"   ○  {c}" for c in top.conditions_missed[:2])

        msg = f"""
{sep}
  {emoji}  {ticker} — {top.direction.upper()} ALERT
  Strategy: {cat_em} {top.strategy_name}
  Consensus: {consensus} ({bull} bull / {bear} bear strategies)
{sep}
  Price:  ${top.price}    Confidence: {top.confidence}/100
  Entry:  ${top.entry}    Stop: ${top.stop}
  T1:     ${top.t1}       (exit 50%)
  T2:     ${top.t2}       (exit 100%)
  R:R:    1:{top.rr}

  All fired strategies ({len(all_signals)} total):
{strat_lines}

  Top strategy conditions met ({top.score}/{top.max_score}):
{conditions}
{"  Conditions not met:" if missed else ""}
{missed if missed else ""}
  ⚠  Educational only — not financial advice
{sep}"""
        return msg

    def _prepare(self, df_1m: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        try:
            if timeframe == "1m":
                return df_1m
            rule = {"5m":"5min","10m":"10min","15m":"15min","1h":"1h"}.get(timeframe,"5min")
            agg  = {c: ("first" if c=="Open" else "max" if c=="High"
                        else "min" if c=="Low" else "last" if c=="Close"
                        else "sum")
                    for c in ["Open","High","Low","Close","Volume"] if c in df_1m.columns}
            return df_1m.resample(rule).agg(agg).dropna()
        except Exception:
            return df_1m


# ══════════════════════════════════════════════════════════════
#  SELF TEST
# ══════════════════════════════════════════════════════════════

    import json
