"""
=============================================================
  QUANT INTELLIGENCE — STANDALONE 20-MIN SIGNAL RUNNER
  Real Alpaca data + repo strategy engine (no Docker/Redis)
  Stocks : MSFT, AVGO, META, TSLA, SPY, NOW
  Schedule: Every 20 min | 9:30 AM – 4:30 PM ET | Mon–Fri
  Telegram: Full uncut original alerts + T1–T12 table
=============================================================
"""

import sys, os, time, math, json, traceback, html as _html

def _safe_print(*args, **kwargs):
    """Print that never crashes on Windows encoding."""
    try:
        text = " ".join(str(a) for a in args)
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        else:
            print(text.encode("ascii", errors="replace").decode("ascii"))
    except Exception:
        pass

import builtins
builtins.print = _safe_print

from datetime import datetime, timedelta, date, time as dtime
from typing import Optional, List, Dict, Tuple
import requests
import pandas as pd
import numpy as np
import pytz

# ── Repo path ─────────────────────────────────────────────────
REPO_BACKEND = os.path.join(os.path.dirname(__file__), "apps", "backend")
sys.path.insert(0, REPO_BACKEND)

# ── Credentials ───────────────────────────────────────────────
ALPACA_KEY     = "PKUVZN3EUNDEDFIIWS3NHWMKXK"
ALPACA_SECRET  = "2otPyywguF8Xn2mgCwgLifEbP9s8RrbLF9mjVn95EjXo"
TELEGRAM_TOKEN = "8752800861:AAGUp376nhu0E-PoFhuKmx9-x572qUO95kw"
TELEGRAM_CHAT_ID: Optional[str] = None

STOCKS   = ["MSFT", "AVGO", "META", "TSLA", "SPY", "NOW"]
ET       = pytz.timezone("America/New_York")
INTERVAL = 20 * 60      # 20 minutes

MARKET_OPEN  = dtime(9,  30)
MARKET_CLOSE = dtime(16, 30)   # 4:30 PM ET

# ── Import repo strategy scanner ──────────────────────────────
try:
    from app.services.strategy_engine import StrategyScanner
    SCANNER = StrategyScanner(min_confidence=40)
    print("[OK] strategy_engine imported from repo")
except Exception as e:
    print(f"[WARN] strategy_engine import failed: {e}")
    SCANNER = None


# ══════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════

def tg_get_chat_id() -> Optional[str]:
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset=-1",
            timeout=5)
        results = r.json().get("result", [])
        if results:
            return str(results[-1]["message"]["chat"]["id"])
    except Exception as e:
        print(f"[TG] getUpdates error: {e}")
    return None


def tg_send(text: str, chat_id: str, parse_mode: str = "HTML") -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    MAX = 4000
    chunks = [text[i:i+MAX] for i in range(0, len(text), MAX)]
    ok = True
    for chunk in chunks:
        try:
            r = requests.post(url, json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }, timeout=10)
            if not r.ok:
                print(f"[TG] Send failed: {r.text[:300]}")
                ok = False
        except Exception as e:
            print(f"[TG] Send error: {e}")
            ok = False
    return ok


# ══════════════════════════════════════════════════════════════
#  ALPACA DATA
# ══════════════════════════════════════════════════════════════

