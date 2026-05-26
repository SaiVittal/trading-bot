import os
import sys
import unittest
import asyncio
import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../apps/backend")))

from app.core.config import settings


class TestTelegramPosting(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_connection_and_dispatch(self):
        """Send a realistic mock alert in the production format to verify Telegram connectivity."""
        token   = settings.TELEGRAM_BOT_TOKEN
        chat_id = settings.TELEGRAM_CHAT_ID

        print(f"\n[TEST INFO] Token: {token[:15]}... (len {len(token) if token else 0})")
        print(f"[TEST INFO] Chat ID: {chat_id}")

        self.assertIsNotNone(token,   "TELEGRAM_BOT_TOKEN must be configured.")
        self.assertIsNotNone(chat_id, "TELEGRAM_CHAT_ID must be configured.")
        self.assertNotEqual(token, "your_telegram_bot_token", "Placeholder token detected.")

        # ── Mock signal matching candle_engine._dispatch_telegram payload ──
        sig = {
            "symbol":          "TSLA",
            "action":          "BUY",
            "session_time":    "10:22 ET",
            "top_strategy":    "S08",
            "top_strategy_name": "Volume Spike Breakout",
            "consensus_bull":  3,
            "consensus_bear":  1,
            "strategies_fired": ["S08", "S03", "S16", "S01"],
            "strategy_names":  [
                "Volume Spike Breakout",
                "Opening Momentum Breakout",
                "EMA9 Pullback Long",
                "VWAP Reclaim Long",
            ],
            "price":    418.50,
            "stop":     413.20,
            "t1":       426.80,
            "t2":       433.60,
            "rr":       1.6,
            "expected_range": [412.40, 428.90],
            "conditions_met": [
                "Volume spike 3.1× 20-bar average — institutional buying",
                "Price broke above VWAP and EMA9 simultaneously",
                "EMA9 curling upward — slope +0.82 over 3 bars",
                "RSI crossed above 50 — momentum confirmed",
            ],
            "confidence":  79,
            "vol_regime":  "high",
            "patterns":    ["bullish_engulfing", "morning_star"],
            "mtf_targets": [
                {"tf": "2m",  "trend": "bullish", "proj_hh": 420.60, "proj_ll": 415.80, "rsi": 61},
                {"tf": "5m",  "trend": "bullish", "proj_hh": 424.30, "proj_ll": 413.20, "rsi": 58},
                {"tf": "15m", "trend": "bullish", "proj_hh": 429.50, "proj_ll": 410.40, "rsi": 54},
                {"tf": "30m", "trend": "neutral", "proj_hh": 434.80, "proj_ll": 407.60, "rsi": 51},
                {"tf": "1h",  "trend": "bullish", "proj_hh": 441.20, "proj_ll": 403.10, "rsi": 56},
                {"tf": "1d",  "trend": "bullish", "proj_hh": 436.50, "proj_ll": 397.80,
                 "swing_high": 422.80, "swing_low": 411.30, "rsi": None},
            ],
            "od_signals": [
                {
                    "strategy_id":   "S19B",
                    "strategy_name": "Opening Drive — Pullback to PM S/R",
                    "premium_setup": True,
                    "gap_pct":       2.1,
                    "rvol":          4.2,
                    "rvol_quality":  "good",
                    "pm_high":       421.60,
                    "entry":  418.50, "stop": 415.30,
                    "t1":     424.90, "t2":   430.60, "rr": 2.0,
                    "confidence": 83, "score": 8, "max_score": 10,
                },
                {
                    "strategy_id":   "S19A",
                    "strategy_name": "Opening Drive — Gap Breakout",
                    "premium_setup": False,
                    "gap_pct":       2.1,
                    "rvol":          4.2,
                    "rvol_quality":  "good",
                    "pm_high":       421.60,
                    "entry":  418.50, "stop": 416.80,
                    "t1":     423.40, "t2":   429.80, "rr": 2.9,
                    "confidence": 69, "score": 5, "max_score": 8,
                },
            ],
            "sr_signals": [
                {
                    "strategy_id":      "S27",
                    "strategy_name":    "Prior Day Hi/Lo Bounce",
                    "direction":        "bullish",
                    "premium_setup":    True,
                    "sr_level_price":   416.90,
                    "sr_level_type":    "Prior Day High",
                    "sr_level_touches": 5,
                    "sr_level_strength": 9,
                    "entry": 418.50, "stop": 415.20,
                    "t1":    423.80, "t2":   429.10, "rr": 1.6,
                    "confidence": 85, "score": 7, "max_score": 8,
                },
                {
                    "strategy_id":      "S26",
                    "strategy_name":    "Round Number S/R",
                    "direction":        "bullish",
                    "premium_setup":    False,
                    "sr_level_price":   415.00,
                    "sr_level_type":    "Round Number",
                    "sr_level_touches": 3,
                    "sr_level_strength": 5,
                    "entry": 418.50, "stop": 414.60,
                    "t1":    422.30, "t2":   426.10, "rr": 1.0,
                    "confidence": 68, "score": 5, "max_score": 7,
                },
            ],
            "ai_insight": (
                "TSLA reclaiming Prior Day High at $416.90 with 4.2× RVOL on Opening Drive gap "
                "— institutional accumulation above key level signals continuation toward $424–430 target zone."
            ),
        }

        # ── Build text exactly as candle_engine._dispatch_telegram does ──
        emoji      = "🟢" if sig["action"] == "BUY" else "🔴"
        strategies = "\n".join(
            f"  • [{sid}] {name}"
            for sid, name in zip(sig["strategies_fired"], sig["strategy_names"])
        )
        conditions = "\n".join(f"  ✅ {c}" for c in sig["conditions_met"][:4])
        exp        = sig["expected_range"]
        rng_str    = f"${exp[0]} → ${exp[1]}"
        patterns   = ", ".join(p.replace("_", " ") for p in sig["patterns"])

        _TREND_ICON = {"bullish": "📈", "bearish": "📉", "neutral": "➡", "n/a": "❓"}
        mtf_lines = []
        for row in sig["mtf_targets"]:
            tf    = row["tf"]
            ticon = _TREND_ICON.get(row.get("trend", "n/a"), "❓")
            hh, ll, rsi = row.get("proj_hh"), row.get("proj_ll"), row.get("rsi")
            if tf == "1d":
                mtf_lines.append(
                    f"`{tf:<3}` {ticon}  Hi ${row['swing_high']}  Lo ${row['swing_low']}"
                    f"  →  ↑${hh}  ↓${ll}"
                )
            else:
                rsi_str = f"  RSI:{rsi:.0f}" if rsi is not None else ""
                mtf_lines.append(f"`{tf:<3}` {ticon}  ↑${hh}  ↓${ll}{rsi_str}")
        mtf_section = "\n".join(mtf_lines)

        od_lines = []
        for od in sig["od_signals"]:
            star  = " ⭐ PREMIUM" if od.get("premium_setup") else ""
            rq    = od.get("rvol_quality", "").upper()
            ph    = od.get("pm_high")
            pmh_s = f" | PM High ${ph}" if ph else ""
            od_lines.append(
                f"🚀 *[{od['strategy_id']}] {od['strategy_name']}*{star}\n"
                f"   Gap: {od['gap_pct']:+.1f}% | RVOL: {od['rvol']:.1f}× ({rq}){pmh_s}\n"
                f"   Entry: ${od['entry']:.2f} | Stop: ${od['stop']:.2f} | "
                f"T1: ${od['t1']:.2f} | T2: ${od['t2']:.2f} | R:R 1:{od['rr']}\n"
                f"   Conf: {od['confidence']}/100 | {od['score']}/{od['max_score']} conditions"
            )
        od_section = "\n\n📊 *Opening Drive Alerts:*\n" + "\n\n".join(od_lines)

        sr_lines = []
        for sr in sig["sr_signals"]:
            dir_em = "🟢" if sr["direction"] == "bullish" else "🔴"
            prem   = " ⭐ PREMIUM" if sr.get("premium_setup") else ""
            sr_lines.append(
                f"{dir_em} *[{sr['strategy_id']}] {sr['strategy_name']}*{prem}\n"
                f"  Level: ${sr['sr_level_price']}  ({sr['sr_level_type']}  ·  "
                f"{sr['sr_level_touches']} touches  ·  strength {sr['sr_level_strength']})\n"
                f"  Entry: ${sr['entry']}  Stop: ${sr['stop']}\n"
                f"  T1: ${sr['t1']}  T2: ${sr['t2']}  R:R 1:{sr['rr']}\n"
                f"  Conf: {sr['confidence']}/100  ({sr['score']}/{sr['max_score']} conditions)"
            )
        sr_section = (
            "\n\n📍 *S/R Strategy Signals (S20–S27):*\n"
            "─────────────────────────────\n"
            + "\n\n".join(sr_lines)
        )

        text = (
            f"{emoji} *{sig['symbol']} {sig['action']} ALERT* — {sig['session_time']}\n"
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
            f"{sr_section}\n\n"
            f"🧠 *AI Insight:* _{sig['ai_insight']}_\n\n"
            f"⚠ _Educational only — not financial advice_"
        )

        print("\n[TEST INFO] Message text to be sent:")
        print(text)

        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}

        print("\n[TEST INFO] Dispatching test alert to Telegram...")
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10.0)
            print(f"[TEST INFO] HTTP status: {resp.status_code}")
            print(f"[TEST INFO] Response: {resp.text}")

            self.assertEqual(resp.status_code, 200, f"Telegram failed: {resp.text}")
            data = resp.json()
            self.assertTrue(data.get("ok"), f"Telegram 'ok' was False: {data}")
            print("[TEST SUCCESS] Alert dispatched successfully to Telegram!")


if __name__ == "__main__":
    unittest.main()
