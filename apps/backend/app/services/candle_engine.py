import asyncio
import json
import logging
import math
import time
from collections import deque
from datetime import datetime, timedelta, time as dtime
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd
import httpx
import pytz

from app.core.config import settings
from app.core.redis_client import redis_client
from app.services.bot_upgrade import (
    AlertFormatter,
    CandlePatternEngine,
    IndicatorEngine,
    InsightGenerator,
    MultiTimeframeEngine,
    PriceRangeEngine,
    ProbabilityEngine,
    RiskEngine,
    VolumeEngine,
    VolatilityEngine,
)
from app.services.strategy_engine import StrategyScanner, StrategySignal
from app.services.opening_drive import OpeningDriveModule, OpeningDriveSignal
from app.services.sr_strategies import SRStrategyModule, SRSignal as SRStrategySignal
from app.services.price_fix import is_price_sane, validate_dataframe

logger = logging.getLogger("app.services.candle_engine")

# ── Redis channels ─────────────────────────────────────────────
REDIS_TICK_CHANNEL   = "market:ticks"
REDIS_CANDLE_CHANNEL = "market:candles"
REDIS_ALERT_CHANNEL  = "signals:alerts"

# ── Candle accumulation ────────────────────────────────────────
CANDLE_WINDOW_SECS  = 5
CANDLE_HISTORY_SIZE = 1440   # 2 h of 5-second candles

# ── Session time (ET) ─────────────────────────────────────────
ET = pytz.timezone("America/New_York")

# Strategies active in each intraday session window (Part 11 of guide)
_ALL_18 = {"S01","S02","S03","S04","S05","S06","S07",
           "S08","S09","S10","S11","S12","S13","S14","S15",
           "S16","S17","S18"}

# S16/S17/S18 (9 EMA strategies) best: 9:45–11:30am and 2–3:30pm ET
# Guide explicitly says skip 12pm–2pm lunch chop for these
_EMA9 = {"S16","S17","S18"}

SESSION_MAP: List[tuple] = [
    # (start, end, allowed_strategy_ids)
    (dtime(9, 30), dtime(9, 35),  set()),                                      # wild open — observe only
    (dtime(9, 35), dtime(10, 0),  {"S03","S04","S08","S13"}),                  # opening momentum
    (dtime(10, 0), dtime(11, 0),  _ALL_18),                                    # primary session (all 18)
    (dtime(11, 0), dtime(12, 0),  {"S01","S02","S11","S12"} | _EMA9),          # mid-morning + EMA9
    (dtime(12, 0), dtime(14, 0),  {"S05"}),                                    # lunch — MR only, skip EMA9
    (dtime(14, 0), dtime(15, 30), {"S01","S02","S11","S12","S15"} | _EMA9),    # afternoon + EMA9
    (dtime(15,30), dtime(15, 45), {"S08","S13","S15"}),                        # power hour close
    (dtime(15,45), dtime(16, 0),  set()),                                       # close — exit only
]


def _session_allowed(now_et: datetime) -> Set[str]:
    """Return the set of strategy IDs allowed for the current ET time."""
    t = now_et.time()
    for start, end, ids in SESSION_MAP:
        if start <= t < end:
            return ids
    return set()   # outside market hours


# Per-strategy minimum confidence (Part 7 of guide + S16/S17/S18 from EMA guide)
STRATEGY_MIN_CONF: Dict[str, int] = {
    "S01": 55, "S02": 55, "S03": 55, "S04": 55, "S05": 50,
    "S06": 60, "S07": 60, "S08": 60, "S09": 55, "S10": 55,
    "S11": 55, "S12": 55, "S13": 60, "S14": 65, "S15": 60,
    # EMA-9 strategies: require all 3 confirmation filters — higher thresholds
    "S16": 60,   # EMA-9 Bounce: slope + VWAP + volume all needed
    "S17": 65,   # EMA-9 Rejection Short: RSI < 50 + VWAP + volume + slope
    "S18": 65,   # EMA-9 Breakout Long: 4 required conditions (slope UP + VWAP mandatory)
}

CATEGORY_EMOJI = {
    "VWAP": "💧", "REVERSAL": "🔄", "TREND": "📈", "SQUEEZE": "💥",
    "EMA": "📉", "OPENING_DRIVE": "🚀",
    "SR_BOUNCE": "🎯", "SR_BREAKOUT": "⚡", "PIVOT": "🔵",
    "ROUND_NUMBER": "🔢", "PDH_PDL": "📌",
}

# Opening Drive: 8:30–10:30 ET (pre-market momentum + first trading hour)
OPENING_DRIVE_WINDOW   = (dtime(8, 30), dtime(10, 30))
# Hourly summary: every top-of-hour 9:30–15:30 ET
HOURLY_ALERT_SESSION   = (dtime(9, 30), dtime(15, 30))


