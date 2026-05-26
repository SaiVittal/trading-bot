"""
=============================================================
  COMPLETE VWAP STRATEGY MODULE
  12 strategies covering every VWAP scenario:

  REVERSAL (6):
  V-R1  VWAP Rejection Reversal Long
  V-R2  VWAP Rejection Reversal Short
  V-R3  VWAP Extended Snap-back Long  (mean reversion)
  V-R4  VWAP Extended Snap-back Short (mean reversion)
  V-R5  VWAP Cross Bias Reversal      (best for 0DTE)
  V-R6  VWAP Std Dev Band Reversal    (±2 StdDev)

  UPTREND (3):
  V-U1  VWAP Bounce Long              (uptrend continuation)
  V-U2  VWAP Hold Long                (3rd test = highest conf)
  V-U3  VWAP Reclaim Long             (false breakdown recovery)

  DOWNTREND (3):
  V-D1  VWAP Rejection Short          (downtrend continuation)
  V-D2  VWAP Hold Short               (3rd rejection = highest conf)
  V-D3  VWAP False Reclaim Short      (bull trap)

  HOW TO USE:
    from vwap_strategies import VWAPStrategyModule, add_vwap_to_scanner
    add_vwap_to_scanner(your_existing_scanner)
    signals = scanner.scan_vwap("TSLA", df_5m)
=============================================================
"""

import math
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
import pytz


# ══════════════════════════════════════════════════════════════
#  INDICATOR HELPERS
# ══════════════════════════════════════════════════════════════

