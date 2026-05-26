import asyncio
import json
import logging
import math
import time
from collections import deque
from datetime import datetime, time as dtime
from typing import Dict, List, Optional, Set

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
from app.services.vwap_strategies import VWAPStrategyModule, VWAPSignal
from app.services.vwap_box_breakout import VWAPBoxBreakoutStrategy, VWAPBreakoutSignal

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
    "UPTREND": "📈", "DOWNTREND": "📉",
}

# Opening Drive: only 9:30–10:30 ET (first hour only)
OPENING_DRIVE_WINDOW = (dtime(9, 30), dtime(10, 30))


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
        self._od_module      = OpeningDriveModule(min_confidence=55, min_rvol=3.0, min_gap_pct=3.0)
        self._sr_module      = SRStrategyModule(min_confidence=55)
        self._vwap_module    = VWAPStrategyModule(min_confidence=55)
        self._s28_strategy   = VWAPBoxBreakoutStrategy()
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

        # ── Session gate ─────────────────────────────────────────
        now_et  = datetime.now(ET)
        allowed = _session_allowed(now_et)
        if not allowed:
            logger.debug(f"{symbol}: outside trading session at {now_et.strftime('%H:%M ET')} — skipped.")
            return

        # ── Run all 18 strategies across 1m / 5m / 10m / 15m ────
        try:
            all_tf = self._scanner.scan_all_timeframes(symbol, df_raw)
        except Exception as e:
            logger.warning(f"StrategyScanner failed for {symbol}: {e}")
            return

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

        if not filtered:
            return

        signals = sorted(filtered.values(), key=lambda s: s.confidence, reverse=True)

        # ── Consensus direction ───────────────────────────────────
        bull = sum(1 for s in signals if s.direction == "bullish")
        bear = sum(1 for s in signals if s.direction == "bearish")
        direction = "bullish" if bull >= bear else "bearish"

        # Re-rank: direction-aligned signals first, then by confidence
        signals = sorted(signals,
                         key=lambda s: (s.direction != direction, -s.confidence))
        top = signals[0]
        action = "BUY" if direction == "bullish" else "SELL"

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

            # Opening Drive scan (9:30–10:30 ET only)
            od_signals: List[OpeningDriveSignal] = []
            od_start, od_end = OPENING_DRIVE_WINDOW
            if od_start <= now_et.time() < od_end:
                try:
                    prior_close = self.prior_closes.get(symbol)
                    od_signals  = self._od_module.scan(symbol, df_raw, prior_close)
                except Exception as e:
                    logger.warning(f"Opening Drive scan failed for {symbol}: {e}")

            # ── S/R strategies scan (S20–S27) ────────────────────────
            sr_signals: List[SRStrategySignal] = []
            try:
                # Build prior-day DataFrame for PDH/PDL and pivot levels
                pdd = self.prior_day_data.get(symbol)
                df_prior = pd.DataFrame([pdd]) if pdd else None
                sr_signals = self._sr_module.scan(symbol, df_raw, df_prior)
                if sr_signals:
                    logger.info(
                        f"{symbol}: {len(sr_signals)} S/R signal(s) fired — "
                        f"{[s.strategy_id for s in sr_signals]}"
                    )
            except Exception as e:
                logger.warning(f"S/R scan failed for {symbol}: {e}")

            # ── VWAP strategies scan (V-R1 through V-D3) ─────────────
            vwap_signals: List[VWAPSignal] = []
            try:
                vwap_signals = self._vwap_module.scan(symbol, df_raw)
                if vwap_signals:
                    logger.info(
                        f"{symbol}: {len(vwap_signals)} VWAP signal(s) fired — "
                        f"{[s.strategy_id for s in vwap_signals]}"
                    )
            except Exception as e:
                logger.warning(f"VWAP strategies scan failed for {symbol}: {e}")

            # ── S28 VWAP Box Breakout scan ────────────────────────────
            s28_signals: List[VWAPBreakoutSignal] = []
            try:
                s28_sig = self._s28_strategy.check(symbol, df_raw)
                if s28_sig and s28_sig.confidence >= 65:
                    s28_signals = [s28_sig]
                    logger.info(
                        f"{symbol}: S28 VWAP Box Breakout fired — "
                        f"conf:{s28_sig.confidence} quality:{s28_sig.quality}"
                    )
            except Exception as e:
                logger.warning(f"S28 scan failed for {symbol}: {e}")

            # Use upgrade engine confidence as secondary filter if configured
            if prob_data["confidence"] < settings.MIN_CONFIDENCE and len(signals) < 2:
                logger.info(
                    f"{symbol}: upgrade engine confidence {prob_data['confidence']}/100 "
                    f"< {settings.MIN_CONFIDENCE} and only 1 strategy fired — suppressed."
                )
                return

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
            od_signals: List[OpeningDriveSignal] = []
            sr_signals: List[SRStrategySignal] = []
            vwap_signals: List[VWAPSignal] = []
            s28_signals: List[VWAPBreakoutSignal] = []

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

        slack_payload = self._build_slack_payload(
            symbol, direction, signals, top, risk_data,
            prob_data, range_data, vol_data, volume_data,
            candle_data, insight_txt, bull, bear, mtf_targets,
        )

        alert_payload = {
            "symbol":             symbol,
            "action":             action,
            "price":              top.price,
            "direction":          direction,
            "strategies_fired":   [s.strategy_id for s in signals],
            "strategy_names":     [s.strategy_name for s in signals],
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
            "vwap_signals":       [self._vwap_signal_to_dict(s) for s in vwap_signals],
            "s28_signals":        [self._s28_signal_to_dict(s) for s in s28_signals],
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

        slack_t = asyncio.create_task(self._dispatch_slack(slack_payload))
        slack_t.add_done_callback(lambda t: _on_done(t, "Slack"))

        tg_t = asyncio.create_task(self._dispatch_telegram(alert_payload))
        tg_t.add_done_callback(lambda t: _on_done(t, "Telegram"))

    # ──────────────────────────────────────────────────────────────
    #  Slack Block Kit builder — combines scanner + upgrade engine
    # ──────────────────────────────────────────────────────────────

    def _build_slack_payload(
        self, symbol: str, direction: str,
        signals: List[StrategySignal], top: StrategySignal,
        risk: dict, prob: dict, rng: dict,
        vol: dict, volume: dict, candles: dict,
        insight: str, bull: int, bear: int,
        mtf_targets: Optional[List[dict]] = None,
    ) -> dict:
        emoji  = "🟢" if direction == "bullish" else "🔴"
        color  = "#1D9E75" if direction == "bullish" else "#a32d2d"
        cat_em = CATEGORY_EMOJI.get(top.category, "📊")

        strat_list = "\n".join(
            f"{CATEGORY_EMOJI.get(s.category,'📊')} *[{s.strategy_id}] {s.strategy_name}* "
            f"— {s.confidence}/100  ({s.score}/{s.max_score} conditions)"
            for s in signals[:5]
        )
        cond_list = "\n".join(f"✅ {c}" for c in top.conditions_met)
        missed    = "\n".join(f"○ {c}" for c in top.conditions_missed[:2]) if top.conditions_missed else ""

        patterns_str = (", ".join(p.replace("_", " ") for p in candles["patterns"][:3])
                        if candles["patterns"] else "None")
        score_bar = "█" * (top.confidence // 10) + "░" * (10 - top.confidence // 10)
        exp_range = f"${rng['expected_low']} → ${rng['expected_high']}"

        blocks = [
            {"type": "header", "text": {"type": "plain_text",
             "text": f"{emoji} {symbol} {direction.upper()} — {top.strategy_name} "
                     f"| {len(signals)} Strategy Consensus"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Entry*\n${risk['entry']}"},
                {"type": "mrkdwn", "text": f"*Stop*\n${risk['stop']}"},
                {"type": "mrkdwn", "text": f"*Target 1*\n${risk['t1']} (50%)"},
                {"type": "mrkdwn", "text": f"*Target 2*\n${risk['t2']} (100%)"},
                {"type": "mrkdwn", "text": f"*R:R*\n1:{risk['rr']}"},
                {"type": "mrkdwn", "text": f"*Consensus*\n{bull} Bull / {bear} Bear"},
            ]},
            {"type": "divider"},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Expected Range*\n{exp_range}"},
                {"type": "mrkdwn", "text": f"*Upgrade Confidence*\n{prob.get('confidence',0)}/100"},
                {"type": "mrkdwn", "text": f"*Volume*\n{volume['rel_vol']}× avg {'🔥' if volume['spike'] else ''}"},
                {"type": "mrkdwn", "text": f"*Volatility*\n{vol['regime'].upper()} (ATR {'↑' if vol['expanding'] else '→'})"},
                {"type": "mrkdwn", "text": f"*Patterns*\n{patterns_str}"},
                {"type": "mrkdwn", "text": f"*Session*\n{datetime.now(ET).strftime('%H:%M ET')}"},
            ]},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"*{cat_em} Fired Strategies ({len(signals)})* — "
                     f"Confidence `{score_bar}` {top.confidence}/100\n{strat_list}"}},
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"*Conditions met ({top.score}/{top.max_score}):*\n{cond_list}"
                     + (f"\n*Not met:*\n{missed}" if missed else "")}},
            {"type": "section", "text": {"type": "mrkdwn",
             "text": f"*🧠 AI Insight*\n_{insight}_"}},
        ]

        # MTF price target table
        if mtf_targets:
            _TI = {"bullish": "📈", "bearish": "📉", "neutral": "➡", "n/a": "❓"}
            rows = []
            for r in mtf_targets:
                ti = _TI.get(r.get("trend", "n/a"), "❓")
                hh = r.get("proj_hh"); ll = r.get("proj_ll")
                tf = r.get("tf", "?")
                if hh and ll:
                    rsi_s = f" RSI:{r['rsi']:.0f}" if r.get("rsi") else ""
                    rows.append(f"`{tf:<3}` {ti}  ↑${hh}  ↓${ll}{rsi_s}")
            if rows:
                blocks.append({"type": "section", "text": {"type": "mrkdwn",
                    "text": "*📊 Multi-Timeframe Targets* (↑ Higher High  ↓ Lower Low)\n"
                            + "\n".join(rows)}})

        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
             "text": "⚠ Educational only — not financial advice"}]})
        return {
            "text": f"{emoji} {symbol} {direction.upper()} @ ${top.price} "
                    f"({len(signals)} strategies aligned)",
            "attachments": [{"color": color, "blocks": blocks}],
        }

    # ──────────────────────────────────────────────────────────────
    #  OpenAI insight enhancement
    # ──────────────────────────────────────────────────────────────

    async def _enhance_insight(
        self, symbol: str, action: str, price: float,
        prob: dict, top: StrategySignal, base: str
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
    #  Slack dispatch
    # ──────────────────────────────────────────────────────────────

    async def _dispatch_slack(self, payload: dict) -> None:
        webhook = settings.SLACK_WEBHOOK_URL or ""
        if not webhook:
            return
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(webhook, json=payload, timeout=5.0)
                if resp.status_code == 200:
                    logger.info("Slack alert dispatched.")
                else:
                    logger.warning(f"Slack returned HTTP {resp.status_code}.")
        except Exception as e:
            logger.error(f"Slack dispatch error: {e}")

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

        emoji     = "🟢" if sig["action"] == "BUY" else "🔴"
        strategies = "\n".join(
            f"  • [{sid}] {name}"
            for sid, name in zip(sig.get("strategies_fired", []),
                                  sig.get("strategy_names", []))
        )
        conditions = "\n".join(f"  ✅ {c}" for c in sig.get("conditions_met", [])[:4])
        exp        = sig.get("expected_range", [])
        rng_str    = f"${exp[0]} → ${exp[1]}" if len(exp) == 2 else "N/A"
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
                mtf_lines.append(f"`{tf:<3}` {ticon}  —  insufficient data")
            elif tf == "1d":
                mtf_lines.append(
                    f"`{tf:<3}` {ticon}  Hi ${row['swing_high']}  Lo ${row['swing_low']}"
                    f"  →  ↑${hh}  ↓${ll}"
                )
            else:
                rsi_str = f"  RSI:{rsi:.0f}" if rsi is not None else ""
                mtf_lines.append(
                    f"`{tf:<3}` {ticon}  ↑${hh}  ↓${ll}{rsi_str}"
                )
        mtf_section = "\n".join(mtf_lines) if mtf_lines else "  N/A"

        # Opening Drive alert section
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
                pmh_s = f" | PM High ${ph}" if ph else ""
                od_lines.append(
                    f"🚀 *[{od['strategy_id']}] {od['strategy_name']}*{star}\n"
                    f"   Gap: {gap:+.1f}% | RVOL: {rvol:.1f}× ({rq}){pmh_s}\n"
                    f"   Entry: ${od['entry']:.2f} | Stop: ${od['stop']:.2f} | "
                    f"T1: ${od['t1']:.2f} | T2: ${od['t2']:.2f} | R:R 1:{od['rr']}\n"
                    f"   Conf: {od['confidence']}/100 | {od['score']}/{od['max_score']} conditions"
                )
            od_section = "\n\n📊 *Opening Drive Alerts:*\n" + "\n\n".join(od_lines)

        # ── S/R levels alert section ──────────────────────────────
        sr_section = ""
        sr_list = sig.get("sr_signals", [])
        if sr_list:
            sr_lines = []
            for sr in sr_list:
                dir_em = "🟢" if sr["direction"] == "bullish" else "🔴"
                prem   = " ⭐ PREMIUM" if sr.get("premium_setup") else ""
                ltype  = sr.get("sr_level_type", "")
                ltouch = sr.get("sr_level_touches", 0)
                lstr   = sr.get("sr_level_strength", 0)
                sr_lines.append(
                    f"{dir_em} *[{sr['strategy_id']}] {sr['strategy_name']}*{prem}\n"
                    f"  Level: ${sr['sr_level_price']}  ({ltype}  ·  {ltouch} touches  ·  strength {lstr})\n"
                    f"  Entry: ${sr['entry']}  Stop: ${sr['stop']}\n"
                    f"  T1: ${sr['t1']}  T2: ${sr['t2']}  R:R 1:{sr['rr']}\n"
                    f"  Conf: {sr['confidence']}/100  ({sr['score']}/{sr['max_score']} conditions)"
                )
            sr_section = (
                "\n\n📍 *S/R Strategy Signals (S20–S27):*\n"
                "─────────────────────────────\n"
                + "\n\n".join(sr_lines)
            )

        # ── VWAP strategies section ───────────────────────────────
        _CAT_ICON = {"REVERSAL": "🔄", "UPTREND": "📈", "DOWNTREND": "📉"}
        vwap_section = ""
        vwap_list = sig.get("vwap_signals", [])
        if vwap_list:
            vwap_lines = []
            for vs in vwap_list[:3]:
                dir_em   = "🟢" if vs["direction"] == "bullish" else "🔴"
                prem_v   = " ⭐ PREMIUM" if vs.get("premium_setup") else ""
                cat_icon = _CAT_ICON.get(vs.get("category", ""), "📊")
                bias_str = vs.get("session_bias", "").upper()
                vwap_lines.append(
                    f"{dir_em} {cat_icon} *[{vs['strategy_id']}] {vs['strategy_name']}*{prem_v}\n"
                    f"  VWAP: ${vs['vwap']} | Bias: {bias_str} | {vs['vwap_touches_today']} touches\n"
                    f"  Entry: ${vs['entry']} | Stop: ${vs['stop']}\n"
                    f"  T1: ${vs['t1']} | T2: ${vs['t2']} | R:R 1:{vs['rr']}\n"
                    f"  Conf: {vs['confidence']}/100  ({vs['score']}/{vs['max_score']} conditions)"
                )
            vwap_section = (
                "\n\n💧 *VWAP Strategy Signals (V-R1–V-D3):*\n"
                "─────────────────────────────\n"
                + "\n\n".join(vwap_lines)
            )

        # ── S28 VWAP Box Breakout section ─────────────────────────
        s28_section = ""
        s28_list = sig.get("s28_signals", [])
        if s28_list:
            s28     = s28_list[0]
            dir_em  = "🟢" if s28["direction"] == "bullish" else "🔴"
            prem_28 = "\n  ⭐ PREMIUM SETUP — dual breakout confirmed!" if s28.get("premium_setup") else ""
            fr_28   = "\n  🔁 FIRST VWAP RECLAIM of session" if s28.get("first_reclaim") else ""
            s28_section = (
                f"\n\n📦 *S28 VWAP Box Breakout:*{prem_28}{fr_28}\n"
                f"─────────────────────────────\n"
                f"{dir_em} *{s28['direction'].upper()}* | Conf: {s28['confidence']}/100 ({s28['quality']})\n"
                f"  Box: ${s28['box_low']} – ${s28['box_high']} ({s28['box_bars']} bars)\n"
                f"  VWAP: ${s28['vwap']} | EMA9: ${s28['ema9']} | Vol: {s28['rel_vol']:.1f}× avg\n"
                f"  Entry: ${s28['entry']} | Stop: ${s28['stop']}\n"
                f"  T1: ${s28['t1']} | T2: ${s28['t2']} | R:R 1:{s28['rr']}\n"
                f"  Conditions: {s28['score']}/{s28['max_score']}"
            )

        text = (
            f"{emoji} *{symbol} {sig['action']} ALERT* — {sig['session_time']}\n"
            f"─────────────────────────────\n"
            f"*Top Strategy:* [{sig['top_strategy']}] {sig['top_strategy_name']}\n"
            f"*Consensus:* {sig['consensus_bull']} Bull / {sig['consensus_bear']} Bear\n\n"
            f"*Fired strategies:*\n{strategies}\n\n"
            f"*Trade levels:*\n"
            f"Entry: ${sig['price']:.2f} | Stop: ${sig['stop']:.2f}\n"
            f"T1: ${sig['t1']:.2f} | T2: ${sig['t2']:.2f} | R:R 1:{sig['rr']}\n"
            f"Expected range: {rng_str}\n\n"
            f"*Conditions met:*\n{conditions}\n\n"
            f"Confidence: {sig['confidence']}/100 | Vol regime: {sig['vol_regime'].upper()}\n"
            f"Patterns: {patterns}\n\n"
            f"📊 *Multi-Timeframe Price Targets:*\n"
            f"_TF  | Trend | ↑ Higher High  ↓ Lower Low_\n"
            f"{mtf_section}"
            f"{od_section}"
            f"{sr_section}"
            f"{vwap_section}"
            f"{s28_section}\n\n"
            f"🧠 *AI Insight:* _{sig['ai_insight']}_\n\n"
            f"⚠ _Educational only — not financial advice_"
        )
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    url,
                    json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                    timeout=5.0,
                )
            self.last_telegram_alert_time[symbol] = now
            logger.info(f"Telegram alert dispatched for {symbol}.")
        except Exception as e:
            logger.error(f"Telegram dispatch error for {symbol}: {e}")

    # ──────────────────────────────────────────────────────────────
    #  Fallback helpers
    # ──────────────────────────────────────────────────────────────

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

    @staticmethod
    def _vwap_signal_to_dict(sig: VWAPSignal) -> dict:
        return {
            "strategy_id":        sig.strategy_id,
            "strategy_name":      sig.strategy_name,
            "category":           sig.category,
            "sub_type":           sig.sub_type,
            "direction":          sig.direction,
            "price":              sig.price,
            "vwap":               sig.vwap,
            "entry":              sig.entry,
            "stop":               sig.stop,
            "t1":                 sig.t1,
            "t2":                 sig.t2,
            "rr":                 sig.rr,
            "confidence":         sig.confidence,
            "conditions_met":     sig.conditions_met,
            "score":              sig.score,
            "max_score":          sig.max_score,
            "vwap_distance_pct":  sig.vwap_distance_pct,
            "session_bias":       sig.session_bias,
            "vwap_touches_today": sig.vwap_touches_today,
            "premium_setup":      sig.premium_setup,
        }

    @staticmethod
    def _s28_signal_to_dict(sig: VWAPBreakoutSignal) -> dict:
        return {
            "strategy_id":    sig.strategy_id,
            "strategy_name":  sig.strategy_name,
            "direction":      sig.direction,
            "price":          sig.price,
            "entry":          sig.entry,
            "stop":           sig.stop,
            "t1":             sig.t1,
            "t2":             sig.t2,
            "rr":             sig.rr,
            "confidence":     sig.confidence,
            "quality":        sig.quality,
            "conditions_met": sig.conditions_met,
            "score":          sig.score,
            "max_score":      sig.max_score,
            "vwap":           sig.vwap,
            "ema9":           sig.ema9,
            "ema20":          sig.ema20,
            "ema200":         sig.ema200,
            "volume":         sig.volume,
            "rel_vol":        sig.rel_vol,
            "box_high":       sig.box_high,
            "box_low":        sig.box_low,
            "box_bars":       sig.box_bars,
            "atr":            sig.atr,
            "first_reclaim":  sig.first_reclaim,
            "premium_setup":  sig.premium_setup,
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
