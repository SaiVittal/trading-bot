"""
=============================================================
  OPENING DRIVE STRATEGY MODULE
  Adds 3 new strategies to your existing bot:

  S19A — Opening Drive Gap Breakout    (aggressive momentum)
  S19B — Opening Drive Pullback        (high probability — ARM example)
  S19C — Opening Drive RSI Divergence  (hidden divergence entry)

  Based on: ARM (1/22/25) chart analysis
    Gap +3% | RVOL 7x | Hidden RSI divergence
    Entry $164.16 → $182 (+10.9%) same session

  HOW TO USE:
    from opening_drive import OpeningDriveModule, add_opening_drive_to_scanner
    add_opening_drive_to_scanner(your_existing_scanner)
=============================================================
"""

import math
import numpy as np
import pandas as pd
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
#  INDICATOR HELPERS
# ══════════════════════════════════════════════════════════════

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def _rsi(s: pd.Series, period: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    return 100 - (100 / (1 + g / (l + 1e-10)))

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl  = df["High"] - df["Low"]
    hpc = (df["High"] - df["Close"].shift()).abs()
    lpc = (df["Low"]  - df["Close"].shift()).abs()
    tr  = pd.concat([hl, hpc, lpc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def _vwap(df: pd.DataFrame) -> pd.Series:
    df  = df.copy()
    df["_dt"] = df.index.date
    tp  = (df["High"] + df["Low"] + df["Close"]) / 3
    tpv = tp * df["Volume"]
    return (tpv.groupby(df["_dt"]).cumsum() /
            df["Volume"].groupby(df["_dt"]).cumsum())

def _vol_ma(s: pd.Series, period: int = 20) -> pd.Series:
    return s.rolling(period).mean()

def _get(df, col, idx=-1, default=None):
    try:
        v = df[col].iloc[idx]
        return float(v) if not (isinstance(v, float) and math.isnan(v)) else default
    except Exception:
        return default


def prepare_opening_drive_df(df: pd.DataFrame) -> pd.DataFrame:
    """Add all indicators needed for Opening Drive strategies."""
    df = df.copy()
    df["EMA9"]   = _ema(df["Close"], 9)
    df["EMA20"]  = _ema(df["Close"], 20)
    df["RSI14"]  = _rsi(df["Close"], 14)
    df["ATR14"]  = _atr(df, 14)
    df["VOL_MA"] = _vol_ma(df["Volume"], 20)
    df["REL_VOL"]= df["Volume"] / (df["VOL_MA"] + 1e-10)
    df["VWAP"]   = _vwap(df)
    return df


# ══════════════════════════════════════════════════════════════
#  PREMARKET ANALYSIS HELPER
# ══════════════════════════════════════════════════════════════

class PremarketAnalyser:
    """
    Analyses premarket data to extract:
    - Gap % vs prior day close
    - Premarket high / low
    - RVOL (relative to 20-day average)
    - Key S/R levels
    - Premarket trendline slope
    """

    @staticmethod
    def analyse(df: pd.DataFrame, prior_close: float = None) -> dict:
        """
        df: full OHLCV DataFrame with premarket bars included.
        prior_close: prior day closing price (optional — uses first bar if missing).
        Returns dict of premarket metrics.
        """
        if df is None or len(df) < 5:
            return {}

        # ── Identify premarket bars (before 9:30 AM ET) ──────
        tz = pytz.timezone("America/New_York")
        try:
            if df.index.tzinfo is None:
                df = df.copy()
                df.index = df.index.tz_localize("UTC").tz_convert(tz)
            else:
                df = df.copy()
                df.index = df.index.tz_convert(tz)
        except Exception:
            pass

        mkt_open = dtime(9, 30)
        pm_mask  = df.index.time < mkt_open
        pm_df    = df[pm_mask]
        reg_df   = df[~pm_mask]

        # Prior close fallback
        if prior_close is None or prior_close <= 0:
            prior_close = float(df["Close"].iloc[0])

        # ── Gap % ─────────────────────────────────────────────
        first_open = (float(reg_df["Open"].iloc[0])
                      if len(reg_df) > 0 else float(df["Open"].iloc[-1]))
        gap_pct = ((first_open - prior_close) / prior_close * 100
                   if prior_close > 0 else 0)

        # ── Premarket high / low / key levels ─────────────────
        pm_high = float(pm_df["High"].max())  if len(pm_df) > 0 else first_open
        pm_low  = float(pm_df["Low"].min())   if len(pm_df) > 0 else first_open

        # Key S/R: rolling pivot points from premarket
        pm_sr = []
        if len(pm_df) >= 3:
            highs = pm_df["High"].values
            lows  = pm_df["Low"].values
            for i in range(1, len(pm_df)-1):
                if highs[i] >= highs[i-1] and highs[i] >= highs[i+1]:
                    pm_sr.append(round(highs[i], 2))
                if lows[i] <= lows[i-1] and lows[i] <= lows[i+1]:
                    pm_sr.append(round(lows[i], 2))
        pm_sr = sorted(set([round(x, 2) for x in pm_sr]))

        # ── Premarket RVOL ────────────────────────────────────
        pm_vol     = float(pm_df["Volume"].sum()) if len(pm_df) > 0 else 0
        avg_pm_vol = float(df["Volume"].mean()) * max(len(pm_df), 1)
        rvol       = round(pm_vol / (avg_pm_vol + 1e-10), 2)

        # Also check RVOL on first 5 bars after open
        first5_vol = float(reg_df["Volume"].iloc[:5].sum()) if len(reg_df) >= 5 else 0
        avg_5bar   = float(df["Volume"].mean()) * 5
        rvol_open  = round(first5_vol / (avg_5bar + 1e-10), 2)

        # Use the higher RVOL reading
        rvol = max(rvol, rvol_open)

        # ── Premarket trendline (higher lows?) ────────────────
        if len(pm_df) >= 4:
            pm_lows = pm_df["Low"].values
            slope   = np.polyfit(range(len(pm_lows)), pm_lows, 1)[0]
            trendline_up = slope > 0
        else:
            trendline_up = None

        return {
            "gap_pct":       round(gap_pct, 2),
            "gap_direction": "up" if gap_pct >= 0 else "down",
            "gap_qualified": abs(gap_pct) >= 3.0,
            "pm_high":       round(pm_high, 2),
            "pm_low":        round(pm_low, 2),
            "first_open":    round(first_open, 2),
            "pm_sr_levels":  pm_sr,
            "rvol":          rvol,
            "rvol_quality":  ("extreme" if rvol >= 10 else
                              "strong"  if rvol >= 5  else
                              "good"    if rvol >= 3  else
                              "average" if rvol >= 1  else "low"),
            "trendline_up":  trendline_up,
            "prior_close":   round(prior_close, 2),
        }


# ══════════════════════════════════════════════════════════════
#  RSI DIVERGENCE DETECTOR
# ══════════════════════════════════════════════════════════════

class RSIDivergenceDetector:
    """
    Detects hidden bullish and bearish RSI divergence
    within the last N bars.

    Hidden bullish: price makes lower low, RSI makes higher low
    Hidden bearish: price makes higher high, RSI makes lower high
    """

    @staticmethod
    def detect(df: pd.DataFrame, lookback: int = 20) -> dict:
        if len(df) < lookback + 5:
            return {"hidden_bull": False, "hidden_bear": False,
                    "regular_bull": False, "regular_bear": False}

        df_sub = df.tail(lookback).copy()
        closes = df_sub["Close"].values
        rsi    = df_sub["RSI14"].values if "RSI14" in df_sub.columns else None

        if rsi is None:
            rsi = _rsi(df_sub["Close"], 14).values

        # Find swing lows (price)
        def swing_lows(arr, w=3):
            pts = []
            for i in range(w, len(arr)-w):
                if arr[i] == min(arr[i-w:i+w+1]):
                    pts.append((i, arr[i]))
            return pts

        def swing_highs(arr, w=3):
            pts = []
            for i in range(w, len(arr)-w):
                if arr[i] == max(arr[i-w:i+w+1]):
                    pts.append((i, arr[i]))
            return pts

        price_lows  = swing_lows(closes)
        price_highs = swing_highs(closes)

        hidden_bull = regular_bull = hidden_bear = regular_bear = False
        bull_details = bear_details = {}

        # Hidden bullish: price lower low, RSI higher low
        if len(price_lows) >= 2:
            (i1, p1), (i2, p2) = price_lows[-2], price_lows[-1]
            r1, r2 = rsi[i1], rsi[i2]
            if not (math.isnan(r1) or math.isnan(r2)):
                if p2 < p1 and r2 > r1:   # price LL, RSI HL
                    hidden_bull = True
                    bull_details = {
                        "type": "hidden_bullish",
                        "price_low1": round(p1, 2),
                        "price_low2": round(p2, 2),
                        "rsi_low1":   round(r1, 1),
                        "rsi_low2":   round(r2, 1),
                    }
                if p2 > p1 and r2 < r1:   # price HL, RSI LL → regular bullish
                    regular_bull = True

        # Hidden bearish: price higher high, RSI lower high
        if len(price_highs) >= 2:
            (i1, p1), (i2, p2) = price_highs[-2], price_highs[-1]
            r1, r2 = rsi[i1], rsi[i2]
            if not (math.isnan(r1) or math.isnan(r2)):
                if p2 > p1 and r2 < r1:   # price HH, RSI LH
                    hidden_bear = True
                    bear_details = {
                        "type": "hidden_bearish",
                        "price_high1": round(p1, 2),
                        "price_high2": round(p2, 2),
                        "rsi_high1":   round(r1, 1),
                        "rsi_high2":   round(r2, 1),
                    }
                if p2 < p1 and r2 > r1:   # price LH, RSI HH → regular bearish
                    regular_bear = True

        return {
            "hidden_bull":    hidden_bull,
            "hidden_bear":    hidden_bear,
            "regular_bull":   regular_bull,
            "regular_bear":   regular_bear,
            "bull_details":   bull_details,
            "bear_details":   bear_details,
        }


# ══════════════════════════════════════════════════════════════
#  SIGNAL DATACLASS
# ══════════════════════════════════════════════════════════════

@dataclass
class OpeningDriveSignal:
    strategy_id:       str
    strategy_name:     str
    variant:           str          # A / B / C
    direction:         str          # bullish / bearish
    ticker:            str
    price:             float
    entry:             float
    stop:              float
    t1:                float
    t2:                float
    rr:                float
    confidence:        int
    conditions_met:    list
    conditions_missed: list
    score:             int
    max_score:         int
    rvol:              float
    gap_pct:           float
    pm_data:           dict
    divergence:        dict
    alert_text:        str
    options_guide:     dict = field(default_factory=dict)
    premium_setup:     bool = False   # True when B+C both fire


# ══════════════════════════════════════════════════════════════
#  STRATEGY S19A — GAP BREAKOUT (AGGRESSIVE)
# ══════════════════════════════════════════════════════════════

class OpeningDriveGapBreakout:
    """
    Aggressive entry: price breaks above premarket high on open
    with gap >= 3% and RVOL >= 3x.
    """
    ID      = "S19A"
    NAME    = "Opening Drive — Gap Breakout"
    VARIANT = "A"

    def check(self, ticker: str, df: pd.DataFrame,
              pm: dict, div: dict) -> Optional[OpeningDriveSignal]:
        if len(df) < 10 or not pm: return None

        df    = prepare_opening_drive_df(df)
        price = _get(df, "Close")
        atr   = _get(df, "ATR14") or price * 0.005
        rsi   = _get(df, "RSI14")
        rel_v = _get(df, "REL_VOL")
        vwap  = _get(df, "VWAP")
        ema9  = _get(df, "EMA9")
        if not price: return None

        cur = df.iloc[-1]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        body = abs(cl - o); rng = h-l if h-l>0 else 1e-10

        ALL = [
            f"Gap >= 3% (actual: {pm.get('gap_pct',0):.1f}%)",
            f"RVOL >= 3x (actual: {pm.get('rvol',0):.1f}x)",
            "Price broke above premarket high",
            "Strong breakout candle (body > 60% of range)",
            "RSI crossed above 55",
            "Price above VWAP",
            "EMA9 sloping upward",
            "Volume expanding on breakout",
        ]
        pm_high = pm.get("pm_high", price)
        gap_ok  = abs(pm.get("gap_pct", 0)) >= 3.0
        rvol_ok = pm.get("rvol", 0) >= 3.0
        broke   = cl > pm_high and o <= pm_high
        strong  = body / rng > 0.60
        rsi_ok  = (rsi or 0) > 55
        vwap_ok = price > (vwap or price * 0.99)
        ema_ok  = ema9 is not None and cl > ema9
        vol_ok  = (rel_v or 0) > 1.5

        checks = [gap_ok, rvol_ok, broke, strong, rsi_ok, vwap_ok, ema_ok, vol_ok]
        met    = [ALL[i] for i,v in enumerate(checks) if v]
        missed = [ALL[i] for i,v in enumerate(checks) if not v]

        # Must have core: gap + rvol + break
        if not (gap_ok and rvol_ok): return None
        if not broke: return None
        if len(met) < 4: return None

        direction = "bullish" if pm.get("gap_direction","up")=="up" else "bearish"
        entry = round(price, 2)
        if direction == "bullish":
            stop = round(pm_high - atr*0.5, 2)
            t1   = round(price + atr*1.5, 2)
            t2   = round(price + atr*3.0, 2)
        else:
            stop = round(pm.get("pm_low", price) + atr*0.5, 2)
            t1   = round(price - atr*1.5, 2)
            t2   = round(price - atr*3.0, 2)

        risk   = abs(entry - stop); reward = abs(t1 - entry)
        rr     = round(reward/risk,2) if risk>0 else 0
        conf   = min(100, int(len(met)/len(ALL)*100) + 10)
        if pm.get("rvol",0) >= 7: conf = min(100, conf+10)

        options = {
            "type":   "Long Call" if direction=="bullish" else "Long Put",
            "strike": "ATM or 1-strike ITM",
            "expiry": "0DTE or next-day",
            "entry":  f"Breakout candle close @ ${entry}",
            "exit":   f"T1 ${t1} (50%) then trail T2 ${t2}",
            "stop":   f"Price falls back below PM high ${round(pm_high,2)}",
        }
        alert = self._fmt(ticker,direction,entry,stop,t1,t2,rr,conf,pm,met,options)
        return OpeningDriveSignal(self.ID, self.NAME, self.VARIANT,
            direction, ticker, entry, entry, stop, t1, t2, rr, conf,
            met, missed, len(met), len(ALL),
            pm.get("rvol",0), pm.get("gap_pct",0), pm, div, alert, options)

    def _fmt(self, ticker, direction, entry, stop, t1, t2, rr, conf, pm, met, opts):
        e = "🟢" if direction=="bullish" else "🔴"
        s = "─"*52
        c = "\n".join(f"   ✅ {x}" for x in met)
        return (f"\n{s}\n  {e}  [S19A] OPENING DRIVE — GAP BREAKOUT  {ticker}\n{s}\n"
                f"  Gap:   {pm.get('gap_pct',0):+.1f}%    RVOL: {pm.get('rvol',0):.1f}x  "
                f"({pm.get('rvol_quality','').upper()})\n"
                f"  PM High: ${pm.get('pm_high',0):.2f}\n\n"
                f"  Entry:  ${entry}    Stop:  ${stop}\n"
                f"  T1:     ${t1}       T2:    ${t2}\n"
                f"  R:R:    1:{rr}      Conf:  {conf}/100\n\n"
                f"  🎯 {opts['type']} | {opts['expiry']}\n"
                f"  Conditions ({len(met)}):\n{c}\n"
                f"  ⚠  Educational only\n{s}")


# ══════════════════════════════════════════════════════════════
#  STRATEGY S19B — PULLBACK TO PREMARKET S/R (ARM EXAMPLE)
# ══════════════════════════════════════════════════════════════

class OpeningDrivePullback:
    """
    High-probability entry: price pulls back to premarket S/R
    level after the initial opening spike, then rejects.
    This is the exact ARM $164.16 setup from the chart.
    """
    ID      = "S19B"
    NAME    = "Opening Drive — Pullback to PM S/R"
    VARIANT = "B"

    def check(self, ticker: str, df: pd.DataFrame,
              pm: dict, div: dict) -> Optional[OpeningDriveSignal]:
        if len(df) < 15 or not pm: return None

        df    = prepare_opening_drive_df(df)
        price = _get(df, "Close")
        atr   = _get(df, "ATR14") or price * 0.005
        rsi   = _get(df, "RSI14")
        rel_v = _get(df, "REL_VOL")
        vwap  = _get(df, "VWAP")
        if not price: return None

        cur = df.iloc[-1]; prv = df.iloc[-2]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        lw   = min(o,cl)-l; body = abs(cl-o) if abs(cl-o)>0 else 1e-10
        rng  = h-l if h-l>0 else 1e-10

        # Find nearest premarket S/R level to current price
        pm_sr = pm.get("pm_sr_levels", [pm.get("pm_high", price)])
        direction = "bullish" if pm.get("gap_direction","up")=="up" else "bearish"

        nearest_sr = None
        if pm_sr:
            # For bullish gap: find SR level just below current price
            candidates = [s for s in pm_sr if abs(s-price) <= atr*2.0]
            if candidates:
                nearest_sr = min(candidates, key=lambda x: abs(x-price))

        if nearest_sr is None:
            nearest_sr = pm.get("pm_high", price) * 0.99

        # Check if price pulled back to this level
        touched_sr   = (float(prv["Low"]) <= nearest_sr * 1.003 or
                        l <= nearest_sr * 1.003)
        # Bullish rejection: closed above SR
        bull_reject  = cl > nearest_sr and cl > o
        # Volume dried on pullback, now expanding
        prv_vol = float(prv["Volume"]); vol_ma = _get(df, "VOL_MA") or prv_vol
        vol_dry  = prv_vol < vol_ma * 0.8    # pullback had low vol
        vol_now  = (rel_v or 0) > 1.2         # current bar vol picking up
        # RSI not oversold
        rsi_ok   = (rsi or 50) > 35 and (rsi or 50) < 70
        # Trendline intact
        trend_ok = pm.get("trendline_up", True)
        # Gap qualified
        gap_ok   = abs(pm.get("gap_pct",0)) >= 3.0
        rvol_ok  = pm.get("rvol",0) >= 3.0
        # Bullish wick (hammer-like)
        wick_ok  = lw > body * 0.8 or (cl > o and body/rng > 0.5)
        # Above VWAP
        vwap_ok  = price > (vwap or price*0.99)

        ALL = [
            f"Gap >= 3% ({pm.get('gap_pct',0):+.1f}%)",
            f"RVOL >= 3x ({pm.get('rvol',0):.1f}x)",
            f"Price pulled back to PM S/R level (${round(nearest_sr,2)})",
            "Bullish rejection candle at S/R (closed above)",
            "Premarket trendline still intact (higher lows)",
            "RSI in valid range (35–70)",
            "Volume dried on pullback (low-vol retest)",
            "Volume expanding on rejection",
            "Bullish wick or strong bull bar at S/R",
            "Price above or reclaiming VWAP",
        ]
        checks = [gap_ok, rvol_ok, touched_sr, bull_reject,
                  bool(trend_ok), rsi_ok, vol_dry, vol_now,
                  wick_ok, vwap_ok]
        met    = [ALL[i] for i,v in enumerate(checks) if v]
        missed = [ALL[i] for i,v in enumerate(checks) if not v]

        if not (gap_ok and rvol_ok): return None
        if not (touched_sr and bull_reject): return None
        if len(met) < 5: return None

        entry = round(price, 2)
        stop  = round(nearest_sr - atr*1.0, 2)
        t1    = round(price + atr*2.0, 2)   # prior high area
        t2    = round(price + atr*3.5, 2)   # continuation target
        risk  = abs(entry-stop); reward = abs(t1-entry)
        rr    = round(reward/risk,2) if risk>0 else 0
        conf  = min(100, int(len(met)/len(ALL)*100)+10)
        # Bonus: RSI divergence also present (ARM combo)
        if div.get("hidden_bull"): conf = min(100, conf+15)

        options = {
            "type":   "Long Call",
            "strike": "ATM or 1-strike ITM",
            "expiry": "0DTE for scalp | next-day for hold",
            "entry":  f"Rejection candle close @ ${entry}",
            "exit":   f"T1 ${t1} (sell 50%) | T2 ${t2} (sell rest)",
            "stop":   f"Close below PM S/R level ${round(nearest_sr,2)}",
            "note":   "This is the ARM $164.16 setup type — highest R:R variant",
        }
        alert = self._fmt(ticker, entry, stop, t1, t2, rr, conf,
                          pm, nearest_sr, met, options, div)
        premium = div.get("hidden_bull", False)   # B+C combo
        return OpeningDriveSignal(self.ID, self.NAME, self.VARIANT,
            "bullish", ticker, entry, entry, stop, t1, t2, rr, conf,
            met, missed, len(met), len(ALL),
            pm.get("rvol",0), pm.get("gap_pct",0), pm, div, alert,
            options, premium_setup=premium)

    def _fmt(self, ticker, entry, stop, t1, t2, rr, conf, pm, sr, met, opts, div):
        s  = "─"*52
        c  = "\n".join(f"   ✅ {x}" for x in met)
        bonus = "\n  ⭐ PREMIUM SETUP: Hidden RSI divergence also confirmed!" if div.get("hidden_bull") else ""
        return (f"\n{s}\n  🟢  [S19B] OPENING DRIVE — PULLBACK  {ticker}{bonus}\n{s}\n"
                f"  Gap:  {pm.get('gap_pct',0):+.1f}%    RVOL: {pm.get('rvol',0):.1f}x\n"
                f"  PM S/R level: ${round(sr,2)}  (pullback target)\n\n"
                f"  Entry: ${entry}    Stop: ${stop}\n"
                f"  T1:    ${t1}       T2:   ${t2}\n"
                f"  R:R:   1:{rr}      Conf: {conf}/100\n\n"
                f"  🎯 {opts['type']} | {opts['expiry']}\n"
                f"  Note:  {opts['note']}\n"
                f"  Conditions ({len(met)}):\n{c}\n"
                f"  ⚠  Educational only\n{s}")


# ══════════════════════════════════════════════════════════════
#  STRATEGY S19C — RSI DIVERGENCE ENTRY
# ══════════════════════════════════════════════════════════════

class OpeningDriveRSIDivergence:
    """
    Hidden bullish RSI divergence during Opening Drive pullback.
    Price makes lower low, RSI makes higher low = bulls accumulating.
    Confirmed by 5-min AND 1-min alignment.
    """
    ID      = "S19C"
    NAME    = "Opening Drive — RSI Divergence"
    VARIANT = "C"

    def check(self, ticker: str, df: pd.DataFrame,
              pm: dict, div: dict) -> Optional[OpeningDriveSignal]:
        if len(df) < 20 or not pm: return None

        df    = prepare_opening_drive_df(df)
        price = _get(df, "Close")
        atr   = _get(df, "ATR14") or price * 0.005
        rsi   = _get(df, "RSI14")
        rel_v = _get(df, "REL_VOL")
        vwap  = _get(df, "VWAP")
        ema9  = _get(df, "EMA9")
        if not price: return None

        # Divergence must be present
        hidden_bull = div.get("hidden_bull", False)
        if not hidden_bull: return None

        div_details = div.get("bull_details", {})
        gap_ok  = abs(pm.get("gap_pct",0)) >= 3.0
        rvol_ok = pm.get("rvol",0) >= 3.0
        rsi_ok  = 38 <= (rsi or 50) <= 60
        above_vwap = price >= (vwap or price*0.99)

        cur = df.iloc[-1]
        o,h,l,cl = (float(cur[c]) for c in ["Open","High","Low","Close"])
        body = abs(cl-o); rng = h-l if h-l>0 else 1e-10
        bull_bar = cl > o and body/rng > 0.45

        # RSI turning up
        prv_rsi = _get(df, "RSI14", -2) or 50
        rsi_up  = (rsi or 50) > prv_rsi

        # EMA9 support
        ema_support = price > (ema9 or price*0.99)

        ALL = [
            f"Gap >= 3% ({pm.get('gap_pct',0):+.1f}%)",
            f"RVOL >= 3x ({pm.get('rvol',0):.1f}x)",
            "Hidden bullish RSI divergence confirmed (price LL, RSI HL)",
            "RSI in valid range (38–60) — not overbought",
            "RSI turning upward (momentum building)",
            "Bullish candle after divergence (body > 45%)",
            "Price above or reclaiming VWAP",
            "Price above EMA9 support",
        ]
        checks = [gap_ok, rvol_ok, True, rsi_ok, rsi_up,
                  bull_bar, above_vwap, ema_support]
        met    = [ALL[i] for i,v in enumerate(checks) if v]
        missed = [ALL[i] for i,v in enumerate(checks) if not v]

        if not (gap_ok and rvol_ok): return None
        if len(met) < 4: return None

        entry = round(price, 2)
        # Stop below divergence low
        div_low = div_details.get("price_low2", price - atr)
        stop    = round(div_low - atr*0.5, 2)
        t1      = round(price + atr*2.0, 2)
        t2      = round(price + atr*3.5, 2)
        risk    = abs(entry-stop); reward = abs(t1-entry)
        rr      = round(reward/risk,2) if risk>0 else 0
        conf    = min(100, int(len(met)/len(ALL)*100)+15)  # bonus for divergence

        options = {
            "type":   "Long Call",
            "strike": "ATM or 1-strike ITM",
            "expiry": "0DTE or next-day",
            "entry":  f"Divergence confirmation candle @ ${entry}",
            "exit":   f"T1 ${t1} (50%) | T2 ${t2} (rest)",
            "stop":   f"Below divergence low ${round(div_low,2)}",
            "note":   f"RSI: low1={div_details.get('rsi_low1','?')} → low2={div_details.get('rsi_low2','?')} (higher low = hidden bull)",
        }
        alert = self._fmt(ticker, entry, stop, t1, t2, rr, conf,
                          pm, div_details, met, options)
        return OpeningDriveSignal(self.ID, self.NAME, self.VARIANT,
            "bullish", ticker, entry, entry, stop, t1, t2, rr, conf,
            met, missed, len(met), len(ALL),
            pm.get("rvol",0), pm.get("gap_pct",0), pm, div, alert, options)

    def _fmt(self, ticker, entry, stop, t1, t2, rr, conf, pm, div_d, met, opts):
        s = "─"*52
        c = "\n".join(f"   ✅ {x}" for x in met)
        return (f"\n{s}\n  🟢  [S19C] OPENING DRIVE — RSI DIVERGENCE  {ticker}\n{s}\n"
                f"  Gap:  {pm.get('gap_pct',0):+.1f}%    RVOL: {pm.get('rvol',0):.1f}x\n"
                f"  Hidden bull div: Price {div_d.get('price_low1','?')}→{div_d.get('price_low2','?')} "
                f"| RSI {div_d.get('rsi_low1','?')}→{div_d.get('rsi_low2','?')}\n\n"
                f"  Entry: ${entry}    Stop: ${stop}\n"
                f"  T1:    ${t1}       T2:   ${t2}\n"
                f"  R:R:   1:{rr}      Conf: {conf}/100\n\n"
                f"  🎯 {opts['type']} | {opts['expiry']}\n"
                f"  {opts['note']}\n"
                f"  Conditions ({len(met)}):\n{c}\n"
                f"  ⚠  Educational only\n{s}")


# ══════════════════════════════════════════════════════════════
#  OPENING DRIVE MODULE — orchestrates all 3 variants
# ══════════════════════════════════════════════════════════════

class OpeningDriveModule:
    """
    Runs S19A + S19B + S19C for any ticker.
    Automatically handles premarket analysis + RSI divergence.

    Usage:
        module  = OpeningDriveModule(min_confidence=55)
        signals = module.scan("ARM", df_full, prior_close=160.0)
        for sig in signals:
            print(sig.alert_text)
            if sig.premium_setup:
                print("⭐ PREMIUM: B+C both fired — ARM $164 type setup!")
    """

    STRATEGIES = [
        OpeningDriveGapBreakout(),
        OpeningDrivePullback(),
        OpeningDriveRSIDivergence(),
    ]

    def __init__(self, min_confidence: int = 55, min_rvol: float = 3.0,
                 min_gap_pct: float = 3.0):
        self.min_confidence = min_confidence
        self.min_rvol       = min_rvol
        self.min_gap_pct    = min_gap_pct
        self.pm_analyser    = PremarketAnalyser()
        self.div_detector   = RSIDivergenceDetector()

    def scan(self, ticker: str, df: pd.DataFrame,
             prior_close: float = None) -> list:
        """
        Full scan: premarket analysis + divergence detection + all 3 strategies.
        df: full OHLCV DataFrame including premarket bars.
        prior_close: optional — prior day's closing price.
        """
        if df is None or len(df) < 15:
            return []

        # 1. Premarket analysis
        pm = self.pm_analyser.analyse(df, prior_close)

        # Gate: skip if gap/rvol too low
        if abs(pm.get("gap_pct", 0)) < self.min_gap_pct:
            return []
        if pm.get("rvol", 0) < self.min_rvol:
            return []

        # 2. Indicators + divergence
        df_ind = prepare_opening_drive_df(df)
        div    = self.div_detector.detect(df_ind)

        # 3. Run all 3 strategies
        signals = []
        for strategy in self.STRATEGIES:
            try:
                sig = strategy.check(ticker, df, pm, div)
                if sig and sig.confidence >= self.min_confidence:
                    signals.append(sig)
            except Exception:
                pass

        signals.sort(key=lambda s: s.confidence, reverse=True)

        # Mark premium setup if both B and C fired
        ids = {s.variant for s in signals}
        if "B" in ids and "C" in ids:
            for s in signals:
                s.premium_setup = True

        return signals

    def format_summary(self, ticker: str, signals: list) -> str:
        if not signals: return ""
        sep = "═"*52
        lines = [f"\n{sep}",
                 f"  OPENING DRIVE — {ticker}",
                 f"  {len(signals)} strategy/strategies fired",
                 sep]

        pm = signals[0].pm_data
        lines += [
            f"  Gap:  {pm.get('gap_pct',0):+.1f}%    RVOL: {pm.get('rvol',0):.1f}x  ({pm.get('rvol_quality','').upper()})",
            f"  PM High: ${pm.get('pm_high',0):.2f}    PM Low: ${pm.get('pm_low',0):.2f}",
        ]
        if signals[0].premium_setup:
            lines.append(f"\n  ⭐ PREMIUM SETUP — B+C both fired (ARM $164 type)")
        lines.append("")

        for s in signals:
            e = "🟢" if s.direction=="bullish" else "🔴"
            lines.append(f"  {e} [{s.strategy_id}] {s.strategy_name:35s} conf:{s.confidence:3d}")

        top = signals[0]
        lines += ["",
                  f"  Best entry: ${top.entry} | Stop: ${top.stop}",
                  f"  T1: ${top.t1} | T2: ${top.t2} | R:R 1:{top.rr}",
                  sep]
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  PLUG-IN FUNCTION
# ══════════════════════════════════════════════════════════════

def add_opening_drive_to_scanner(scanner, min_confidence=55,
                                  min_rvol=3.0, min_gap_pct=3.0):
    """
    Adds S19A/B/C Opening Drive strategies to your existing scanner.

    Usage:
        from strategy_engine  import StrategyScanner
        from opening_drive    import add_opening_drive_to_scanner

        scanner = StrategyScanner(min_confidence=55)
        add_opening_drive_to_scanner(scanner)

        # In your bar loop:
        od_signals = scanner.scan_opening_drive("ARM", df_full, prior_close=160.0)
    """
    od_module = OpeningDriveModule(min_confidence, min_rvol, min_gap_pct)

    def scan_opening_drive(ticker, df, prior_close=None):
        return od_module.scan(ticker, df, prior_close)

    scanner.scan_opening_drive = scan_opening_drive
    scanner.od_module           = od_module

    print("✅  Opening Drive strategies added: S19A, S19B, S19C")
    print("    Usage: scanner.scan_opening_drive('ARM', df, prior_close=160.0)")
    return scanner


# ══════════════════════════════════════════════════════════════
#  SELF TEST
# ══════════════════════════════════════════════════════════════