class RealtimeCandleEngine:
    def __init__(self) -> None:
        self.active_candles:         Dict[str, Dict]  = {}
        self.candle_start_times:     Dict[str, float] = {}
        self.closed_candles_history: Dict[str, deque] = {}
        self.last_telegram_alert_time: Dict[str, float] = {}

        # Strategy scanner — 15 strategies; we apply per-strategy thresholds ourselves
        self._scanner = StrategyScanner(min_confidence=25)

        # Upgrade engines (stateless, safe to share)
        self._indicator  = IndicatorEngine()
        self._candles    = CandlePatternEngine()
        self._range      = PriceRangeEngine()
        self._mtf        = MultiTimeframeEngine()
        self._volume     = VolumeEngine()
        self._volatility = VolatilityEngine()
        self._prob       = ProbabilityEngine()
        self._risk       = RiskEngine()
        self._insight    = InsightGenerator()
        self._formatter  = AlertFormatter()
        self._od_module      = OpeningDriveModule(min_confidence=55, min_rvol=1.5, min_gap_pct=0.5)
        self._sr_module      = SRStrategyModule(min_confidence=55)
        self.prior_closes:   Dict[str, float] = {}   # symbol → prior session close
        self.session_dates:  Dict[str, object] = {}  # symbol → last seen date
        self.prior_day_data: Dict[str, dict] = {}    # symbol → {high, low, close} of prior session

        # Optional OpenAI
        self._openai = None
        openai_key   = settings.OPENAI_API_KEY or ""
        if openai_key and "your_openai_api_key" not in openai_key:
            try:
                from openai import AsyncOpenAI
                self._openai = AsyncOpenAI(api_key=openai_key)
                logger.info("OpenAI detected — enhanced AI insights active.")
            except ImportError:
                logger.warning("openai package missing; using InsightGenerator.")
        else:
            logger.info("No OpenAI key — using InsightGenerator for AI narratives.")

    # ──────────────────────────────────────────────────────────────
    #  Main loop
    # ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        logger.info("Initializing Multi-Symbol Candle Engine (18 strategies)...")
        client = redis_client.client
        if not client:
            logger.error("Redis client not initialized. Cannot run Candle Engine.")
            return

        hourly_task = asyncio.create_task(self._hourly_alert_loop())
        async with client.pubsub() as pubsub:
            await pubsub.subscribe(REDIS_TICK_CHANNEL)
            logger.info(f"Subscribed to tick channel: {REDIS_TICK_CHANNEL}")
            try:
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    await self.process_tick(json.loads(message["data"]))
            except asyncio.CancelledError:
                logger.info("Candle Engine cancelled — shutting down.")
            except Exception as e:
                logger.error(f"Candle Engine error: {e}", exc_info=True)
            finally:
                hourly_task.cancel()

    # ──────────────────────────────────────────────────────────────
    #  Tick → Candle accumulation
    # ──────────────────────────────────────────────────────────────

    async def process_tick(self, tick: Dict) -> None:
        symbol    = tick["symbol"]
        price     = float(tick["price"])
        volume    = int(tick["volume"])
        timestamp = float(tick["timestamp"])

        if symbol not in self.active_candles:
            self.candle_start_times[symbol] = timestamp
            self.active_candles[symbol] = {
                "symbol": symbol, "open": price, "high": price,
                "low": price, "close": price, "volume": volume,
                "timestamp": timestamp,
            }
            return

        active = self.active_candles[symbol]
        start  = self.candle_start_times[symbol]

        if timestamp - start < CANDLE_WINDOW_SECS:
            active["high"]   = max(active["high"], price)
            active["low"]    = min(active["low"],  price)
            active["close"]  = price
            active["volume"] += volume
        else:
            await self.publish_candle(active)

            if symbol not in self.closed_candles_history:
                self.closed_candles_history[symbol] = deque(maxlen=CANDLE_HISTORY_SIZE)
            self.closed_candles_history[symbol].append(active)

            # Track date change to capture prior close
            from datetime import date as _date
            bar_date = _date.fromtimestamp(timestamp)
            if symbol in self.session_dates and self.session_dates[symbol] != bar_date:
                # New trading day — store yesterday's last close
                self.prior_closes[symbol] = float(active["close"])
                # Build prior day summary for S/R pivot calculations
                history = self.closed_candles_history.get(symbol)
                if history:
                    prev_date = self.session_dates[symbol]
                    prev_bars = [c for c in history
                                 if _date.fromtimestamp(c["timestamp"]) == prev_date]
                    if prev_bars:
                        self.prior_day_data[symbol] = {
                            "High":  max(c["high"]  for c in prev_bars),
                            "Low":   min(c["low"]   for c in prev_bars),
                            "Close": prev_bars[-1]["close"],
                        }
            self.session_dates[symbol] = bar_date

            await self.evaluate_strategy_checklist(symbol)

            self.candle_start_times[symbol] = timestamp
            self.active_candles[symbol] = {
                "symbol": symbol, "open": price, "high": price,
                "low": price, "close": price, "volume": volume,
                "timestamp": timestamp,
            }

    async def publish_candle(self, candle: Dict) -> None:
        if redis_client.client:
            await redis_client.client.publish(REDIS_CANDLE_CHANNEL, json.dumps(candle))

    # ──────────────────────────────────────────────────────────────
    #  DataFrame builder
    # ──────────────────────────────────────────────────────────────

    def _build_dataframe(self, symbol: str) -> Optional[pd.DataFrame]:
        history = self.closed_candles_history.get(symbol)
        if not history or len(history) < 30:
            return None
        df = pd.DataFrame(list(history))
        df.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        }, inplace=True)
        idx = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df.set_index(idx, inplace=True)
        df.drop(columns=["symbol", "timestamp"], errors="ignore", inplace=True)
        # Price sanity: reject DataFrames with stuck feeds or out-of-range prices
        if not validate_dataframe(symbol, df):
            return None
        return df

    # ──────────────────────────────────────────────────────────────
    #  Strategy evaluation — 15 strategies + upgrade enrichment
    # ──────────────────────────────────────────────────────────────

    async def evaluate_strategy_checklist(self, symbol: str) -> None:
        df_raw = self._build_dataframe(symbol)
        if df_raw is None:
            return

        # ── Session gate (core strategies only) ──────────────────
        now_et  = datetime.now(ET)
        allowed = _session_allowed(now_et)
        # NOTE: do NOT return here — specialty scans (OD/SR) run independently
        # of the session gate and have their own time windows.

        # ── Run all 18 strategies across 1m / 5m / 10m / 15m ────
        # Skip core scan outside trading hours to avoid false signals.
        try:
            all_tf = self._scanner.scan_all_timeframes(symbol, df_raw) if allowed else {}
        except Exception as e:
            logger.warning(f"StrategyScanner failed for {symbol}: {e}")
            all_tf = {}   # let specialty scans continue even if core fails

        # ── Apply session + per-strategy confidence filters ───────
        filtered: Dict[str, StrategySignal] = {}   # strategy_id → best signal
        for tf_sigs in all_tf.values():
            for sig in tf_sigs:
                if sig.strategy_id not in allowed:
                    continue
                min_conf = STRATEGY_MIN_CONF.get(sig.strategy_id, 55)
                if sig.confidence < min_conf:
                    continue
                # Keep highest-confidence instance per strategy
                if (sig.strategy_id not in filtered or
                        sig.confidence > filtered[sig.strategy_id].confidence):
                    filtered[sig.strategy_id] = sig

        # ── Specialty scans — run independently of core strategy gate ─
        od_signals: List[OpeningDriveSignal] = []
        od_start, od_end = OPENING_DRIVE_WINDOW
        if od_start <= now_et.time() < od_end:
            try:
                prior_close = self.prior_closes.get(symbol) or 0.0
                od_signals  = self._od_module.scan(symbol, df_raw, prior_close)
                if od_signals:
                    logger.info(
                        f"{symbol}: {len(od_signals)} Opening Drive signal(s) — "
                        f"{[s.strategy_id for s in od_signals]}"
                    )
            except Exception as e:
                logger.warning(f"Opening Drive scan failed for {symbol}: {e}")

        sr_signals: List[SRStrategySignal] = []
        try:
            pdd      = self.prior_day_data.get(symbol)
            df_prior = pd.DataFrame([pdd]) if pdd else pd.DataFrame()
            sr_signals = self._sr_module.scan(symbol, df_raw, df_prior)
            if sr_signals:
                logger.info(
                    f"{symbol}: {len(sr_signals)} S/R signal(s) — "
                    f"{[s.strategy_id for s in sr_signals]}"
                )
        except Exception as e:
            logger.warning(f"S/R scan failed for {symbol}: {e}")

        # ── Gate: need at least one signal from any module ────────
        if not (filtered or od_signals or sr_signals):
            return

        # ── Build primary signal context ──────────────────────────
        if filtered:
            signals = sorted(filtered.values(), key=lambda s: s.confidence, reverse=True)
            bull = sum(1 for s in signals if s.direction == "bullish")
            bear = sum(1 for s in signals if s.direction == "bearish")
            direction = "bullish" if bull >= bear else "bearish"
            signals = sorted(signals,
                             key=lambda s: (s.direction != direction, -s.confidence))
            top    = signals[0]
            action = "BUY" if direction == "bullish" else "SELL"
        else:
            # Specialty-only alert: build from best available specialty signal
            signals   = []
            best_spec = (od_signals + sr_signals)[0]
            direction = best_spec.direction
            action    = "BUY" if direction == "bullish" else "SELL"
            bull      = 1 if direction == "bullish" else 0
            bear      = 1 if direction == "bearish" else 0
            top       = self._make_top_from_specialty(best_spec)

        # ── Price sanity guard — never alert with wrong price ─────
        if not is_price_sane(symbol, top.price):
            logger.warning(
                f"{symbol}: alert suppressed — price ${top.price:.2f} failed sanity check. "
                "Register a range in price_fix.PRICE_RANGES or check your data feed."
            )
            return

        logger.info(
            f"{symbol}: {len(signals)} strategy signal(s) fired "
            f"({bull} bull / {bear} bear) → consensus {direction.upper()} "
            f"at {now_et.strftime('%H:%M ET')}"
        )

        # ── Upgrade pipeline ─────────────────────────────────────
        try:
            df = self._indicator.run(df_raw)

            candle_data  = self._candles.run(df)
            mtf_data     = self._mtf.run(df_raw)
            range_data   = self._range.run(df, direction, candle_data)
            vol_data     = self._volatility.run(df)
            volume_data  = self._volume.run(df)
            prob_data    = self._prob.run(mtf_data, df, vol_data, volume_data, candle_data)
            mtf_targets  = self._compute_mtf_targets(df_raw)

            # Use upgrade engine confidence as secondary filter if configured
            # (bypass gate when only specialty signals fired — they have their own confidence)
            if signals and prob_data["confidence"] < settings.MIN_CONFIDENCE and len(signals) < 2:
                logger.info(
                    f"{symbol}: upgrade engine confidence {prob_data['confidence']}/100 "
                    f"< {settings.MIN_CONFIDENCE} and only 1 strategy fired — suppressed."
                )
                return

            # Enforce 0.3% minimum ATR so trade levels are meaningful even when
            # the engine just started and only has seconds of candle history.
            _atr_floor = top.price * 0.003
            if vol_data["atr"] < _atr_floor:
                vol_data = {**vol_data, "atr": round(_atr_floor, 4)}

            risk_data    = self._risk.run(top.price, direction, vol_data["atr"], range_data)
            base_insight = self._insight.generate(
                symbol, prob_data, mtf_data, vol_data, volume_data, candle_data, range_data
            )
            insight_txt = await self._enhance_insight(
                symbol, action, top.price, prob_data, top, base_insight
            )

        except Exception as e:
            logger.error(f"Upgrade pipeline failed for {symbol}: {e}", exc_info=True)
            atr = top.data.get("atr", top.price * 0.005) if top.data else top.price * 0.005
            risk_data   = self._basic_risk(top.price, direction, float(atr or top.price * 0.005))
            prob_data   = self._empty_prob(direction)
            mtf_data    = self._empty_mtf(direction)
            vol_data    = {"regime": "normal", "atr": 0, "atr_avg": 0,
                           "expanding": False, "vwap_dist_pct": 0, "rolling_vol": 0}
            volume_data = {"rel_vol": 1.0, "spike": False, "exhaustion": False,
                           "confirmation": False, "vol_trend": "normal", "avg_volume": 0}
            candle_data = {"patterns": [], "bias": "neutral",
                           "pattern_target": None, "candle_expansion": 1.0}
            range_data  = {"expected_low": round(top.price - float(atr or top.price*0.005)*2.5, 2),
                           "expected_high": round(top.price + float(atr or top.price*0.005)*2.5, 2),
                           "support": [], "resistance": [], "atr": 0, "sr_target": None}
            insight_txt = f"Strategy cluster for {symbol} — {len(signals)} signal(s) fired."
            mtf_targets  = self._compute_mtf_targets(df_raw)

        # ── Build alert payloads ──────────────────────────────────
        strategy_summary = ", ".join(
            f"[{s.strategy_id}] {s.strategy_name} {s.confidence}/100"
            for s in signals[:4]
        )
        conditions_str = "\n".join(f"✅ {c}" for c in top.conditions_met)

        legacy_str = (
            f"{symbol} {action} ALERT | Strategies: {len(signals)} "
            f"({bull}B/{bear}S) | Top: [{top.strategy_id}] {top.strategy_name} "
            f"| Price: ${top.price:.2f} | Stop: ${risk_data['stop']:.2f} "
            f"| T1: ${risk_data['t1']:.2f} | T2: ${risk_data['t2']:.2f} "
            f"| R:R 1:{risk_data['rr']} | Conf: {top.confidence}/100 "
            f"| {now_et.strftime('%H:%M ET')}"
        )
        alert_payload = {
            "symbol":             symbol,
            "action":             action,
            "price":              top.price,
            "direction":          direction,
            # When no core strategies fire, list specialty signals so the
            # "Fired strategies" section in Telegram is never blank.
            "strategies_fired":   (
                [s.strategy_id   for s in signals]
                if signals else
                [s.strategy_id   for s in (od_signals + sr_signals)]
            ),
            "strategy_names":     (
                [s.strategy_name for s in signals]
                if signals else
                [s.strategy_name for s in (od_signals + sr_signals)]
            ),
            "top_strategy":       top.strategy_id,
            "top_strategy_name":  top.strategy_name,
            "top_category":       top.category,
            "consensus_bull":     bull,
            "consensus_bear":     bear,
            "conditions_met":     top.conditions_met,
            "conditions_missed":  top.conditions_missed,
            "stop":               risk_data["stop"],
            "t1":                 risk_data["t1"],
            "t2":                 risk_data["t2"],
            "rr":                 risk_data["rr"],
            "trade_type":         self._classify_trade_type(
                                      now_et, vol_data["atr"] / top.price * 100,
                                      risk_data["rr"], top.category),
            "confidence":         top.confidence,
            "confidence_label":   prob_data.get("confidence_label", "N/A"),
            "upgrade_confidence": prob_data.get("confidence", 0),
            "patterns":           candle_data["patterns"],
            "expected_range":     [range_data["expected_low"], range_data["expected_high"]],
            "vol_regime":         vol_data["regime"],
            "vol_rel":            volume_data["rel_vol"],
            "session_time":       now_et.strftime("%H:%M ET"),
            "message":            legacy_str,
            "ai_insight":         insight_txt,
            "mtf_targets":        mtf_targets,
            "od_signals":         [self._od_signal_to_dict(s) for s in od_signals],
            "sr_signals":         [self._sr_signal_to_dict(s) for s in sr_signals],
            "timestamp":          time.time(),
        }

        logger.info(f"Alert — {legacy_str}")
        logger.debug(f"Conditions met: {top.conditions_met}")

        if redis_client.client:
            await redis_client.client.publish(
                REDIS_ALERT_CHANNEL, json.dumps(alert_payload)
            )

        def _on_done(task: asyncio.Task, label: str) -> None:
            if not task.cancelled() and task.exception():
                logger.error(f"{label} dispatch failed: {task.exception()}")

        tg_t = asyncio.create_task(self._dispatch_telegram(alert_payload))
        tg_t.add_done_callback(lambda t: _on_done(t, "Telegram"))

    # ──────────────────────────────────────────────────────────────
    #  Slack Block Kit builder — combines scanner + upgrade engine
    # ──────────────────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────────
    #  OpenAI insight enhancement
    # ──────────────────────────────────────────────────────────────

    async def _enhance_insight(
        self, symbol: str, action: str, price: float,
        prob: dict, top: Any, base: str
    ) -> str:
        if not self._openai:
            return base

        strat_ctx = f"[{top.strategy_id}] {top.strategy_name} ({top.score}/{top.max_score} conditions)"
        conditions = "; ".join(top.conditions_met[:3])
        prompt = (
            f"Institutional quant insight — one sentence, max 30 words:\n"
            f"{symbol} {action} triggered by {strat_ctx}\n"
            f"Key conditions: {conditions}\n"
            f"Base analysis: {base}"
        )
        try:
            resp    = await self._openai.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Professional quant trader writing concise signal insights."},
                    {"role": "user",   "content": prompt},
                ],
                max_tokens=60,
                temperature=0.65,
            )
            content = resp.choices[0].message.content
            return content.strip() if content else base
        except Exception as e:
            logger.warning(f"OpenAI insight failed for {symbol}: {e}")
            return base

    # ──────────────────────────────────────────────────────────────
    #  Telegram dispatch
    # ──────────────────────────────────────────────────────────────

    async def _dispatch_telegram(self, sig: Dict) -> None:
        token   = settings.TELEGRAM_BOT_TOKEN or ""
        chat_id = settings.TELEGRAM_CHAT_ID   or ""
        if not token or not chat_id or "your_telegram_bot_token" in token:
            return

        symbol = sig["symbol"]
        now    = time.time()
        last   = self.last_telegram_alert_time.get(symbol, 0.0)
        if now - last < settings.TELEGRAM_ALERT_COOLDOWN:
            logger.info(f"Telegram cooldown active for {symbol}.")
            return

        emoji      = "🟢" if sig["action"] == "BUY" else "🔴"
        trade_type = sig.get("trade_type", "Intraday")
        strategies = "\n".join(
            f"  • [{sid}] {name}"
            for sid, name in zip(sig.get("strategies_fired", []),
                                  sig.get("strategy_names", []))
        )
        conditions = "\n".join(f"  ✅ {c}" for c in sig.get("conditions_met", [])[:4])
        exp        = sig.get("expected_range", [])
        rng_str    = f"${exp[0]:.2f} → ${exp[1]:.2f}" if len(exp) == 2 else "N/A"
        patterns   = ", ".join(p.replace("_", " ") for p in sig.get("patterns", [])) or "None"

        # ── Multi-timeframe target table ──────────────────────────
        _TREND_ICON = {"bullish": "📈", "bearish": "📉", "neutral": "➡", "n/a": "❓"}
        mtf_lines = []
        for row in sig.get("mtf_targets", []):
            tf    = row.get("tf", "?")
            ticon = _TREND_ICON.get(row.get("trend", "n/a"), "❓")
            hh    = row.get("proj_hh")
            ll    = row.get("proj_ll")
            rsi   = row.get("rsi")
            if hh is None or ll is None:
                mtf_lines.append(f"<code>{tf:<3}</code> {ticon}  —  insufficient data")
            elif tf == "1d":
                mtf_lines.append(
                    f"<code>{tf:<3}</code> {ticon}  Hi ${row['swing_high']:.2f}  Lo ${row['swing_low']:.2f}"
                    f"  →  ↑${hh:.2f}  ↓${ll:.2f}"
                )
            else:
                rsi_str = f"  RSI:{rsi:.0f}" if rsi is not None else ""
                mtf_lines.append(
                    f"<code>{tf:<3}</code> {ticon}  ↑${hh:.2f}  ↓${ll:.2f}{rsi_str}"
                )
        mtf_section = "\n".join(mtf_lines) if mtf_lines else "  N/A"

        # ── Opening Drive alert section ───────────────────────────
        od_section = ""
        od_list    = sig.get("od_signals", [])
        if od_list:
            od_lines = []
            for od in od_list:
                star  = " ⭐ PREMIUM" if od.get("premium_setup") else ""
                gap   = od.get("gap_pct", 0)
                rvol  = od.get("rvol", 0)
                rq    = od.get("rvol_quality", "").upper()
                ph    = od.get("pm_high")
                pmh_s = f" | PM High ${ph:.2f}" if ph else ""
                od_lines.append(
                    f"🚀 <b>[{od['strategy_id']}] {od['strategy_name']}</b>{star}\n"
                    f"   Gap: {gap:+.1f}% | RVOL: {rvol:.1f}× ({rq}){pmh_s}\n"
                    f"   Entry: ${od['entry']:.2f} | Stop: ${od['stop']:.2f} | "
                    f"T1: ${od['t1']:.2f} | T2: ${od['t2']:.2f} | R:R 1:{od['rr']}\n"
                    f"   Conf: {od['confidence']}/100 | {od['score']}/{od['max_score']} conditions"
                )
            od_section = "\n\n📊 <b>Opening Drive Alerts:</b>\n" + "\n\n".join(od_lines)

        # ── S/R levels alert section ──────────────────────────────
        sr_section = ""
        sr_list = sig.get("sr_signals", [])
        main_dir = sig.get("direction", "bullish")
        conflict_srs = [sr for sr in sr_list if sr["direction"] != main_dir]
        if sr_list:
            sr_lines = []
            for sr in sr_list:
                dir_em  = "🟢" if sr["direction"] == "bullish" else "🔴"
                prem    = " ⭐ PREMIUM" if sr.get("premium_setup") else ""
                ltype   = sr.get("sr_level_type", "")
                ltouch  = sr.get("sr_level_touches", 0)
                lstr    = sr.get("sr_level_strength", 0)
                conflict_tag = " ⚠ CONFLICTING" if sr["direction"] != main_dir else ""
                sr_lines.append(
                    f"{dir_em} <b>[{sr['strategy_id']}] {sr['strategy_name']}</b>{prem}{conflict_tag}\n"
                    f"  Level: ${sr['sr_level_price']:.2f}  ({ltype}  ·  {ltouch} touches  ·  strength {lstr})\n"
                    f"  Entry: ${sr['entry']:.2f}  Stop: ${sr['stop']:.2f}\n"
                    f"  T1: ${sr['t1']:.2f}  T2: ${sr['t2']:.2f}  R:R 1:{sr['rr']}\n"
                    f"  Conf: {sr['confidence']}/100  ({sr['score']}/{sr['max_score']} conditions)"
                )
            conflict_note = (
                f"\n⚠ <i>{len(conflict_srs)} S/R signal(s) oppose this alert direction — trade with caution</i>"
                if conflict_srs else ""
            )
            sr_section = (
                "\n\n📍 <b>S/R Strategy Signals (S20–S27):</b>\n"
                "─────────────────────────────\n"
                + "\n\n".join(sr_lines)
                + conflict_note
            )

        text = (
            f"{emoji} <b>{symbol} {sig['action']} ALERT</b> — {sig['session_time']}\n"
            f"─────────────────────────────\n"
            f"<b>Top Strategy:</b> [{sig['top_strategy']}] {sig['top_strategy_name']}\n"
            f"<b>Consensus:</b> {sig['consensus_bull']} Bull / {sig['consensus_bear']} Bear\n"
            f"<b>Trade Type:</b> {trade_type}\n\n"
            f"<b>Fired strategies:</b>\n{strategies}\n\n"
            f"<b>Trade levels:</b>\n"
            f"Entry: ${sig['price']:.2f} | Stop: ${sig['stop']:.2f}\n"
            f"T1: ${sig['t1']:.2f} | T2: ${sig['t2']:.2f} | R:R 1:{sig['rr']}\n"
            f"Expected range: {rng_str}\n\n"
            f"<b>Conditions met:</b>\n{conditions}\n\n"
            f"Confidence: {sig['confidence']}/100 | Vol regime: {sig['vol_regime'].upper()}\n"
            f"Patterns: {patterns}\n\n"
            f"📊 <b>Multi-Timeframe Price Targets:</b>\n"
            f"<i>TF  | Trend | ↑ Higher High  ↓ Lower Low</i>\n"
            f"{mtf_section}"
            f"{od_section}"
            f"{sr_section}\n\n"
            f"🧠 <b>AI Insight:</b> <i>{sig['ai_insight']}</i>\n\n"
            f"⚠ <i>Educational only — not financial advice</i>"
        )
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    url,
                    json={"chat_id": chat_id, "text": text,
                          "parse_mode": "HTML", "disable_web_page_preview": True},
                    timeout=5.0,
                )
            self.last_telegram_alert_time[symbol] = now
            logger.info(f"Telegram alert dispatched for {symbol}.")
        except Exception as e:
            logger.error(f"Telegram dispatch error for {symbol}: {e}")

    # ──────────────────────────────────────────────────────────────
    #  Trade-type classifier
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _classify_trade_type(now_et: datetime, atr_pct: float,
                             rr: float, category: str) -> str:
        """Label the alert as 0DTE Scalp / Intraday / Weekly / Swing based on
        time of day, volatility, R:R, and strategy category."""
        t = now_et.time()
        # Late-day or tight R:R → 0DTE only, must close before market end
        if t >= dtime(14, 0) or rr < 1.5:
            return "0DTE Scalp (exit today)"
        # Strong trend strategy + good ATR + early session → swing candidate
        if category in ("TREND", "EMA") and rr >= 2.5 and atr_pct >= 0.4:
            return "Swing Trade (1–3 weeks)"
        # Good R:R + morning window → multi-day hold
        if rr >= 2.0 and t < dtime(11, 30):
            return "Weekly (2–5 days)"
        return "Intraday (same-day exit)"

    # ──────────────────────────────────────────────────────────────
    #  Hourly market-update loop
    # ──────────────────────────────────────────────────────────────

    async def _hourly_alert_loop(self) -> None:
        """Sleep until the next top-of-hour ET, then dispatch a brief
        intraday summary for every active symbol.  Repeats indefinitely."""
        while True:
            now_et = datetime.now(ET)
            nxt    = (now_et + timedelta(hours=1)).replace(
                         minute=0, second=2, microsecond=0)
            await asyncio.sleep(max(1, (nxt - datetime.now(ET)).total_seconds()))

            now_et = datetime.now(ET)
            h_start, h_end = HOURLY_ALERT_SESSION
            if not (h_start <= now_et.time() < h_end):
                continue

            lines: List[str] = []
            for sym in sorted(self.closed_candles_history):
                df = self._build_dataframe(sym)
                if df is None:
                    continue
                try:
                    price = float(df["Close"].iloc[-1])
                    ema9  = float(df["Close"].ewm(span=9, adjust=False).mean().iloc[-1])
                    atr   = float((df["High"] - df["Low"]).tail(14).mean())
                    atr   = max(atr, price * 0.003)

                    if price >= ema9:
                        direction, t_icon = "Bullish", "📈"
                        stop = round(price - atr,       2)
                        t1   = round(price + atr * 1.5, 2)
                        t2   = round(price + atr * 2.5, 2)
                    else:
                        direction, t_icon = "Bearish", "📉"
                        stop = round(price + atr,       2)
                        t1   = round(price - atr * 1.5, 2)
                        t2   = round(price - atr * 2.5, 2)

                    risk_r = round(abs(price - t1) / abs(price - stop), 1) if price != stop else 0
                    ttype  = self._classify_trade_type(now_et, atr / price * 100, risk_r, "INTRADAY")
                    lines.append(
                        f"<b>{sym}</b>  ${price:.2f}  {t_icon} {direction}\n"
                        f"  Type: <b>{ttype}</b>\n"
                        f"  Entry ${price:.2f} | Stop ${stop:.2f} | T1 ${t1:.2f} | T2 ${t2:.2f} | R:R 1:{risk_r}"
                    )
                except Exception:
                    continue

            if lines:
                await self._dispatch_hourly_telegram(now_et, lines)

    async def _dispatch_hourly_telegram(self, now_et: datetime,
                                        lines: List[str]) -> None:
        """Send the hourly summary message to Telegram."""
        token   = settings.TELEGRAM_BOT_TOKEN or ""
        chat_id = settings.TELEGRAM_CHAT_ID   or ""
        if not token or not chat_id or "your_telegram_bot_token" in token:
            return

        body = "\n\n".join(lines)
        text = (
            f"📊 <b>HOURLY UPDATE — {now_et.strftime('%H:%M ET')}</b>\n"
            f"─────────────────────────────\n"
            f"{body}\n\n"
            f"─────────────────────────────\n"
            f"⚠ <i>Intraday market update — not financial advice</i>"
        )
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    url,
                    json={"chat_id": chat_id, "text": text,
                          "parse_mode": "HTML", "disable_web_page_preview": True},
                    timeout=5.0,
                )
            logger.info(
                f"Hourly update dispatched — {len(lines)} symbol(s) at "
                f"{now_et.strftime('%H:%M ET')}"
            )
        except Exception as e:
            logger.error(f"Hourly Telegram dispatch error: {e}")

    # ──────────────────────────────────────────────────────────────
    #  Fallback helpers
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _make_top_from_specialty(sig) -> Any:
        """Wrap a specialty signal (OD/SR/VWAP/S28) as a StrategySignal-compatible namespace."""
        import types
        return types.SimpleNamespace(
            strategy_id      = getattr(sig, "strategy_id",  "S-SPEC"),
            strategy_name    = getattr(sig, "strategy_name", "Specialty"),
            category         = getattr(sig, "category",      "SPECIALTY"),
            price            = sig.price,
            conditions_met   = getattr(sig, "conditions_met",   []),
            conditions_missed= getattr(sig, "conditions_missed", []),
            score            = getattr(sig, "score",    0),
            max_score        = getattr(sig, "max_score", 8),
            confidence       = getattr(sig, "confidence", 0),
            direction        = getattr(sig, "direction", "bullish"),
            data             = {},
        )

    @staticmethod
    def _basic_risk(price: float, direction: str, atr: float) -> dict:
        atr = atr if atr > 0 else price * 0.005
        m   = 1 if direction == "bullish" else -1
        stop, t1, t2 = (round(price - m*atr, 2),
                        round(price + m*atr*1.5, 2),
                        round(price + m*atr*2.5, 2))
        risk = abs(price - stop)
        return {"entry": round(price, 2), "stop": stop, "t1": t1, "t2": t2,
                "rr": round(abs(price-t1)/risk, 2) if risk > 0 else 0,
                "risk_$": round(risk, 2)}

    @staticmethod
    def _empty_prob(direction: str) -> dict:
        return {"direction": direction, "confidence": 0, "confidence_label": "N/A",
                "continuation_probability": 0, "reversal_probability": 0,
                "scores": {"trend":0,"momentum":0,"volume":0,"volatility":0,"pattern":0}}

    @staticmethod
    def _empty_mtf(direction: str) -> dict:
        empty = {"trend":"unknown","rsi":None,"above_vwap":None,
                 "stoch_signal":"neutral","stoch_k":None}
        return {"timeframes": {tf: empty for tf in ["base","5m","15m","1h"]},
                "dominant": direction, "aligned_tfs": 0, "alignment_pct": 0}

    @staticmethod
    def _od_signal_to_dict(sig: OpeningDriveSignal) -> dict:
        return {
            "strategy_id":    sig.strategy_id,
            "strategy_name":  sig.strategy_name,
            "variant":        sig.variant,
            "direction":      sig.direction,
            "price":          sig.price,
            "entry":          sig.entry,
            "stop":           sig.stop,
            "t1":             sig.t1,
            "t2":             sig.t2,
            "rr":             sig.rr,
            "confidence":     sig.confidence,
            "conditions_met": sig.conditions_met,
            "score":          sig.score,
            "max_score":      sig.max_score,
            "rvol":           sig.rvol,
            "gap_pct":        sig.gap_pct,
            "premium_setup":  sig.premium_setup,
            "pm_high":        sig.pm_data.get("pm_high") if sig.pm_data else None,
            "pm_sr_levels":   sig.pm_data.get("pm_sr_levels", []) if sig.pm_data else [],
            "rvol_quality":   sig.pm_data.get("rvol_quality","") if sig.pm_data else "",
        }

    @staticmethod
    def _sr_signal_to_dict(sig: SRStrategySignal) -> dict:
        return {
            "strategy_id":        sig.strategy_id,
            "strategy_name":      sig.strategy_name,
            "category":           sig.category,
            "direction":          sig.direction,
            "price":              sig.price,
            "entry":              sig.entry,
            "stop":               sig.stop,
            "t1":                 sig.t1,
            "t2":                 sig.t2,
            "t3":                 sig.t3,
            "rr":                 sig.rr,
            "confidence":         sig.confidence,
            "conditions_met":     sig.conditions_met,
            "score":              sig.score,
            "max_score":          sig.max_score,
            "sr_level_price":     sig.sr_level_price,
            "sr_level_type":      sig.sr_level_type.replace("_", " ").title(),
            "sr_level_strength":  sig.sr_level_strength,
            "sr_level_touches":   sig.sr_level_touches,
            "premium_setup":      sig.premium_setup,
        }

    # ──────────────────────────────────────────────────────────────
    #  Multi-timeframe price projection (HH / LL targets per TF)
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_mtf_targets(df_base: pd.DataFrame) -> List[dict]:
        """
        Resample 5s base candles to 2m/5m/10m/15m/30m/1h/session-day
        and compute swing-based higher-high / lower-low projections per TF.
        Swing high/low = rolling 5-bar extremes; projection = swing ± 1.5×ATR.
        """
        TF_RULES = [
            ("2m",  "2min"),
            ("5m",  "5min"),
            ("10m", "10min"),
            ("15m", "15min"),
            ("30m", "30min"),
            ("1h",  "1h"),
        ]

        def _resample(df: pd.DataFrame, rule: str) -> Optional[pd.DataFrame]:
            try:
                agg = {k: v for k, v in {
                    "Open": "first", "High": "max",
                    "Low": "min", "Close": "last", "Volume": "sum",
                }.items() if k in df.columns}
                out = df.resample(rule).agg(agg).dropna()
                return out if len(out) >= 3 else None
            except Exception:
                return None

        def _tf_stats(df: pd.DataFrame) -> dict:
            n      = len(df)
            period = min(14, n - 1) if n > 2 else 1
            close  = df["Close"]
            high   = df["High"]
            low    = df["Low"]

            # Trend: EMA9 vs EMA21, confirmed by recent price momentum
            ema9  = float(close.ewm(span=min(9,  n), adjust=False).mean().iloc[-1])
            ema21 = float(close.ewm(span=min(21, n), adjust=False).mean().iloc[-1])
            cur   = float(close.iloc[-1])
            prev5 = float(close.iloc[max(-5, -n)])
            if ema9 > ema21 and cur >= prev5:
                trend = "bullish"
            elif ema9 < ema21 and cur <= prev5:
                trend = "bearish"
            else:
                trend = "neutral"

            # ATR (smoothed True Range)
            pc  = close.shift(1)
            tr  = pd.concat([(high - low), (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
            atr = float(tr.rolling(period).mean().iloc[-1])
            if math.isnan(atr) or atr <= 0:
                atr = float((high - low).mean())
            atr = max(atr, cur * 0.001)   # floor at 0.1% of price

            # Swing high / low over last 5 bars
            look       = min(5, n)
            swing_high = float(high.iloc[-look:].max())
            swing_low  = float(low.iloc[-look:].min())

            # RSI
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(period).mean()
            loss  = (-delta.clip(upper=0)).rolling(period).mean()
            rs    = gain / loss.replace(0, np.nan)
            rsi   = float((100 - 100 / (1 + rs)).iloc[-1])
            rsi   = rsi if not math.isnan(rsi) else 50.0

            return {
                "trend":      trend,
                "atr":        round(atr, 4),
                "swing_high": round(swing_high, 2),
                "swing_low":  round(swing_low,  2),
                "proj_hh":    round(swing_high + 1.5 * atr, 2),
                "proj_ll":    round(swing_low  - 1.5 * atr, 2),
                "rsi":        round(rsi, 1),
                "price":      round(cur, 2),
            }

        results: List[dict] = []
        ref_atr: float = 0.0   # store 5m ATR for day-projection fallback

        for tf_label, rule in TF_RULES:
            df_tf = _resample(df_base, rule)
            if df_tf is None:
                results.append({"tf": tf_label, "trend": "n/a",
                                 "swing_high": None, "swing_low": None,
                                 "proj_hh": None, "proj_ll": None,
                                 "rsi": None, "atr": None, "price": None})
                continue
            try:
                stats = _tf_stats(df_tf)
                if tf_label == "5m" and stats["atr"]:
                    ref_atr = stats["atr"]
                results.append({"tf": tf_label, **stats})
            except Exception:
                results.append({"tf": tf_label, "trend": "n/a",
                                 "swing_high": None, "swing_low": None,
                                 "proj_hh": None, "proj_ll": None,
                                 "rsi": None, "atr": None, "price": None})

        # Session-day row: today's intraday high/low + ATR-based extension
        try:
            day_hi  = float(df_base["High"].max())
            day_lo  = float(df_base["Low"].min())
            cur_day = float(df_base["Close"].iloc[-1])
            rng     = day_hi - day_lo
            pos     = (cur_day - day_lo) / rng if rng > 0 else 0.5
            day_trend = "bullish" if pos > 0.55 else ("bearish" if pos < 0.45 else "neutral")

            # Daily extension: use 5m ATR × Fibonacci 1.618 ratio
            ext = (ref_atr * 1.618) if ref_atr > 0 else rng * 0.5
            results.append({
                "tf":         "1d",
                "trend":      day_trend,
                "swing_high": round(day_hi, 2),
                "swing_low":  round(day_lo,  2),
                "proj_hh":    round(day_hi + ext, 2),
                "proj_ll":    round(day_lo - ext, 2),
                "rsi":        None,
                "atr":        None,
                "price":      round(cur_day, 2),
            })
        except Exception:
            pass

        return results


async def start_candle_engine() -> None:
    engine = RealtimeCandleEngine()
    await engine.run()