def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def _rsi(s: pd.Series, p: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - (100 / (1 + g / (l + 1e-10)))

def _atr(df: pd.DataFrame, p: int = 14) -> pd.Series:
    hl  = df["High"] - df["Low"]
    hpc = (df["High"] - df["Close"].shift()).abs()
    lpc = (df["Low"]  - df["Close"].shift()).abs()
    return pd.concat([hl, hpc, lpc], axis=1).max(axis=1).rolling(p).mean()

def _vwap_session(df: pd.DataFrame) -> pd.Series:
    """Session VWAP that resets daily."""
    df = df.copy()
    df["_dt"] = df.index.date
    tp  = (df["High"] + df["Low"] + df["Close"]) / 3
    tpv = tp * df["Volume"]
    return (tpv.groupby(df["_dt"]).cumsum() /
            df["Volume"].groupby(df["_dt"]).cumsum())

def _vwap_stddev(df: pd.DataFrame, vwap: pd.Series,
                 mult: float = 2.0) -> tuple:
    """VWAP standard deviation bands."""
    df    = df.copy()
    df["_dt"] = df.index.date
    tp    = (df["High"] + df["Low"] + df["Close"]) / 3
    variance = (
        (tp - vwap).pow(2).multiply(df["Volume"])
        .groupby(df["_dt"]).cumsum()
        / df["Volume"].groupby(df["_dt"]).cumsum()
    )
    std  = variance.pow(0.5)
    upper = vwap + mult * std
    lower = vwap - mult * std
    return upper, lower

def _stoch(df: pd.DataFrame, k: int = 5, d: int = 3) -> tuple:
    lo  = df["Low"].rolling(k).min()
    hi  = df["High"].rolling(k).max()
    rk  = 100 * (df["Close"] - lo) / (hi - lo + 1e-10)
    stk = rk.rolling(d).mean()
    std = stk.rolling(d).mean()
    return stk, std

def _vol_ma(s: pd.Series, p: int = 20) -> pd.Series:
    return s.rolling(p).mean()

def _get(df, col, idx=-1, default=None):
    try:
        v = df[col].iloc[idx]
        return float(v) if not (isinstance(v, float) and math.isnan(v)) else default
    except Exception:
        return default


def prepare_vwap_df(df: pd.DataFrame) -> pd.DataFrame:
    """Add all indicators needed for VWAP strategies."""
    df = df.copy()
    df["EMA9"]    = _ema(df["Close"], 9)
    df["EMA21"]   = _ema(df["Close"], 21)
    df["RSI14"]   = _rsi(df["Close"], 14)
    df["ATR14"]   = _atr(df, 14)
    df["VWAP"]    = _vwap_session(df)
    df["VOL_MA"]  = _vol_ma(df["Volume"], 20)
    df["REL_VOL"] = df["Volume"] / (df["VOL_MA"] + 1e-10)
    df["STC_K"], df["STC_D"] = _stoch(df)

    vwap = df["VWAP"]
    df["VWAP_U1"], df["VWAP_L1"] = _vwap_stddev(df, vwap, 1.0)
    df["VWAP_U2"], df["VWAP_L2"] = _vwap_stddev(df, vwap, 2.0)

    df["EMA9_SLOPE"] = df["EMA9"] - df["EMA9"].shift(3)
    return df


# ══════════════════════════════════════════════════════════════
#  SESSION BIAS DETECTOR
# ══════════════════════════════════════════════════════════════

class SessionBiasDetector:
    """
    Determines the intraday bias: uptrend, downtrend, or mixed.
    Used to select which strategies to run.
    """

    def detect(self, df: pd.DataFrame) -> dict:
        if len(df) < 10:
            return {"bias": "unknown", "above_count": 0, "below_count": 0}

        recent  = df.tail(20)
        price   = _get(df, "Close")
        vwap    = _get(df, "VWAP")
        ema9    = _get(df, "EMA9")
        ema21   = _get(df, "EMA21")
        rsi     = _get(df, "RSI14")

        above_vwap = (recent["Close"] > recent["VWAP"]).sum()
        below_vwap = (recent["Close"] < recent["VWAP"]).sum()

        ema_bull = (ema9 or 0) > (ema21 or 0)
        ema_bear = (ema9 or 999) < (ema21 or 998)
        above_now = (price or 0) > (vwap or 0)

        vwap_touches = self._count_vwap_touches(df)

        if above_vwap >= 15 and ema_bull:   bias = "uptrend"
        elif below_vwap >= 15 and ema_bear: bias = "downtrend"
        elif above_now:                      bias = "bullish_lean"
        else:                                bias = "bearish_lean"

        return {
            "bias":         bias,
            "above_count":  int(above_vwap),
            "below_count":  int(below_vwap),
            "ema_bull":     ema_bull,
            "ema_bear":     ema_bear,
            "price_above_vwap": above_now,
            "rsi":          rsi,
            "vwap_touches": vwap_touches,
        }

    def _count_vwap_touches(self, df: pd.DataFrame,
                             tolerance: float = 0.002) -> int:
        """Count how many times price touched VWAP today."""
        if "VWAP" not in df.columns: return 0
        touches = 0
        was_away = True
        for _, row in df.iterrows():
            try:
                price = float(row["Close"])
                vwap  = float(row["VWAP"])
                near  = abs(price - vwap) / vwap < tolerance
                if near and was_away:
                    touches += 1
                    was_away = False
                elif not near:
                    was_away = True
            except Exception:
                pass
        return touches


# ══════════════════════════════════════════════════════════════
#  SIGNAL DATACLASS
# ══════════════════════════════════════════════════════════════

@dataclass
class VWAPSignal:
    strategy_id:       str
    strategy_name:     str
    category:          str        # REVERSAL / UPTREND / DOWNTREND
    sub_type:          str        # bounce / snap-back / cross / band / hold / reclaim
    direction:         str        # bullish / bearish
    ticker:            str
    price:             float
    vwap:              float
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
    vwap_distance_pct: float      # how far price is from VWAP in %
    session_bias:      str        # uptrend / downtrend / mixed
    vwap_touches_today:int        # how many VWAP touches this session
    alert_text:        str = ""
    options_guide:     dict = field(default_factory=dict)
    premium_setup:     bool = False
    data:              dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
#  BASE STRATEGY
# ══════════════════════════════════════════════════════════════

class BaseVWAPStrategy:
    ID       = "V-XX"
    NAME     = "Base VWAP Strategy"
    CATEGORY = "VWAP"
    SUB_TYPE = "generic"

    def check(self, ticker: str, df: pd.DataFrame,
              bias: dict) -> Optional[VWAPSignal]:
        raise NotImplementedError

    def _risk(self, price, direction, atr,
              stop_mult=1.0, t1_mult=1.5, t2_mult=2.5):
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

    def _options(self, direction, entry, stop, t1, t2, vwap):
        if direction == "bullish":
            return {
                "type":   "Long Call",
                "strike": "ATM or 1-strike ITM",
                "expiry": "0DTE (scalp) or next-day",
                "entry":  f"Bounce close above VWAP ${round(vwap,2)} @ ${entry}",
                "exit":   f"T1 ${t1} (50%) → T2 ${t2} (50%)",
                "stop":   f"5-min close back below VWAP ${round(vwap,2)}",
            }
        return {
            "type":   "Long Put",
            "strike": "ATM or 1-strike ITM",
            "expiry": "0DTE (scalp) or next-day",
            "entry":  f"Rejection close below VWAP ${round(vwap,2)} @ ${entry}",
            "exit":   f"T1 ${t1} (50%) → T2 ${t2} (50%)",
            "stop":   f"5-min close back above VWAP ${round(vwap,2)}",
        }

    def _format(self, ticker, direction, entry, stop, t1, t2, rr,
                conf, vwap, met, opts, premium=False):
        e   = "🟢" if direction == "bullish" else "🔴"
        sep = "═" * 52
        conds = "\n".join(f"   ✅ {c}" for c in met)
        prem  = "\n  ⭐ PREMIUM SETUP — Cross+VWAP aligned!" if premium else ""
        return (
            f"\n{sep}\n  {e}  [{self.ID}] {self.NAME} — {ticker}{prem}\n{sep}\n"
            f"  Price:  ${entry}     VWAP: ${round(vwap,2)}\n"
            f"  Entry:  ${entry}     Stop: ${stop}\n"
            f"  T1:     ${t1}        T2:   ${t2}   R:R 1:{rr}\n"
            f"  Conf:   {conf}/100\n\n"
            f"  🎯 {opts['type']} | {opts['expiry']}\n"
            f"  Conditions ({len(met)}):\n{conds}\n"
            f"  ⚠  Educational only\n{sep}"
        )

    def _sig(self, ticker, df, direction, met, missed, ALL,
             vwap, atr, bias, stop_mult=1.0, t1_mult=1.5, t2_mult=2.5,
             premium=False):
        price  = _get(df, "Close")
        stop, t1, t2, rr = self._risk(price, direction, atr,
                                       stop_mult, t1_mult, t2_mult)
        conf   = min(100, int(len(met)/len(ALL)*100) + 10)
        opts   = self._options(direction, price, stop, t1, t2, vwap)
        alert  = self._format(ticker, direction, round(price,2), stop, t1, t2,
                              rr, conf, vwap, met, opts, premium)
        dist   = round(abs(price-vwap)/vwap*100, 2) if vwap else 0
        return VWAPSignal(
            strategy_id       = self.ID,
            strategy_name     = self.NAME,
            category          = self.CATEGORY,
            sub_type          = self.SUB_TYPE,
            direction         = direction,
            ticker            = ticker,
            price             = round(price, 2),
            vwap              = round(vwap, 2),
            entry             = round(price, 2),
            stop              = stop,
            t1                = t1,
            t2                = t2,
            rr                = rr,
            confidence        = conf,
            conditions_met    = met,
            conditions_missed = missed,
            score             = len(met),
            max_score         = len(ALL),
            vwap_distance_pct = dist,
            session_bias      = bias.get("bias", "unknown"),
            vwap_touches_today= bias.get("vwap_touches", 0),
            alert_text        = alert,
            options_guide     = opts,
            premium_setup     = premium,
            data              = {
                "vwap": round(vwap,2), "atr": round(atr,4),
                "rsi": _get(df,"RSI14"), "rel_vol": _get(df,"REL_VOL"),
                "ema9": _get(df,"EMA9"), "ema21": _get(df,"EMA21"),
            },
        )


# ══════════════════════════════════════════════════════════════
#  V-R1 — VWAP REJECTION REVERSAL LONG
# ══════════════════════════════════════════════════════════════

class VWAPRejectionReversalLong(BaseVWAPStrategy):
    ID="V-R1"; NAME="VWAP Rejection Reversal Long"
    CATEGORY="REVERSAL"; SUB_TYPE="rejection_bounce"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); rel_v=_get(df,"REL_VOL")
        stk=_get(df,"STC_K"); ema9=_get(df,"EMA9")
        if not all([price,vwap,atr,rsi]): return None

        cur=df.iloc[-1]; prv=df.iloc[-2]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        lw=min(o,cl)-l; body=abs(cl-o) if abs(cl-o)>0 else 1e-10
        rng=h-l if h-l>0 else 1e-10

        wicked_below = float(prv["Low"]) < vwap or l < vwap
        close_above  = cl > vwap
        bull_candle  = (lw > body*1.2 or (cl>o and body/rng>0.45))
        rsi_os       = (rsi or 50) < 38
        vol_spike    = (rel_v or 0) > 1.5
        stoch_up     = stk is not None and _get(df,"STC_K",-2) is not None \
                       and (stk > (_get(df,"STC_K",-2) or stk))
        ema_support  = ema9 is not None and cl > ema9*0.995

        ALL=["Price wicked below VWAP on prior/current bar",
             "Current candle closed above VWAP (recovery)",
             "Bullish reversal candle (wick or strong bull bar)",
             "RSI in oversold territory (< 38)",
             "Volume spike on bounce bar (> 1.5×)",
             "Stochastic turning upward",
             "Price holding above EMA9"]
        checks=[wicked_below,close_above,bull_candle,rsi_os,vol_spike,stoch_up,ema_support]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if not (wicked_below and close_above): return None
        if len(met) < 4: return None
        return self._sig(ticker,df,"bullish",met,missed,ALL,vwap,atr,bias,
                         stop_mult=0.8,t1_mult=1.5,t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  V-R2 — VWAP REJECTION REVERSAL SHORT
# ══════════════════════════════════════════════════════════════

class VWAPRejectionReversalShort(BaseVWAPStrategy):
    ID="V-R2"; NAME="VWAP Rejection Reversal Short"
    CATEGORY="REVERSAL"; SUB_TYPE="rejection_bounce"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); rel_v=_get(df,"REL_VOL")
        stk=_get(df,"STC_K")
        if not all([price,vwap,atr,rsi]): return None

        cur=df.iloc[-1]; prv=df.iloc[-2]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        uw=h-max(o,cl); body=abs(cl-o) if abs(cl-o)>0 else 1e-10
        rng=h-l if h-l>0 else 1e-10

        wicked_above = float(prv["High"]) > vwap or h > vwap
        close_below  = cl < vwap
        bear_candle  = (uw > body*1.2 or (cl<o and body/rng>0.45))
        rsi_ob       = (rsi or 50) > 62
        vol_spike    = (rel_v or 0) > 1.5
        stoch_dn     = stk is not None and _get(df,"STC_K",-2) is not None \
                       and (stk < (_get(df,"STC_K",-2) or stk))

        ALL=["Price wicked above VWAP on prior/current bar",
             "Current candle closed below VWAP (rejection confirmed)",
             "Bearish reversal candle (wick or strong bear bar)",
             "RSI in overbought territory (> 62)",
             "Volume spike on rejection bar (> 1.5×)",
             "Stochastic turning downward"]
        checks=[wicked_above,close_below,bear_candle,rsi_ob,vol_spike,stoch_dn]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if not (wicked_above and close_below): return None
        if len(met) < 4: return None
        return self._sig(ticker,df,"bearish",met,missed,ALL,vwap,atr,bias,
                         stop_mult=0.8,t1_mult=1.5,t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  V-R3 — VWAP EXTENDED SNAP-BACK LONG
# ══════════════════════════════════════════════════════════════

class VWAPSnapbackLong(BaseVWAPStrategy):
    ID="V-R3"; NAME="VWAP Extended Snap-back Long"
    CATEGORY="REVERSAL"; SUB_TYPE="mean_reversion"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); rel_v=_get(df,"REL_VOL")
        stk=_get(df,"STC_K")
        if not all([price,vwap,atr,rsi]): return None

        dist_atr = (vwap - price) / atr if atr > 0 else 0

        cur=df.iloc[-1]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        lw=min(o,cl)-l; body=abs(cl-o) if abs(cl-o)>0 else 1e-10
        rng=h-l if h-l>0 else 1e-10

        extended   = dist_atr > 1.5
        rsi_ext    = (rsi or 50) < 30
        vol_vals   = df["Volume"].values
        vol_dry    = len(vol_vals)>=3 and vol_vals[-1]<vol_vals[-2]<vol_vals[-3]
        bull_candle= (lw > body or (cl>o and body/rng>0.40))
        stoch_up   = (stk or 50) < 25
        below_l2   = "VWAP_L2" in df.columns and price <= _get(df,"VWAP_L2")

        ALL=["Price > 1.5× ATR below VWAP (extreme extension)",
             "RSI below 30 (deeply oversold)",
             "Volume declining (exhaustion of selling pressure)",
             "Bullish candle forming at the low",
             "Stochastic in extreme oversold (< 25)",
             "Price at or below VWAP -2 StdDev band"]
        checks=[extended,rsi_ext,vol_dry,bull_candle,stoch_up,below_l2]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if not extended: return None
        if len(met) < 3: return None

        # Override targets: T1 = VWAP itself, T2 = VWAP + 0.5×ATR
        price_f = round(price, 2)
        stop    = round(price - atr*0.8, 2)
        t1      = round(vwap, 2)
        t2      = round(vwap + atr*0.5, 2)
        rr      = round(abs(t1-price_f)/abs(price_f-stop), 2) if abs(price_f-stop)>0 else 0
        conf    = min(100, int(len(met)/len(ALL)*100)+15)
        opts    = self._options("bullish", price_f, stop, t1, t2, vwap)
        alert   = self._format(ticker,"bullish",price_f,stop,t1,t2,rr,conf,vwap,met,opts)
        dist    = round(abs(price-vwap)/vwap*100, 2) if vwap else 0
        return VWAPSignal(self.ID,self.NAME,self.CATEGORY,self.SUB_TYPE,
            "bullish",ticker,price_f,round(vwap,2),price_f,stop,t1,t2,rr,
            conf,met,missed,len(met),len(ALL),dist,bias.get("bias","unknown"),
            bias.get("vwap_touches",0),alert,opts,data={"dist_atr":round(dist_atr,2)})


