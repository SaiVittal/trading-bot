import asyncio
import json
import logging
import math
import time
from collections import deque
from typing import Dict, Optional

import numpy as np
import pandas as pd
import httpx

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

logger = logging.getLogger("app.services.candle_engine")

# Redis channels
REDIS_TICK_CHANNEL   = "market:ticks"
REDIS_CANDLE_CHANNEL = "market:candles"
REDIS_ALERT_CHANNEL  = "signals:alerts"

# Candle accumulation window (seconds)
CANDLE_WINDOW_SECS = 5

# 2 hours of 5s candles — enough for meaningful 5m/15m resampling
CANDLE_HISTORY_SIZE = 1440

# Stochastic parameters
STOCH_K_PERIOD = 5
STOCH_D_PERIOD = 3
STOCH_SMOOTH   = 3
STOCH_OB       = 80
STOCH_OS       = 20

# RSI parameters
RSI_PERIOD = 14
RSI_OS     = 35
RSI_OB     = 65

# VWAP proximity gate (%)
VWAP_TOLERANCE_PCT = 0.3

# Volume spike multiplier (used for gate only; VolumeEngine handles richer stats)
VOLUME_MA_PERIOD  = 20
VOLUME_SPIKE_MULT = 1.5


class RealtimeCandleEngine:
    def __init__(self) -> None:
        self.active_candles:        Dict[str, Dict]  = {}
        self.candle_start_times:    Dict[str, float] = {}
        # deque gives O(1) append/pop vs list's O(n) pop(0)
        self.closed_candles_history: Dict[str, deque] = {}
        self.last_telegram_alert_time: Dict[str, float] = {}

        # Upgrade engines — all stateless, safe to share across ticks
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

        # Optional OpenAI for enhanced narrative (graceful no-op if absent)
        self._openai = None
        openai_key   = settings.OPENAI_API_KEY or ""
        if openai_key and "your_openai_api_key" not in openai_key:
            try:
                from openai import AsyncOpenAI
                self._openai = AsyncOpenAI(api_key=openai_key)
                logger.info("OpenAI detected — enhanced AI insights active.")
            except ImportError:
                logger.warning("openai package missing; using InsightGenerator narrative.")
        else:
            logger.info("No OpenAI key — using InsightGenerator for AI narratives.")

    # ──────────────────────────────────────────────────────────────
    #  Main loop
    # ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        logger.info("Initializing Multi-Symbol Candle Engine...")
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

        active    = self.active_candles[symbol]
        start     = self.candle_start_times[symbol]

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
        if not history or len(history) < 25:
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
    #  Signal evaluation + upgrade pipeline
    # ──────────────────────────────────────────────────────────────

    async def evaluate_strategy_checklist(self, symbol: str) -> None:
        df_raw = self._build_dataframe(symbol)
        if df_raw is None or len(df_raw) < 5:
            return

        # Run indicators (STC_K/D, RSI, VWAP, ATR, EMA9/21)
        try:
            df = self._indicator.run(df_raw)
        except Exception as e:
            logger.warning(f"IndicatorEngine failed for {symbol}: {e}")
            return

        cur  = df.iloc[-1]
        prev = df.iloc[-2]

        # Extract and validate indicator values
        price   = float(cur["Close"])
        vwap    = float(cur.get("VWAP") or price)
        k       = cur.get("STC_K")
        d       = cur.get("STC_D")
        pk      = prev.get("STC_K")
        pd_     = prev.get("STC_D")
        rsi     = cur.get("RSI")
        atr     = cur.get("ATR")
        vol_ma  = df["Volume"].rolling(VOLUME_MA_PERIOD).mean().iloc[-1]

        required = [k, d, pk, pd_, rsi, atr, vol_ma]
        if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in required):
            return

        k, d, pk, pd_ = float(k), float(d), float(pk), float(pd_)
        rsi, atr, vol_ma = float(rsi), float(atr), float(vol_ma)
        vol = float(cur["Volume"])

        vwap_pct_diff = abs(price - vwap) / vwap * 100 if vwap > 0 else 0

        # ── PRIMARY SIGNAL GATE (stochastic crossover + RSI + VWAP) ──────────
        stoch_cross_up  = pk < pd_ and k > d
        stoch_cross_dn  = pk > pd_ and k < d
        stoch_oversold  = pk < STOCH_OS
        stoch_overbought = pk > STOCH_OB
        rsi_oversold    = rsi < RSI_OS
        rsi_overbought  = rsi > RSI_OB
        near_vwap       = vwap_pct_diff < VWAP_TOLERANCE_PCT
        vol_spike_gate  = vol > vol_ma * VOLUME_SPIKE_MULT

        buy_score  = sum([stoch_cross_up, stoch_oversold, rsi_oversold,
                          near_vwap or price < vwap, vol_spike_gate])
        sell_score = sum([stoch_cross_dn, stoch_overbought, rsi_overbought,
                          near_vwap or price > vwap, vol_spike_gate])

        if stoch_cross_up and stoch_oversold and buy_score >= 3:
            action = "BUY"
        elif stoch_cross_dn and stoch_overbought and sell_score >= 3:
            action = "SELL"
        else:
            return

        direction  = "bullish" if action == "BUY" else "bearish"
        stc_cross  = (f"{k:.1f} crossed above {d:.1f}" if action == "BUY"
                      else f"{k:.1f} crossed below {d:.1f}")
        vwap_state = "At VWAP" if near_vwap else ("Below VWAP" if price < vwap else "Above VWAP")

        # ── UPGRADE PIPELINE ─────────────────────────────────────────────────
        try:
            candle_data  = self._candles.run(df)
            mtf_data     = self._mtf.run(df_raw)
            range_data   = self._range.run(df, direction, candle_data)
            vol_data     = self._volatility.run(df)
            volume_data  = self._volume.run(df)
            prob_data    = self._prob.run(mtf_data, df, vol_data, volume_data, candle_data)

            if prob_data["confidence"] < settings.MIN_CONFIDENCE:
                logger.info(
                    f"{symbol} {action} gate passed but confidence "
                    f"{prob_data['confidence']}/100 < threshold {settings.MIN_CONFIDENCE}. Suppressed."
                )
                return

            risk_data   = self._risk.run(price, direction, vol_data["atr"], range_data)
            base_insight = self._insight.generate(
                symbol, prob_data, mtf_data, vol_data, volume_data, candle_data, range_data
            )
            insight_txt = await self._enhance_insight(
                symbol, action, price, rsi, vwap, stc_cross, base_insight
            )

        except Exception as e:
            logger.error(f"Upgrade pipeline failed for {symbol}: {e}", exc_info=True)
            # Graceful fallback — fire a basic alert so the signal isn't lost entirely
            risk_data    = self._basic_risk(price, action, atr)
            prob_data    = self._empty_prob(direction)
            mtf_data     = self._empty_mtf(direction)
            vol_data     = {"regime": "normal", "atr": round(atr, 4), "atr_avg": round(atr, 4),
                            "expanding": False, "vwap_dist_pct": round(vwap_pct_diff, 2),
                            "rolling_vol": 0}
            volume_data  = {"rel_vol": round(vol / vol_ma, 2), "spike": vol_spike_gate,
                            "exhaustion": False, "confirmation": False,
                            "vol_trend": "normal", "avg_volume": int(vol_ma)}
            candle_data  = {"patterns": [], "bias": "neutral",
                            "pattern_target": None, "candle_expansion": 1.0}
            range_data   = {"expected_low": round(price - atr * 2.5, 2),
                            "expected_high": round(price + atr * 2.5, 2),
                            "support": [], "resistance": [], "atr": round(atr, 4),
                            "sr_target": None}
            insight_txt  = await self._enhance_insight(
                symbol, action, price, rsi, vwap, stc_cross, ""
            ) or f"Stochastic crossover for {symbol} at ${price:.2f}. RSI: {rsi:.1f}."

        # ── FORMAT & DISPATCH ────────────────────────────────────────────────
        console_msg   = self._formatter.format_console(
            symbol, prob_data, mtf_data, vol_data, volume_data,
            candle_data, range_data, risk_data, insight_txt
        )
        slack_payload = self._formatter.format_slack(
            symbol, prob_data, mtf_data, vol_data, volume_data,
            candle_data, range_data, risk_data, insight_txt
        )

        legacy_str = (
            f"{symbol} {action} ALERT | Price: ${price:.2f} | "
            f"STC: {stc_cross} | RSI: {int(rsi)} | {vwap_state} | "
            f"Stop: ${risk_data['stop']:.2f} | T1: ${risk_data['t1']:.2f} | "
            f"T2: ${risk_data['t2']:.2f} | Confidence: {prob_data['confidence']}/100"
        )

        alert_payload = {
            "symbol":           symbol,
            "action":           action,
            "price":            price,
            "rsi":              int(rsi),
            "vwap":             round(vwap, 2),
            "stc":              stc_cross,
            "stop":             risk_data["stop"],
            "t1":               risk_data["t1"],
            "t2":               risk_data["t2"],
            "rr":               risk_data["rr"],
            "confidence":       prob_data["confidence"],
            "confidence_label": prob_data["confidence_label"],
            "patterns":         candle_data["patterns"],
            "expected_range":   [range_data["expected_low"], range_data["expected_high"]],
            "vol_regime":       vol_data["regime"],
            "vol_rel":          volume_data["rel_vol"],
            "mtf_alignment":    prob_data.get("scores", {}).get("trend", 0),
            "message":          legacy_str,
            "ai_insight":       insight_txt,
            "timestamp":        time.time(),
        }

        logger.info(f"Signal fired — {legacy_str}")
        logger.debug(console_msg)

        if redis_client.client:
            await redis_client.client.publish(
                REDIS_ALERT_CHANNEL, json.dumps(alert_payload)
            )

        # Fire-and-forget dispatch with error logging
        def _on_task_done(task: asyncio.Task, label: str) -> None:
            exc = task.exception() if not task.cancelled() else None
            if exc:
                logger.error(f"{label} dispatch task failed: {exc}")

        slack_task = asyncio.create_task(
            self._dispatch_slack(slack_payload)
        )
        slack_task.add_done_callback(lambda t: _on_task_done(t, "Slack"))

        tg_task = asyncio.create_task(
            self._dispatch_telegram(alert_payload)
        )
        tg_task.add_done_callback(lambda t: _on_task_done(t, "Telegram"))

    # ──────────────────────────────────────────────────────────────
    #  OpenAI enhancement (optional, no-ops gracefully)
    # ──────────────────────────────────────────────────────────────

    async def _enhance_insight(self, symbol: str, action: str, price: float,
                                rsi: float, vwap: float, stc: str, base: str) -> str:
        if not self._openai:
            return base

        prompt = (
            f"Institutional quant insight — one sentence, max 30 words:\n"
            f"{symbol} {action} at ${price:.2f} | STC: {stc} | RSI: {rsi:.1f} | VWAP: ${vwap:.2f}\n"
            f"Base: {base}"
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
    #  Slack dispatch (Block Kit)
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
        cooldown = settings.TELEGRAM_ALERT_COOLDOWN

        if now - last < cooldown:
            logger.info(
                f"Telegram cooldown active for {symbol} "
                f"({now - last:.1f}s elapsed / {cooldown}s required)."
            )
            return

        emoji    = "🟢" if sig["action"] == "BUY" else "🔴"
        patterns = (", ".join(p.replace("_", " ") for p in sig.get("patterns", []))
                    or "None")
        exp      = sig.get("expected_range", [])
        rng_str  = f"${exp[0]} → ${exp[1]}" if len(exp) == 2 else "N/A"

        text = (
            f"{emoji} *{symbol} {sig['action']} ALERT*\n"
            f"Price: ${sig['price']:.2f} | VWAP: ${sig['vwap']:.2f}\n"
            f"STC: {sig['stc']} | RSI-14: {sig['rsi']}\n"
            f"Entry: ${sig['price']:.2f} | Stop: ${sig['stop']:.2f}\n"
            f"T1: ${sig['t1']:.2f} | T2: ${sig['t2']:.2f} | R:R 1:{sig['rr']}\n"
            f"Expected Range: {rng_str}\n"
            f"Confidence: {sig['confidence']}/100 ({sig['confidence_label']})\n"
            f"Patterns: {patterns}\n"
            f"Volatility: {sig['vol_regime'].upper()}\n\n"
            f"🧠 *AI Insight:* _{sig['ai_insight']}_"
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
    #  Fallback helpers (used when upgrade pipeline errors)
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _basic_risk(price: float, action: str, atr: float) -> dict:
        mult = 1.0 if action == "BUY" else -1.0
        stop = round(price - mult * atr, 2)
        t1   = round(price + mult * atr * 1.5, 2)
        t2   = round(price + mult * atr * 2.5, 2)
        risk = abs(price - stop)
        return {"entry": round(price, 2), "stop": stop, "t1": t1, "t2": t2,
                "rr": round(abs(price - t1) / risk, 2) if risk > 0 else 0,
                "risk_$": round(risk, 2)}

    @staticmethod
    def _empty_prob(direction: str) -> dict:
        return {"direction": direction, "confidence": 0, "confidence_label": "N/A",
                "continuation_probability": 0, "reversal_probability": 0, "scores": {
                    "trend": 0, "momentum": 0, "volume": 0, "volatility": 0, "pattern": 0}}

    @staticmethod
    def _empty_mtf(direction: str) -> dict:
        empty_tf = {"trend": "unknown", "rsi": None, "above_vwap": None,
                    "stoch_signal": "neutral", "stoch_k": None}
        return {"timeframes": {tf: empty_tf for tf in ["base", "5m", "15m", "1h"]},
                "dominant": direction, "aligned_tfs": 0, "alignment_pct": 0}


async def start_candle_engine() -> None:
    engine = RealtimeCandleEngine()
    await engine.run()
