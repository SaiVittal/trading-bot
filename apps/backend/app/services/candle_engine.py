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
_ALL_15 = {"S01","S02","S03","S04","S05","S06","S07",
           "S08","S09","S10","S11","S12","S13","S14","S15"}

SESSION_MAP: List[tuple] = [
    # (start, end, allowed_strategy_ids)
    (dtime(9, 30), dtime(9, 35),  set()),                            # wild open — observe only
    (dtime(9, 35), dtime(10, 0),  {"S03","S04","S08","S13"}),        # opening momentum
    (dtime(10, 0), dtime(11, 0),  _ALL_15),                          # primary session
    (dtime(11, 0), dtime(12, 0),  {"S01","S02","S11","S12"}),        # mid-morning
    (dtime(12, 0), dtime(14, 0),  {"S05"}),                          # lunch — MR only
    (dtime(14, 0), dtime(15, 0),  {"S01","S02","S11","S12","S15"}),  # afternoon resume
    (dtime(15, 0), dtime(15, 45), {"S08","S13","S15"}),              # power hour
    (dtime(15,45), dtime(16, 0),  set()),                            # close — exit only
]


def _session_allowed(now_et: datetime) -> Set[str]:
    """Return the set of strategy IDs allowed for the current ET time."""
    t = now_et.time()
    for start, end, ids in SESSION_MAP:
        if start <= t < end:
            return ids
    return set()   # outside market hours


# Per-strategy minimum confidence (Part 7 of guide)
STRATEGY_MIN_CONF: Dict[str, int] = {
    "S01": 55, "S02": 55, "S03": 55, "S04": 55, "S05": 50,
    "S06": 60, "S07": 60, "S08": 60, "S09": 55, "S10": 55,
    "S11": 55, "S12": 55, "S13": 60, "S14": 65, "S15": 60,
}

CATEGORY_EMOJI = {"VWAP": "💧", "REVERSAL": "🔄", "TREND": "📈", "SQUEEZE": "💥"}


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
        logger.info("Initializing Multi-Symbol Candle Engine (15 strategies)...")
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

        # ── Run all 15 strategies across 1m / 5m / 15m ──────────
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
            candle_data, insight_txt, bull, bear,
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
            {"type": "context", "elements": [{"type": "mrkdwn",
             "text": "⚠ Educational only — not financial advice"}]},
        ]
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


async def start_candle_engine() -> None:
    engine = RealtimeCandleEngine()
    await engine.run()