# ══════════════════════════════════════════════════════════════
#  V-R4 — VWAP EXTENDED SNAP-BACK SHORT
# ══════════════════════════════════════════════════════════════

class VWAPSnapbackShort(BaseVWAPStrategy):
    ID="V-R4"; NAME="VWAP Extended Snap-back Short"
    CATEGORY="REVERSAL"; SUB_TYPE="mean_reversion"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); stk=_get(df,"STC_K")
        if not all([price,vwap,atr,rsi]): return None

        dist_atr = (price - vwap) / atr if atr > 0 else 0

        cur=df.iloc[-1]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        uw=h-max(o,cl); body=abs(cl-o) if abs(cl-o)>0 else 1e-10
        rng=h-l if h-l>0 else 1e-10

        extended   = dist_atr > 1.5
        rsi_ext    = (rsi or 50) > 70
        vol_vals   = df["Volume"].values
        vol_dry    = len(vol_vals)>=3 and vol_vals[-1]<vol_vals[-2]<vol_vals[-3]
        bear_candle= (uw > body or (cl<o and body/rng>0.40))
        stoch_dn   = (stk or 50) > 75
        above_u2   = "VWAP_U2" in df.columns and price >= _get(df,"VWAP_U2")

        ALL=["Price > 1.5× ATR above VWAP (extreme extension)",
             "RSI above 70 (deeply overbought)",
             "Volume declining (exhaustion of buying pressure)",
             "Bearish candle forming at the high",
             "Stochastic in extreme overbought (> 75)",
             "Price at or above VWAP +2 StdDev band"]
        checks=[extended,rsi_ext,vol_dry,bear_candle,stoch_dn,above_u2]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if not extended: return None
        if len(met) < 3: return None

        price_f = round(price,2)
        stop    = round(price + atr*0.8, 2)
        t1      = round(vwap, 2)
        t2      = round(vwap - atr*0.5, 2)
        rr      = round(abs(t1-price_f)/abs(stop-price_f), 2) if abs(stop-price_f)>0 else 0
        conf    = min(100, int(len(met)/len(ALL)*100)+15)
        opts    = self._options("bearish", price_f, stop, t1, t2, vwap)
        alert   = self._format(ticker,"bearish",price_f,stop,t1,t2,rr,conf,vwap,met,opts)
        dist    = round(abs(price-vwap)/vwap*100, 2) if vwap else 0
        return VWAPSignal(self.ID,self.NAME,self.CATEGORY,self.SUB_TYPE,
            "bearish",ticker,price_f,round(vwap,2),price_f,stop,t1,t2,rr,
            conf,met,missed,len(met),len(ALL),dist,bias.get("bias","unknown"),
            bias.get("vwap_touches",0),alert,opts,data={"dist_atr":round(dist_atr,2)})