def fetch_bars(symbol: str, timeframe: str = "1Min",
               lookback_days: int = 2) -> Optional[pd.DataFrame]:
    now_et     = datetime.now(ET)
    start_utc  = (now_et - timedelta(days=lookback_days)).replace(
                    hour=9, minute=0, second=0, microsecond=0
                 ).astimezone(pytz.utc)
    end_utc    = now_et.astimezone(pytz.utc)

    headers = {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }
    params = {
        "symbols":   symbol,
        "timeframe": timeframe,
        "start":     start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end":       end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit":     1000,
        "feed":      "iex",
        "sort":      "asc",
    }
    try:
        r = requests.get(
            "https://data.alpaca.markets/v2/stocks/bars",
            headers=headers, params=params, timeout=15)
        if not r.ok:
            print(f"[ALPACA] {symbol} HTTP {r.status_code}: {r.text[:200]}")
            return None
        bars = r.json().get("bars", {}).get(symbol, [])
        if not bars:
            print(f"[ALPACA] {symbol}: 0 bars returned")
            return None
        df = pd.DataFrame(bars)
        df.rename(columns={"t":"timestamp","o":"Open","h":"High",
                            "l":"Low","c":"Close","v":"Volume"}, inplace=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df.set_index("timestamp", inplace=True)
        df = df[["Open","High","Low","Close","Volume"]].astype(float)
        print(f"[ALPACA] {symbol}: {len(df)} bars | "
              f"last {df.index[-1].astimezone(ET).strftime('%H:%M ET')} "
              f"close ${df['Close'].iloc[-1]:.2f}")
        return df
    except Exception as e:
        print(f"[ALPACA] {symbol} fetch error: {e}")
        return None


def today_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to today's regular session (9:30–16:30 ET)."""
    today = datetime.now(ET).date()
    df_et = df.copy()
    df_et.index = df_et.index.tz_convert(ET)
    mask = (
        (df_et.index.date == today) &
        (df_et.index.time >= dtime(9, 30)) &
        (df_et.index.time <= dtime(16, 30))
    )
    return df_et[mask]


def prev_day_close(df: pd.DataFrame) -> Optional[float]:
    """Get prior session close for gap calculation."""
    today = datetime.now(ET).date()
    df_et = df.copy()
    df_et.index = df_et.index.tz_convert(ET)
    prev_bars = df_et[df_et.index.date < today]
    if prev_bars.empty:
        return None
    return float(prev_bars["Close"].iloc[-1])


# ══════════════════════════════════════════════════════════════
#  INDICATOR HELPERS
# ══════════════════════════════════════════════════════════════

def _ema(s, n):  return s.ewm(span=n, adjust=False).mean()
def _rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - (100 / (1 + g / (l + 1e-10)))
def _macd(s, fast=12, slow=26, sig=9):
    ml = _ema(s,fast) - _ema(s,slow)
    sl = _ema(ml, sig)
    return ml, sl, ml - sl
def _atr(df, n=14):
    hl  = df["High"] - df["Low"]
    hpc = (df["High"] - df["Close"].shift()).abs()
    lpc = (df["Low"]  - df["Close"].shift()).abs()
    return pd.concat([hl,hpc,lpc], axis=1).max(axis=1).rolling(n).mean()
def _vwap(df):
    tp  = (df["High"] + df["Low"] + df["Close"]) / 3
    tpv = tp * df["Volume"]
    dt  = pd.Series(df.index.date, index=df.index)
    return tpv.groupby(dt).cumsum() / df["Volume"].groupby(dt).cumsum()
def _bbands(s, n=20):
    mid = s.rolling(n).mean()
    std = s.rolling(n).std()
    return mid+2*std, mid, mid-2*std
def _stoch(df, k=14, d=3):
    lo  = df["Low"].rolling(k).min()
    hi  = df["High"].rolling(k).max()
    pct = 100*(df["Close"]-lo)/(hi-lo+1e-10)
    return pct, pct.rolling(d).mean()

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA9"]      = _ema(df["Close"], 9)
    df["EMA20"]     = _ema(df["Close"], 20)
    df["EMA21"]     = _ema(df["Close"], 21)
    df["EMA50"]     = _ema(df["Close"], 50)
    df["RSI"]       = _rsi(df["Close"], 14)
    df["VWAP"]      = _vwap(df)
    df["ATR"]       = _atr(df, 14)
    df["VOL_MA20"]  = df["Volume"].rolling(20).mean()
    df["REL_VOL"]   = df["Volume"] / (df["VOL_MA20"] + 1e-10)
    macd, msig, mhist = _macd(df["Close"])
    df["MACD"]      = macd
    df["MACD_SIG"]  = msig
    df["MACD_HIST"] = mhist
    bbu, bbm, bbl   = _bbands(df["Close"], 20)
    df["BB_UP"]     = bbu; df["BB_MID"] = bbm; df["BB_LO"] = bbl
    stk, std        = _stoch(df, 14, 3)
    df["STC_K"]     = stk; df["STC_D"] = std
    df["EMA9_SLOPE"]= df["EMA9"] - df["EMA9"].shift(3)
    return df

def _v(df, col, idx=-1, default=None):
    try:
        v = df[col].iloc[idx]
        return None if (isinstance(v, float) and math.isnan(v)) else float(v)
    except:
        return default


# ══════════════════════════════════════════════════════════════
#  T1–T12 SIGNAL CALCULATORS
# ══════════════════════════════════════════════════════════════

def t1_price_signal(df_today: pd.DataFrame, df_ind: pd.DataFrame,
                    prior_close: Optional[float]) -> Tuple[str, str]:
    """Returns (signal_text, key_level)."""
    price = _v(df_ind, "Close")
    atr   = _v(df_ind, "ATR") or price * 0.005
    res   = round(float(df_today["High"].tail(20).max()), 2)
    sup   = round(float(df_today["Low"].tail(20).min()), 2)

    # Gap
    gap_str = ""
    if prior_close:
        first_open = float(df_today["Open"].iloc[0])
        gap_pct    = (first_open - prior_close) / prior_close * 100
        if abs(gap_pct) >= 0.4:
            gap_dir = "Up" if gap_pct > 0 else "Down"
            holding = (price > first_open) if gap_pct > 0 else (price < first_open)
            status  = "Holding" if holding else "Filling"
            gap_str = f" | Gap {gap_dir} {gap_pct:+.1f}% ({status})"

    if price >= res * 0.999 and len(df_today) > 5:
        return f"Breakout ${res:.2f}{gap_str}", f"Resistance ${res:.2f}"
    elif price <= sup * 1.001 and len(df_today) > 5:
        return f"Breakdown ${sup:.2f}{gap_str}", f"Support ${sup:.2f}"
    elif abs(price - res) < atr * 0.8:
        return f"Rejection at Resistance ${res:.2f}{gap_str}", f"Resistance ${res:.2f}"
    elif abs(price - sup) < atr * 0.8:
        return f"Bounce from Support ${sup:.2f}{gap_str}", f"Support ${sup:.2f}"
    elif abs(price - sup) < atr * 2:
        return f"Pullback to Support ${sup:.2f}{gap_str}", f"Support ${sup:.2f}"
    else:
        mid = round((res + sup) / 2, 2)
        return f"Mid-Range ${sup:.2f}–${res:.2f}{gap_str}", f"Mid ${mid:.2f}"


def t2_open_drive(df_today: pd.DataFrame) -> str:
    if len(df_today) < 3:
        return "N/A"
    open_p = float(df_today["Open"].iloc[0])
    # First 15 bars (1-min) = 15 minutes
    first15 = df_today.head(15)
    if len(first15) < 3:
        first15 = df_today.head(max(3, len(df_today)//4))

    f_high   = float(first15["High"].max())
    f_low    = float(first15["Low"].min())
    f_close  = float(first15["Close"].iloc[-1])
    now_p    = float(df_today["Close"].iloc[-1])
    pct_move = (f_close - open_p) / open_p * 100

    # False breakout / reversal in 9:30–9:40 window
    if len(first15) >= 10:
        early5  = df_today.head(5)
        early_hi = float(early5["High"].max())
        early_lo = float(early5["Low"].min())
        later10  = df_today.head(10)
        later_p  = float(later10["Close"].iloc[-1])
        if early_hi > open_p * 1.002 and later_p < open_p:
            return "Failed Drive / False Breakout (9:30–9:40)"
        if early_lo < open_p * 0.998 and later_p > open_p:
            return "Failed Drive / False Breakdown (9:30–9:40)"

    # Sustained drives
    if pct_move > 0.35:
        # Check no major pullback (low stayed above 40% of range)
        no_pullback = f_low > open_p - (f_high - open_p) * 0.35
        if no_pullback:
            return "Drive Up (sustained, no pullback)"
        else:
            return "Failed Drive (reversed from up)"
    elif pct_move < -0.35:
        no_pullback = f_high < open_p + (open_p - f_low) * 0.35
        if no_pullback:
            return "Drive Down (sustained, no pullback)"
        else:
            return "Failed Drive (reversed from down)"

    # Auction: rotated both ways
    rng_pct = (f_high - f_low) / open_p * 100
    if rng_pct > 0.5 and abs(pct_move) < 0.2:
        return "Open Auction (rotates both ways)"
    return "No Drive (flat/indecisive)"


def t3_momentum(df_today: pd.DataFrame, df_ind: pd.DataFrame) -> str:
    if len(df_today) < 5:
        return "N/A"
    open_p  = float(df_today["Open"].iloc[0])
    price   = _v(df_ind, "Close")
    rsi     = _v(df_ind, "RSI") or 50
    rel_vol = _v(df_ind, "REL_VOL") or 1.0
    pct     = (price - open_p) / open_p * 100

    if pct >= 2.0 and rel_vol >= 3.0 and rsi > 65:
        return f"Strong Bull ({pct:+.1f}% | RSI {rsi:.0f} | {rel_vol:.1f}x vol)"
    elif pct >= 0.5 and rel_vol >= 1.2 and 55 <= rsi <= 65:
        return f"Bull ({pct:+.1f}% | RSI {rsi:.0f} | {rel_vol:.1f}x vol)"
    elif pct <= -2.0 and rel_vol >= 3.0 and rsi < 35:
        return f"Strong Bear ({pct:+.1f}% | RSI {rsi:.0f} | {rel_vol:.1f}x vol)"
    elif pct <= -0.5 and rel_vol >= 1.2 and 35 <= rsi <= 45:
        return f"Bear ({pct:+.1f}% | RSI {rsi:.0f} | {rel_vol:.1f}x vol)"
    else:
        return f"Neutral ({pct:+.1f}% | RSI {rsi:.0f} | {rel_vol:.1f}x vol)"


def t4_scalping(df_today: pd.DataFrame, df_ind: pd.DataFrame) -> str:
    price  = _v(df_ind, "Close")
    vwap   = _v(df_ind, "VWAP")
    ema9   = _v(df_ind, "EMA9")
    slope  = _v(df_ind, "EMA9_SLOPE") or 0
    rsi    = _v(df_ind, "RSI") or 50
    atr    = _v(df_ind, "ATR") or price * 0.005

    last5 = df_today["Close"].tail(5)
    clean_up   = all(last5.iloc[i] <= last5.iloc[i+1] for i in range(len(last5)-1))
    clean_down = all(last5.iloc[i] >= last5.iloc[i+1] for i in range(len(last5)-1))
    above_vwap = vwap and price > vwap
    above_ema9 = ema9 and price > ema9

    if clean_up and above_vwap and above_ema9 and slope > 0:
        return "Long Bias (clean uptrend + above VWAP + EMA9)"
    if clean_down and not above_vwap and not above_ema9 and slope < 0:
        return "Short Bias (clean downtrend + below VWAP + EMA9)"

    # Range check
    recent_rng = float(df_today["High"].tail(10).max() - df_today["Low"].tail(10).min())
    if recent_rng < atr * 5 and 40 < rsi < 60:
        sup10 = round(float(df_today["Low"].tail(10).min()), 2)
        res10 = round(float(df_today["High"].tail(10).max()), 2)
        return f"Range (${sup10}–${res10}, scalp both sides)"

    if not (clean_up or clean_down) and 45 < rsi < 55:
        return "Avoid (choppy, no clear structure)"

    if above_vwap and slope > 0:
        return "Long Bias (above VWAP + EMA9 slope)"
    if not above_vwap and slope < 0:
        return "Short Bias (below VWAP + EMA9 slope)"
    return "Range (mixed signals)"


def t5_0dte(df_ind: pd.DataFrame) -> str:
    price  = _v(df_ind, "Close")
    vwap   = _v(df_ind, "VWAP")
    ema9   = _v(df_ind, "EMA9")
    ema21  = _v(df_ind, "EMA21")
    rsi    = _v(df_ind, "RSI") or 50
    atr    = _v(df_ind, "ATR") or price * 0.005

    # Nearest gamma strike ($1 for <$200, $5 for >$200)
    stride = 5 if price > 200 else 1
    gamma  = round(price / stride) * stride
    above_gamma = price > gamma

    bull_int = (vwap and price > vwap) and (ema9 and ema21 and ema9 > ema21)
    bear_int = (vwap and price < vwap) and (ema9 and ema21 and ema9 < ema21)
    near_vwap = vwap and abs(price - vwap) < atr * 0.5

    if near_vwap:
        return f"Neutral (${gamma:.0f} strike, near VWAP ${vwap:.2f})"
    if bull_int and above_gamma and rsi > 50:
        return f"Call - ${gamma:.0f} (above VWAP + EMA aligned)"
    if bear_int and not above_gamma and rsi < 50:
        return f"Put - ${gamma:.0f} (below VWAP + EMA aligned)"
    if bull_int:
        return f"Call - ${gamma:.0f} (bullish internals)"
    if bear_int:
        return f"Put - ${gamma:.0f} (bearish internals)"
    return f"Neutral - ${gamma:.0f} (mixed internals)"


def t6_volume(df_today: pd.DataFrame, df_ind: pd.DataFrame) -> str:
    rel_vol = _v(df_ind, "REL_VOL") or 1.0
    vols    = df_today["Volume"].tail(5).tolist()

    # Climax: big spike then drying up
    if len(vols) >= 3 and vols[-2] > vols[-3] * 2 and vols[-1] < vols[-2] * 0.5:
        return f"Climax ({rel_vol:.1f}x) — exhaustion likely"
    if rel_vol >= 3.0:
        return f"Spike ({rel_vol:.1f}x avg) — strong conviction"
    if rel_vol >= 1.5:
        return f"Above Average ({rel_vol:.1f}x avg)"
    if rel_vol <= 0.4:
        return f"Dry Up ({rel_vol:.1f}x avg) — weak interest"
    return f"Average ({rel_vol:.1f}x avg)"


def t7_vwap(df_ind: pd.DataFrame) -> str:
    price = _v(df_ind, "Close")
    vwap  = _v(df_ind, "VWAP")
    atr   = _v(df_ind, "ATR") or price * 0.005
    if not vwap:
        return "N/A"
    pct = (price - vwap) / vwap * 100

    if abs(price - vwap) < atr * 0.3:
        return f"Testing VWAP (${vwap:.2f})"

    prev_close = _v(df_ind, "Close", idx=-2)
    try:
        prev_vwap = float(df_ind["VWAP"].iloc[-2])
    except:
        prev_vwap = None

    if prev_close and prev_vwap:
        if price > vwap and prev_close < prev_vwap:
            return f"Reclaimed VWAP (${vwap:.2f}) — bullish flip"
        if price < vwap and prev_close > prev_vwap:
            return f"Lost VWAP (${vwap:.2f}) — bearish flip"

    if price > vwap:
        return f"Above VWAP (${vwap:.2f}, +{pct:.2f}%)"
    return f"Below VWAP (${vwap:.2f}, {pct:.2f}%)"


def t8_rsi(df_ind: pd.DataFrame) -> str:
    rsi      = _v(df_ind, "RSI")
    prev_rsi = _v(df_ind, "RSI", idx=-2) or rsi
    if rsi is None:
        return "N/A"
    if rsi > 70:
        return f"Overbought ({rsi:.1f}) — pullback risk"
    if rsi < 30:
        return f"Oversold ({rsi:.1f}) — bounce watch"
    if rsi > 55 and rsi > (prev_rsi or rsi):
        return f"Bullish ({rsi:.1f} and rising)"
    if rsi < 45 and rsi < (prev_rsi or rsi):
        return f"Bearish ({rsi:.1f} and falling)"
    return f"Neutral ({rsi:.1f})"


def t9_macd(df_ind: pd.DataFrame) -> str:
    macd  = _v(df_ind, "MACD")
    sig   = _v(df_ind, "MACD_SIG")
    hist  = _v(df_ind, "MACD_HIST")
    pm    = _v(df_ind, "MACD", idx=-2)
    ps    = _v(df_ind, "MACD_SIG", idx=-2)
    ph    = _v(df_ind, "MACD_HIST", idx=-2)
    if None in [macd, sig, hist]:
        return "N/A"

    if pm is not None and ps is not None:
        if pm < ps and macd > sig:
            return f"Bullish Cross (MACD {macd:.3f} x Signal {sig:.3f})"
        if pm > ps and macd < sig:
            return f"Bearish Cross (MACD {macd:.3f} x Signal {sig:.3f})"

    # Divergence: price up but MACD hist declining (bearish div) or vice versa
    close_now  = _v(df_ind, "Close")
    close_prev = _v(df_ind, "Close", idx=-5)
    if close_now and close_prev and ph is not None:
        if close_now > close_prev and hist < ph:
            return f"Bearish Divergence (hist {hist:.3f} declining)"
        if close_now < close_prev and hist > ph:
            return f"Bullish Divergence (hist {hist:.3f} rising)"

    if hist > 0 and ph and hist > ph:
        return f"Bullish Momentum (hist {hist:.3f})"
    if hist < 0 and ph and hist < ph:
        return f"Bearish Momentum (hist {hist:.3f})"
    if hist > 0:
        return f"Above Zero (hist {hist:.3f})"
    return f"Below Zero (hist {hist:.3f})"


def t10_candlestick(df_today: pd.DataFrame) -> str:
    if len(df_today) < 2:
        return "N/A"
    cur = df_today.iloc[-1]
    prv = df_today.iloc[-2]
    o, h, l, c   = float(cur.Open), float(cur.High), float(cur.Low), float(cur.Close)
    po, ph, pl, pc = float(prv.Open), float(prv.High), float(prv.Low), float(prv.Close)
    body = abs(c - o)
    rng  = (h - l) if (h - l) > 0 else 1e-10
    lw   = min(o, c) - l
    uw   = h - max(o, c)
    bull = c >= o

    if body / rng < 0.1:
        return "Doji — Neutral (indecision)"
    if lw > body * 2 and uw < body * 0.5:
        return "Hammer — Bullish (reversal at support)"
    if uw > body * 2 and lw < body * 0.5:
        return "Shooting Star — Bearish (reversal at resistance)"
    if bull and c > po and o < pc and body > abs(pc - po):
        return "Bullish Engulfing — Bullish"
    if not bull and c < po and o > pc and body > abs(pc - po):
        return "Bearish Engulfing — Bearish"
    if lw / rng > 0.60 and body / rng < 0.25:
        return "Bullish Pin Bar — Bullish"
    if uw / rng > 0.60 and body / rng < 0.25:
        return "Bearish Pin Bar — Bearish"
    if len(df_today) >= 3:
        b3 = df_today.iloc[-3]
        if float(b3.Close) < float(b3.Open) and body / rng > 0.5 and bull:
            return "Morning Star — Bullish (3-bar reversal)"
        if float(b3.Close) > float(b3.Open) and body / rng > 0.5 and not bull:
            return "Evening Star — Bearish (3-bar reversal)"
    if h < ph and l > pl:
        return "Inside Bar — Neutral (breakout pending)"
    if body / rng > 0.70:
        return f"{'Strong Bullish Bar' if bull else 'Strong Bearish Bar'} — {'Bullish' if bull else 'Bearish'}"
    return f"{'Bullish' if bull else 'Bearish'} Bar ({body/rng:.0%} body) — {'Bullish' if bull else 'Bearish'}"


def derive_bias_confidence(signals: Dict[str, str]) -> Tuple[str, str, int, int]:
    """Return (bias_label, conf_label, bull_count, bear_count)."""
    bull = 0; bear = 0
    bull_kws = ["Bull", "Long Bias", "Call", "Above VWAP", "Reclaimed",
                "Breakout", "Bounce", "Drive Up", "Bullish", "Spike",
                "Oversold", "Strong Buy", "Morning Star", "Hammer", "Engulf"]
    bear_kws = ["Bear", "Short Bias", "Put", "Below VWAP", "Lost VWAP",
                "Breakdown", "Rejection", "Drive Down", "Bearish",
                "Overbought", "Strong Sell", "Evening Star", "Shooting"]
    for v in signals.values():
        if any(k in v for k in bull_kws): bull += 1
        if any(k in v for k in bear_kws): bear += 1

    net = bull - bear
    if net >= 5:   bias = "Strong Buy"
    elif net >= 2: bias = "Buy"
    elif net <= -5: bias = "Strong Sell"
    elif net <= -2: bias = "Sell"
    else:          bias = "Neutral"

    aligned = max(bull, bear)
    if aligned >= 5:   conf = "High"
    elif aligned >= 3: conf = "Medium"
    else:              conf = "Low"

    return bias, conf, bull, bear


# ══════════════════════════════════════════════════════════════
#  STRATEGY ENGINE SCAN
# ══════════════════════════════════════════════════════════════

def run_strategy_engine(symbol: str, df_utc: pd.DataFrame) -> Dict:
    if SCANNER is None or len(df_utc) < 30:
        return {"fired": [], "bull": 0, "bear": 0, "top": None}
    try:
        df = df_utc.copy()
        if df.index.tz is not None and str(df.index.tz) != "UTC":
            df.index = df.index.tz_convert("UTC")
        signals = SCANNER.scan(symbol, df, "5m")
        b = sum(1 for s in signals if s.direction == "bullish")
        r = sum(1 for s in signals if s.direction == "bearish")
        return {"fired": signals, "bull": b, "bear": r,
                "top": signals[0] if signals else None}
    except Exception as e:
        print(f"[SCANNER] {symbol}: {e}")
        return {"fired": [], "bull": 0, "bear": 0, "top": None}


# ══════════════════════════════════════════════════════════════
#  FULL UNCUT TELEGRAM ALERT (original candle_engine format)
# ══════════════════════════════════════════════════════════════

def format_full_bot_alert(symbol: str, scan: Dict,
                           df_ind: pd.DataFrame,
                           now_et: datetime) -> Optional[str]:
    import html as h
    top     = scan.get("top")
    signals = scan.get("fired", [])
    if not top or not signals:
        return None

    direction = top.direction
    action    = "BUY" if direction == "bullish" else "SELL"
    emoji     = "🟢" if action == "BUY" else "🔴"
    price     = top.price
    vwap_v    = _v(df_ind, "VWAP") or price
    rsi_v     = _v(df_ind, "RSI") or 50
    atr_v     = _v(df_ind, "ATR") or price * 0.005

    mult = 1 if direction == "bullish" else -1
    stop = round(price - mult * atr_v,       2)
    t1   = round(price + mult * atr_v * 1.5, 2)
    t2   = round(price + mult * atr_v * 2.5, 2)
    risk = abs(price - stop)
    rr   = round(abs(t1 - price) / risk, 2) if risk > 0 else 0

    # Session time classifier
    t_obj = now_et.time()
    if t_obj >= dtime(14, 0) or rr < 1.5:
        trade_type = "0DTE Scalp (exit today)"
    elif rr >= 2.0 and t_obj < dtime(11, 30):
        trade_type = "Intraday (same-day exit)"
    else:
        trade_type = "Intraday (same-day exit)"

    exit_price = t1
    exit_note  = "Exit at T1, trail stop to entry after T1 hit"

    bull = scan["bull"]; bear = scan["bear"]
    strategies_str = "\n".join(
        f"  • [{s.strategy_id}] {h.escape(s.strategy_name)}"
        for s in signals[:6]
    )
    conditions_str = "\n".join(
        f"  ✅ {h.escape(str(c))}" for c in top.conditions_met[:5]
    )
    exp_lo = round(price - atr_v * 2.5, 2)
    exp_hi = round(price + atr_v * 2.5, 2)
    rng_str = f"${exp_lo:.2f} → ${exp_hi:.2f}"

    text = (
        f"{emoji} <b>{h.escape(symbol)} {action} ALERT</b> — {now_et.strftime('%H:%M ET')}\n"
        f"─────────────────────────────\n"
        f"<b>Top Strategy:</b> [{top.strategy_id}] {h.escape(top.strategy_name)}\n"
        f"<b>Consensus:</b> {bull} Bull / {bear} Bear\n"
        f"<b>Trade Type:</b> {h.escape(trade_type)}\n\n"
        f"<b>Fired strategies:</b>\n{strategies_str}\n\n"
        f"<b>Trade levels:</b>\n"
        f"Entry: ${price:.2f} | Stop: ${stop:.2f}\n"
        f"T1: ${t1:.2f} | T2: ${t2:.2f} | R:R 1:{rr}\n"
        f"Exit: ${exit_price:.2f}  <i>({h.escape(exit_note)})</i>\n"
        f"Expected range: {h.escape(rng_str)}\n\n"
        f"<b>Conditions met ({top.score}/{top.max_score}):</b>\n{conditions_str}\n\n"
        f"Confidence: {top.confidence}/100\n"
        f"VWAP: ${vwap_v:.2f} | RSI: {rsi_v:.1f} | ATR: ${atr_v:.2f}\n\n"
        f"⚠ <i>Educational only — not financial advice</i>"
    )
    return text


# ══════════════════════════════════════════════════════════════
#  T1–T12 TABLE FORMATTER (Telegram)
# ══════════════════════════════════════════════════════════════

def e(s): return _html.escape(str(s))   # shorthand

def format_table_message(sym: str, price: float,
                         t: Dict[str, str], bias: str, conf: str,
                         bull: int, bear: int,
                         sup: float, res: float,
                         vwap_v: float, rsi_v: float, atr_v: float,
                         scan: Dict, now_et: datetime,
                         t1_key_level: str) -> str:
    flag = ""
    if conf == "High" and ("Strong" in bias or bull >= 5 or bear >= 5):
        flag = " ⭐"
    elif conf == "Low" and bias == "Neutral":
        flag = " ⚠"

    bias_emoji = "🟢" if "Buy" in bias else ("🔴" if "Sell" in bias else "🟡")
    conf_emoji = "🟢" if conf == "High" else ("🟡" if conf == "Medium" else "🔴")

    # Bias emoji for each signal
    def bv(s):
        bull_kws = ["Bull","Long","Call","Above","Reclaimed","Breakout","Bounce",
                    "Drive Up","Bullish","Spike","Oversold","Hammer","Engulf",
                    "Morning","Pin Bar — B","Strong Bullish"]
        bear_kws = ["Bear","Short","Put","Below","Lost","Breakdown","Rejection",
                    "Drive Down","Bearish","Overbought","Shooting","Evening",
                    "Pin Bar — Bear","Strong Bearish"]
        if any(k in s for k in bull_kws): return "🟢 "
        if any(k in s for k in bear_kws): return "🔴 "
        return "🟡 "

    lines = [
        f"{'═'*48}",
        f"## {sym}{flag}",
        f"<b>${price:.2f}</b>  |  {now_et.strftime('%H:%M ET')}  "
        f"|  Bias: {bias_emoji} <b>{e(bias)}</b>",
        f"{'─'*48}",
        "",
        f"<b>T1  Price Signal:</b>",
        f"  {bv(t['t1'])}{e(t['t1'])}",
        f"<b>T2  Open Drive:</b>",
        f"  {bv(t['t2'])}{e(t['t2'])}",
        f"<b>T3  Momentum:</b>",
        f"  {bv(t['t3'])}{e(t['t3'])}",
        f"<b>T4  Scalping:</b>",
        f"  {bv(t['t4'])}{e(t['t4'])}",
        f"<b>T5  0DTE:</b>",
        f"  {bv(t['t5'])}{e(t['t5'])}",
        f"<b>T6  Volume:</b>",
        f"  {bv(t['t6'])}{e(t['t6'])}",
        f"<b>T7  VWAP:</b>",
        f"  {bv(t['t7'])}{e(t['t7'])}",
        f"<b>T8  RSI:</b>",
        f"  {bv(t['t8'])}{e(t['t8'])}",
        f"<b>T9  MACD:</b>",
        f"  {bv(t['t9'])}{e(t['t9'])}",
        f"<b>T10 Candlestick:</b>",
        f"  {bv(t['t10'])}{e(t['t10'])}",
        f"{'─'*48}",
        f"<b>T11 Intraday Bias:</b>  {bias_emoji} <b>{e(bias)}</b>  "
        f"  ({bull}🟢 / {bear}🔴 signals)",
        f"<b>T12 Confidence:</b>     {conf_emoji} <b>{e(conf)}</b>",
        f"{'─'*48}",
        f"",
        f"📊 <b>Signal Summary:</b>",
    ]

    # Line 1: Overall bias + strongest signal
    strongest_key = max(t, key=lambda k: len(t[k]))  # longest = most detail
    lines.append(f"→ {bias_emoji} <b>{e(bias)}</b> confluence — strongest: {bv(t[strongest_key])}{e(t[strongest_key])}")

    # Line 2: Key level
    lines.append(f"→ Key level: <b>{e(t1_key_level)}</b>  "
                 f"| Sup ${sup:.2f}  Res ${res:.2f}  VWAP ${vwap_v:.2f}")

    # Bot strategy engine line (if fired)
    if scan["fired"]:
        top = scan["top"]
        lines.append(f"")
        lines.append(f"🤖 <b>Bot Engine:</b> {scan['bull']}B/{scan['bear']}S "
                     f"| Top: [{top.strategy_id}] {e(top.strategy_name)} "
                     f"({top.confidence}/100)")
    lines.append(f"{'═'*48}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  FULL ANALYSIS PER STOCK
# ══════════════════════════════════════════════════════════════

def analyze_stock(symbol: str) -> Optional[Dict]:
    print(f"[→] {symbol} ...")
    df_raw = fetch_bars(symbol, "1Min", lookback_days=2)
    if df_raw is None or len(df_raw) < 30:
        return None

    df_today = today_bars(df_raw)
    if len(df_today) < 5:
        print(f"[!] {symbol}: insufficient today bars ({len(df_today)})")
        return None

    prior_close = prev_day_close(df_raw)
    df_ind      = compute_indicators(df_today)
    price       = _v(df_ind, "Close")
    if price is None:
        return None

    t1_sig, t1_level = t1_price_signal(df_today, df_ind, prior_close)

    sigs = {
        "t1":  t1_sig,
        "t2":  t2_open_drive(df_today),
        "t3":  t3_momentum(df_today, df_ind),
        "t4":  t4_scalping(df_today, df_ind),
        "t5":  t5_0dte(df_ind),
        "t6":  t6_volume(df_today, df_ind),
        "t7":  t7_vwap(df_ind),
        "t8":  t8_rsi(df_ind),
        "t9":  t9_macd(df_ind),
        "t10": t10_candlestick(df_today),
    }

    bias, conf, bull, bear = derive_bias_confidence(sigs)
    sigs["t11"] = bias
    sigs["t12"] = conf

    sup  = round(float(df_today["Low"].tail(20).min()), 2)
    res  = round(float(df_today["High"].tail(20).max()), 2)
    vwap = _v(df_ind, "VWAP") or price
    rsi  = _v(df_ind, "RSI") or 50
    atr  = _v(df_ind, "ATR") or price * 0.005

    scan = run_strategy_engine(symbol, df_raw)

    return {
        "symbol": symbol, "price": price,
        "sigs": sigs, "bias": bias, "conf": conf,
        "bull": bull, "bear": bear,
        "sup": sup, "res": res,
        "vwap": vwap, "rsi": rsi, "atr": atr,
        "t1_level": t1_level,
        "scan": scan,
        "df_ind": df_ind,
    }


# ══════════════════════════════════════════════════════════════
#  RUN HEADER / FOOTER
# ══════════════════════════════════════════════════════════════

def run_header(now_et: datetime, run_num: int) -> str:
    return (
        f"{'═'*48}\n"
        f"🔔 <b>QUANT INTELLIGENCE — RUN #{run_num}</b>\n"
        f"📅 {now_et.strftime('%A, %b %d %Y')}  |  "
        f"{now_et.strftime('%H:%M ET')}\n"
        f"📋 Stocks: {', '.join(STOCKS)}\n"
        f"⏰ Auto-running every 20 min | 9:30–4:30 PM ET\n"
        f"{'═'*48}"
    )


# ══════════════════════════════════════════════════════════════
#  TIMING HELPERS
# ══════════════════════════════════════════════════════════════

def is_market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def sleep_until_open():
    now = datetime.now(ET)
    # Find next weekday market open
    for days_ahead in range(1, 8):
        candidate = now + timedelta(days=days_ahead)
        if candidate.weekday() < 5:
            next_open = ET.localize(datetime.combine(
                candidate.date(), MARKET_OPEN))
            wait = (next_open - datetime.now(ET)).total_seconds()
            print(f"[CLOSED] Market closed. Next open: "
                  f"{next_open.strftime('%a %b %d %H:%M ET')} "
                  f"(in {wait/3600:.1f}h)")
            # Sleep in 60-second chunks
            while wait > 0:
                time.sleep(min(60, wait))
                wait -= 60
            return


def next_run_time() -> datetime:
    """Align to next 20-min mark from 9:30 ET."""
    now   = datetime.now(ET)
    base  = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now < base:
        return base
    elapsed   = (now - base).total_seconds()
    intervals = int(elapsed // INTERVAL) + 1
    return base + timedelta(seconds=intervals * INTERVAL)


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    global TELEGRAM_CHAT_ID

    print("=" * 50)
    print("  QUANT INTELLIGENCE SIGNAL RUNNER")
    print(f"  Stocks  : {', '.join(STOCKS)}")
    print(f"  Schedule: Every 20 min | 9:30–4:30 PM ET")
    print("=" * 50)

    # Detect Telegram chat ID
    print("\n[TG] Detecting chat ID...")
    for attempt in range(12):
        TELEGRAM_CHAT_ID = tg_get_chat_id()
        if TELEGRAM_CHAT_ID:
            break
        if attempt == 0:
            print("[TG] Send /start to your Telegram bot now...")
        time.sleep(5)

    if TELEGRAM_CHAT_ID:
        print(f"[TG] Chat ID: {TELEGRAM_CHAT_ID} [OK]")
        tg_send(
            "🤖 <b>Quant Intelligence started!</b>\n"
            f"Monitoring: {', '.join(STOCKS)}\n"
            f"Signals every 20 min | 9:30 AM – 4:30 PM ET\n"
            "First run starting now...",
            TELEGRAM_CHAT_ID
        )
    else:
        print("[TG] No chat ID found — printing to console only")

    run_num = 0

    while True:
        # Wait for market hours
        if not is_market_open():
            sleep_until_open()

        run_num += 1
        now_et = datetime.now(ET)
        print(f"\n{'='*50}")
        print(f"  RUN #{run_num}  |  {now_et.strftime('%H:%M ET')}")
        print(f"{'='*50}")

        # ── Per-stock analysis ────────────────────────────────
        table_parts  = [run_header(now_et, run_num)]
        full_alerts  = []

        for sym in STOCKS:
            try:
                r = analyze_stock(sym)
                if r is None:
                    msg = (f"⚠ <b>{sym}</b>: No data at "
                           f"{now_et.strftime('%H:%M ET')}")
                    table_parts.append(msg)
                    print(f"  ✗ {sym}: no data")
                    continue

                # T1-T12 table
                table_msg = format_table_message(
                    sym, r["price"], r["sigs"],
                    r["bias"], r["conf"], r["bull"], r["bear"],
                    r["sup"], r["res"], r["vwap"], r["rsi"], r["atr"],
                    r["scan"], now_et, r["t1_level"]
                )
                table_parts.append(table_msg)
                print(f"  ✓ {sym}: ${r['price']:.2f} | "
                      f"{r['bias']} | {r['conf']}")

                # Full bot alert (if strategy engine fired)
                if r["scan"]["fired"]:
                    full_alert = format_full_bot_alert(
                        sym, r["scan"], r["df_ind"], now_et)
                    if full_alert:
                        full_alerts.append(full_alert)

            except Exception as ex:
                table_parts.append(
                    f"⚠ <b>{sym}</b>: Error — {e(str(ex)[:120])}")
                print(f"  ✗ {sym}: {ex}")
                traceback.print_exc()

        # ── Send to Telegram ──────────────────────────────────
        if TELEGRAM_CHAT_ID:
            # Send all tables together (split if too long)
            combined = "\n\n".join(table_parts)
            print(f"\n[TG] Sending table ({len(combined)} chars)...")
            tg_send(combined, TELEGRAM_CHAT_ID)

            # Send each full strategy alert individually (uncut)
            for alert in full_alerts:
                print(f"[TG] Sending full strategy alert...")
                tg_send(alert, TELEGRAM_CHAT_ID)
        else:
            print("\n" + "\n\n".join(table_parts))

        # ── Sleep to next 20-min mark ─────────────────────────
        nxt     = next_run_time()
        now_et  = datetime.now(ET)
        sleep_s = max(5, (nxt - now_et).total_seconds())
        print(f"\n[NEXT] Run #{run_num+1} at {nxt.strftime('%H:%M ET')} "
              f"(in {sleep_s/60:.1f} min)")
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