# ══════════════════════════════════════════════════════════════
#  V-R5 — VWAP CROSS BIAS REVERSAL
# ══════════════════════════════════════════════════════════════

class VWAPCrossReversal(BaseVWAPStrategy):
    ID="V-R5"; NAME="VWAP Cross Bias Reversal"
    CATEGORY="REVERSAL"; SUB_TYPE="cross"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); rel_v=_get(df,"REL_VOL")
        prv_price=_get(df,"Close",-2); prv_vwap=_get(df,"VWAP",-2)
        prv_rsi=_get(df,"RSI14",-2)
        ema9=_get(df,"EMA9"); prv_ema9=_get(df,"EMA9",-2)
        if not all([price,vwap,atr,rsi,prv_price,prv_vwap]): return None

        bull_cross = prv_price < prv_vwap and price > vwap
        bear_cross = prv_price > prv_vwap and price < vwap
        if not (bull_cross or bear_cross): return None
        direction = "bullish" if bull_cross else "bearish"

        cur=df.iloc[-1]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        body=abs(cl-o); rng=h-l if h-l>0 else 1e-10

        strong_body  = body/rng > 0.55
        rsi_cross_50 = ((prv_rsi or 50)<50 and (rsi or 50)>50 if bull_cross
                        else (prv_rsi or 50)>50 and (rsi or 50)<50)
        vol_expand   = (rel_v or 0) > 1.5
        ema_align    = ((ema9 or 0)>(prv_ema9 or 0) if bull_cross
                        else (ema9 or 999)<(prv_ema9 or 998)) if ema9 and prv_ema9 else False

        # Ultra-premium: VWAP cross + EMA9 cross same candle
        ema9_cross = ema_align and abs((ema9 or 0)-(vwap or 0))/(vwap or 1)*100 < 0.5
        premium    = ema9_cross and vol_expand

        ALL=["Price crossed VWAP (prev below/above, now above/below)",
             "Strong body on cross candle (body > 55% of range)",
             "RSI crossed 50 (momentum confirmation)",
             "Volume expanded on cross candle (> 1.5×)",
             "EMA9 aligning in new direction"]
        checks=[True, strong_body, rsi_cross_50, vol_expand, ema_align]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if len(met) < 3: return None
        conf = min(100, int(len(met)/len(ALL)*100)+10)
        if premium: conf = min(100, conf+15)
        return self._sig(ticker,df,direction,met,missed,ALL,vwap,atr,bias,
                         stop_mult=0.6,t1_mult=2.0,t2_mult=3.5,premium=premium)


# ══════════════════════════════════════════════════════════════
#  V-R6 — VWAP BAND REVERSAL (±2 STDDEV)
# ══════════════════════════════════════════════════════════════

class VWAPBandReversal(BaseVWAPStrategy):
    ID="V-R6"; NAME="VWAP Std Dev Band Reversal"
    CATEGORY="REVERSAL"; SUB_TYPE="band"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); rel_v=_get(df,"REL_VOL")
        u2=_get(df,"VWAP_U2"); l2=_get(df,"VWAP_L2")
        u1=_get(df,"VWAP_U1"); l1=_get(df,"VWAP_L1")
        if not all([price,vwap,rsi,u2,l2]): return None

        at_upper = price >= u2*(1-0.002)
        at_lower = price <= l2*(1+0.002)
        if not (at_upper or at_lower): return None
        direction = "bullish" if at_lower else "bearish"

        cur=df.iloc[-1]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        body=abs(cl-o); rng=h-l if h-l>0 else 1e-10
        wick_ok = ((min(o,cl)-l > body*1.0) if direction=="bullish"
                   else (h-max(o,cl) > body*1.0))

        rsi_ext   = ((rsi or 50)<30 if direction=="bullish" else (rsi or 50)>70)
        vol_ok    = (rel_v or 0) > 1.2
        candle_ok = (wick_ok or body/rng > 0.45)

        ALL=["Price at VWAP ±2 standard deviation band (extreme zone)",
             "RSI in extreme territory (< 30 or > 70)",
             "Volume confirmation (> 1.2×)",
             "Rejection candle at band level (wick or strong body)"]
        checks=[True, rsi_ext, vol_ok, candle_ok]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if len(met) < 3: return None

        # Targets: ±1 band → VWAP
        if direction == "bullish":
            t1 = round(l1 or vwap, 2)
            t2 = round(vwap, 2)
            stop = round(price - atr*0.8, 2)
        else:
            t1 = round(u1 or vwap, 2)
            t2 = round(vwap, 2)
            stop = round(price + atr*0.8, 2)

        price_f = round(price,2)
        rr  = round(abs(t1-price_f)/abs(price_f-stop), 2) if abs(price_f-stop)>0 else 0
        conf= min(100, int(len(met)/len(ALL)*100)+15)
        opts= self._options(direction, price_f, stop, t1, t2, vwap)
        alert=self._format(ticker,direction,price_f,stop,t1,t2,rr,conf,vwap,met,opts)
        dist= round(abs(price-vwap)/vwap*100, 2) if vwap else 0
        return VWAPSignal(self.ID,self.NAME,self.CATEGORY,self.SUB_TYPE,
            direction,ticker,price_f,round(vwap,2),price_f,stop,t1,t2,rr,
            conf,met,missed,len(met),len(ALL),dist,bias.get("bias","unknown"),
            bias.get("vwap_touches",0),alert,opts)


# ══════════════════════════════════════════════════════════════
#  V-U1 — VWAP BOUNCE LONG (UPTREND)
# ══════════════════════════════════════════════════════════════

class VWAPBounceLongUptrend(BaseVWAPStrategy):
    ID="V-U1"; NAME="VWAP Bounce Long (uptrend)"
    CATEGORY="UPTREND"; SUB_TYPE="bounce"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        if bias.get("bias") not in ["uptrend","bullish_lean"]: return None

        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); rel_v=_get(df,"REL_VOL")
        ema9=_get(df,"EMA9"); ema21=_get(df,"EMA21")
        if not all([price,vwap,atr,rsi,ema9,ema21]): return None

        near_vwap   = abs(price-vwap)/vwap < 0.003
        above_vwap  = price > vwap
        ema_bull    = ema9 > ema21
        rsi_ok      = 38 <= (rsi or 50) <= 65
        vol_ok      = (rel_v or 0) >= 1.2

        cur=df.iloc[-1]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        lw=min(o,cl)-l; body=abs(cl-o) if abs(cl-o)>0 else 1e-10
        rng=h-l if h-l>0 else 1e-10
        bull_candle = (lw > body*1.0 or (cl>o and body/rng>0.45))

        above_3 = bias.get("above_count",0) >= 12

        ALL=["Session bias is bullish (uptrend confirmed)",
             "Price near VWAP (within 0.3%) — pullback to test",
             "Price holding above VWAP after test",
             "EMA9 above EMA21 (trend alignment)",
             "RSI in healthy range (38–65)",
             "Volume pickup on bounce bar (≥ 1.2×)",
             "Bullish rejection candle at VWAP",
             "Price spent majority of session above VWAP"]
        checks=[True, near_vwap, above_vwap, ema_bull, rsi_ok, vol_ok, bull_candle, above_3]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if not (near_vwap and above_vwap): return None
        if len(met) < 4: return None
        return self._sig(ticker,df,"bullish",met,missed,ALL,vwap,atr,bias,
                         stop_mult=0.5,t1_mult=1.5,t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  V-U2 — VWAP HOLD LONG (3RD TEST)
# ══════════════════════════════════════════════════════════════

class VWAPHoldLong3rdTest(BaseVWAPStrategy):
    ID="V-U2"; NAME="VWAP Hold Long (3rd test)"
    CATEGORY="UPTREND"; SUB_TYPE="hold"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        if bias.get("bias") not in ["uptrend","bullish_lean"]: return None

        touches = bias.get("vwap_touches", 0)
        if touches < 3: return None   # need 3 touches today

        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); rel_v=_get(df,"REL_VOL")
        if not all([price,vwap,atr,rsi]): return None

        near_vwap  = abs(price-vwap)/vwap < 0.004
        above_vwap = price > vwap
        rsi_ok     = (rsi or 50) > 42
        vol_ok     = (rel_v or 0) >= 1.0

        cur=df.iloc[-1]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        body=abs(cl-o); rng=h-l if h-l>0 else 1e-10
        bull_bar=(cl>o and body/rng>0.4)

        ALL=[f"VWAP tested {touches} times today (strong level)",
             "Price near VWAP (within 0.4%) — 3rd+ test",
             "Price held above VWAP after test",
             "RSI above 42 (not oversold)",
             "Volume present on hold bar",
             "Bullish close on hold candle"]
        checks=[True,near_vwap,above_vwap,rsi_ok,vol_ok,bull_bar]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if not (near_vwap and above_vwap): return None
        if len(met) < 4: return None

        conf = min(100, int(len(met)/len(ALL)*100)+10)
        if touches >= 4: conf = min(100, conf+10)
        return self._sig(ticker,df,"bullish",met,missed,ALL,vwap,atr,bias,
                         stop_mult=0.5,t1_mult=1.5,t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  V-U3 — VWAP RECLAIM LONG
# ══════════════════════════════════════════════════════════════

class VWAPReclaimLong(BaseVWAPStrategy):
    ID="V-U3"; NAME="VWAP Reclaim Long"
    CATEGORY="UPTREND"; SUB_TYPE="reclaim"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); rel_v=_get(df,"REL_VOL")
        prv_price=_get(df,"Close",-2); prv_vwap=_get(df,"VWAP",-2)
        prv2_price=_get(df,"Close",-3); prv2_vwap=_get(df,"VWAP",-3)
        if not all([price,vwap,atr,rsi,prv_price,prv_vwap,prv2_price,prv2_vwap]):
            return None

        was_above   = prv2_price > prv2_vwap
        dipped_below= prv_price < prv_vwap
        reclaimed   = price > vwap
        if not (was_above and dipped_below and reclaimed): return None

        dip_vol  = float(df["Volume"].iloc[-2])
        vol_ma   = _get(df,"VOL_MA") or dip_vol
        low_dip  = dip_vol < vol_ma * 0.9   # low vol on dip = no conviction
        vol_now  = (rel_v or 0) > 1.3
        rsi_ok   = (rsi or 50) > 40

        cur=df.iloc[-1]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        body=abs(cl-o); rng=h-l if h-l>0 else 1e-10
        strong_reclaim=(cl>o and body/rng>0.50)

        ALL=["Price was above VWAP (bullish session)",
             "Price dipped briefly below VWAP (false breakdown)",
             "Price reclaimed above VWAP on current bar",
             "Dip had low volume (no conviction from sellers)",
             "Reclaim bar has strong volume (> 1.3×)",
             "RSI above 40 (not oversold)",
             "Strong bullish candle on reclaim"]
        checks=[was_above,dipped_below,reclaimed,low_dip,vol_now,rsi_ok,strong_reclaim]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if not (dipped_below and reclaimed): return None
        if len(met) < 4: return None
        return self._sig(ticker,df,"bullish",met,missed,ALL,vwap,atr,bias,
                         stop_mult=0.7,t1_mult=1.5,t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  V-D1 — VWAP REJECTION SHORT (DOWNTREND)
# ══════════════════════════════════════════════════════════════

class VWAPRejectionShortDowntrend(BaseVWAPStrategy):
    ID="V-D1"; NAME="VWAP Rejection Short (downtrend)"
    CATEGORY="DOWNTREND"; SUB_TYPE="rejection"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        if bias.get("bias") not in ["downtrend","bearish_lean"]: return None

        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); rel_v=_get(df,"REL_VOL")
        ema9=_get(df,"EMA9"); ema21=_get(df,"EMA21")
        if not all([price,vwap,atr,rsi,ema9,ema21]): return None

        near_vwap   = abs(price-vwap)/vwap < 0.003
        below_vwap  = price < vwap
        ema_bear    = ema9 < ema21
        rsi_ok      = 35 <= (rsi or 50) <= 62

        cur=df.iloc[-1]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        uw=h-max(o,cl); body=abs(cl-o) if abs(cl-o)>0 else 1e-10
        rng=h-l if h-l>0 else 1e-10
        bear_candle = (uw > body*1.0 or (cl<o and body/rng>0.45))

        vol_ok     = (rel_v or 0) >= 1.2
        below_3    = bias.get("below_count",0) >= 12

        ALL=["Session bias is bearish (downtrend confirmed)",
             "Price near VWAP (within 0.3%) — rally to test",
             "Price failing to hold above VWAP (rejection)",
             "EMA9 below EMA21 (trend alignment)",
             "RSI in healthy range (35–62)",
             "Volume on rejection bar (≥ 1.2×)",
             "Bearish rejection candle at VWAP",
             "Price spent majority of session below VWAP"]
        checks=[True,near_vwap,below_vwap,ema_bear,rsi_ok,vol_ok,bear_candle,below_3]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if not (near_vwap and below_vwap): return None
        if len(met) < 4: return None
        return self._sig(ticker,df,"bearish",met,missed,ALL,vwap,atr,bias,
                         stop_mult=0.5,t1_mult=1.5,t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  V-D2 — VWAP HOLD SHORT (3RD REJECTION)
# ══════════════════════════════════════════════════════════════

class VWAPHoldShort3rdTest(BaseVWAPStrategy):
    ID="V-D2"; NAME="VWAP Hold Short (3rd rejection)"
    CATEGORY="DOWNTREND"; SUB_TYPE="hold"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        if bias.get("bias") not in ["downtrend","bearish_lean"]: return None

        touches = bias.get("vwap_touches", 0)
        if touches < 3: return None

        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); rel_v=_get(df,"REL_VOL")
        if not all([price,vwap,atr,rsi]): return None

        near_vwap  = abs(price-vwap)/vwap < 0.004
        below_vwap = price < vwap
        rsi_ok     = (rsi or 50) < 58
        vol_ok     = (rel_v or 0) >= 1.0

        cur=df.iloc[-1]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        body=abs(cl-o); rng=h-l if h-l>0 else 1e-10
        bear_bar=(cl<o and body/rng>0.4)

        ALL=[f"VWAP tested {touches} times today (strong resistance)",
             "Price near VWAP (within 0.4%) — 3rd+ test from below",
             "Price failed to hold above VWAP (rejection confirmed)",
             "RSI below 58 (not overbought)",
             "Volume present on rejection bar",
             "Bearish close on rejection candle"]
        checks=[True,near_vwap,below_vwap,rsi_ok,vol_ok,bear_bar]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if not (near_vwap and below_vwap): return None
        if len(met) < 4: return None

        conf = min(100, int(len(met)/len(ALL)*100)+10)
        if touches >= 4: conf = min(100, conf+10)
        return self._sig(ticker,df,"bearish",met,missed,ALL,vwap,atr,bias,
                         stop_mult=0.5,t1_mult=1.5,t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  V-D3 — VWAP FALSE RECLAIM SHORT
# ══════════════════════════════════════════════════════════════

class VWAPFalseReclaimShort(BaseVWAPStrategy):
    ID="V-D3"; NAME="VWAP False Reclaim Short"
    CATEGORY="DOWNTREND"; SUB_TYPE="false_reclaim"

    def check(self, ticker, df, bias):
        if len(df) < 20: return None
        price=_get(df,"Close"); vwap=_get(df,"VWAP")
        atr=_get(df,"ATR14") or (price or 100)*0.005
        rsi=_get(df,"RSI14"); rel_v=_get(df,"REL_VOL")
        prv_price=_get(df,"Close",-2); prv_vwap=_get(df,"VWAP",-2)
        prv2_price=_get(df,"Close",-3); prv2_vwap=_get(df,"VWAP",-3)
        if not all([price,vwap,atr,rsi,prv_price,prv_vwap,prv2_price,prv2_vwap]):
            return None

        was_below     = prv2_price < prv2_vwap
        briefly_above = prv_price > prv_vwap
        failed_back   = price < vwap
        if not (was_below and briefly_above and failed_back): return None

        recovery_vol = float(df["Volume"].iloc[-2])
        vol_ma  = _get(df,"VOL_MA") or recovery_vol
        low_rec = recovery_vol < vol_ma * 0.9  # low vol recovery = no conviction
        vol_now = (rel_v or 0) > 1.3
        rsi_ok  = (rsi or 50) < 55

        cur=df.iloc[-1]
        o,h,l,cl=(float(cur[c]) for c in ["Open","High","Low","Close"])
        body=abs(cl-o); rng=h-l if h-l>0 else 1e-10
        strong_fail=(cl<o and body/rng>0.50)

        ALL=["Price was below VWAP (bearish session)",
             "Price briefly recovered above VWAP (bull trap)",
             "Price failed and closed back below VWAP",
             "Recovery had low volume (no conviction from bulls)",
             "Failure bar has strong volume (> 1.3×)",
             "RSI below 55 (no real bullish momentum)",
             "Strong bearish candle on failure"]
        checks=[was_below,briefly_above,failed_back,low_rec,vol_now,rsi_ok,strong_fail]
        met=[ALL[i] for i,v in enumerate(checks) if v]
        missed=[ALL[i] for i,v in enumerate(checks) if not v]

        if not (briefly_above and failed_back): return None
        if len(met) < 4: return None
        return self._sig(ticker,df,"bearish",met,missed,ALL,vwap,atr,bias,
                         stop_mult=0.7,t1_mult=1.5,t2_mult=2.5)


# ══════════════════════════════════════════════════════════════
#  VWAP STRATEGY MODULE
# ══════════════════════════════════════════════════════════════

class VWAPStrategyModule:
    """
    Runs all 12 VWAP strategies.
    Compatible with existing bot — add with add_vwap_to_scanner().

    Usage:
        module  = VWAPStrategyModule(min_confidence=55)
        signals = module.scan("TSLA", df_5m)
        for sig in signals:
            print(sig.alert_text)
            if sig.premium_setup: print("⭐ PREMIUM!")
    """

    STRATEGIES = [
        VWAPRejectionReversalLong(),
        VWAPRejectionReversalShort(),
        VWAPSnapbackLong(),
        VWAPSnapbackShort(),
        VWAPCrossReversal(),
        VWAPBandReversal(),
        VWAPBounceLongUptrend(),
        VWAPHoldLong3rdTest(),
        VWAPReclaimLong(),
        VWAPRejectionShortDowntrend(),
        VWAPHoldShort3rdTest(),
        VWAPFalseReclaimShort(),
    ]

    CAT_ICONS = {"REVERSAL":"🔄","UPTREND":"📈","DOWNTREND":"📉"}

    def __init__(self, min_confidence: int = 55):
        self.min_confidence = min_confidence
        self.bias_detector  = SessionBiasDetector()

    def scan(self, ticker: str, df: pd.DataFrame) -> list:
        if df is None or len(df) < 20: return []

        df  = prepare_vwap_df(df)
        bias= self.bias_detector.detect(df)

        signals = []
        for strategy in self.STRATEGIES:
            try:
                sig = strategy.check(ticker, df, bias)
                if sig and sig.confidence >= self.min_confidence:
                    signals.append(sig)
            except Exception:
                pass

        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals

    def format_summary(self, ticker: str, signals: list) -> str:
        if not signals: return ""
        sep = "═"*52
        lines=[f"\n{sep}",f"  VWAP STRATEGIES — {ticker} ({len(signals)} fired)",sep]
        bias_str = signals[0].session_bias if signals else "unknown"
        lines.append(f"  Session bias: {bias_str.upper()} | "
                     f"VWAP touches today: {signals[0].vwap_touches_today}")
        lines.append("")
        for s in signals:
            e = "🟢" if s.direction=="bullish" else "🔴"
            ic= self.CAT_ICONS.get(s.category,"📊")
            pm= " ⭐" if s.premium_setup else ""
            lines.append(f"  {e} {ic} [{s.strategy_id}] {s.strategy_name:35s} "
                         f"conf:{s.confidence:3d}{pm}")
        top=signals[0]
        lines+=[f"\n  Best: {top.strategy_name}",
                f"  Entry ${top.entry} | Stop ${top.stop} | "
                f"T1 ${top.t1} | T2 ${top.t2} | R:R 1:{top.rr}",sep]
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  PLUG-IN FUNCTION
# ══════════════════════════════════════════════════════════════

def add_vwap_to_scanner(scanner, min_confidence: int = 55):
    """
    Adds all 12 VWAP strategies to your existing scanner.

    Usage:
        from strategy_engine import StrategyScanner
        from vwap_strategies import add_vwap_to_scanner

        scanner = StrategyScanner(min_confidence=55)
        add_vwap_to_scanner(scanner)

        # In your main bar loop — replaces S01-S05:
        vwap_signals = scanner.scan_vwap("TSLA", df_5m)
    """
    vwap_module = VWAPStrategyModule(min_confidence=min_confidence)

    def scan_vwap(ticker, df):
        return vwap_module.scan(ticker, df)

    scanner.scan_vwap   = scan_vwap
    scanner.vwap_module = vwap_module
    print("✅  VWAP strategies added: V-R1 through V-D3 (12 total)")
    print("    Usage: scanner.scan_vwap('TSLA', df_5m)")
    return scanner


# ══════════════════════════════════════════════════════════════
#  SELF TEST
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("="*52)
    print("  VWAP STRATEGY MODULE — 12 STRATEGIES — TEST")
    print("="*52)

    np.random.seed(42)
    n = 100

    # Simulate bullish VWAP bounce day
    base  = 418.89
    close = base + np.linspace(0, 3, n) + np.random.randn(n)*0.15
    high  = close + np.abs(np.random.randn(n)*0.2)
    low   = close - np.abs(np.random.randn(n)*0.2)
    opn   = close + np.random.randn(n)*0.1
    vol   = np.random.randint(800_000, 4_000_000, n).astype(float)
    # Simulate VWAP bounce: dip to VWAP then recover
    close[-5:] -= np.linspace(0, 0.8, 5)   # pullback
    close[-2:]  += np.array([0.4, 0.8])    # bounce
    vol[-2:]   *= 1.8

    idx = pd.date_range("2024-01-15 09:30", periods=n, freq="5min",
                         tz="America/New_York")
    df  = pd.DataFrame({"Open":opn,"High":high,"Low":low,
                         "Close":close,"Volume":vol}, index=idx)

    module  = VWAPStrategyModule(min_confidence=25)
    signals = module.scan("TSLA", df)

    if signals:
        print(module.format_summary("TSLA", signals))
        print(f"\nTop signal: {signals[0].alert_text}")
    else:
        df_ind = prepare_vwap_df(df)
        bias   = SessionBiasDetector().detect(df_ind)
        print(f"No signals fired. Session bias: {bias['bias']}")
        print(f"VWAP: ${_get(df_ind,'VWAP'):.2f} | Price: ${_get(df_ind,'Close'):.2f}")
        print("All 12 VWAP strategies loaded correctly ✅")
