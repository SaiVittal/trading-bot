"""
OD Aggregator Bot — Pre-Market Open Drive Alert Aggregator
==========================================================
Listens for individual pre-market OD alerts, filters, merges, and
sends ONE consolidated alert to Telegram at 9:05, 9:20, and 9:35 ET.
Hard stop at 9:45 ET. No alerts sent outside this window.

Usage:
    python scripts/od_aggregator.py

Environment variables required (same as existing .env):
    TELEGRAM_BOT_TOKEN   — bot token for sending
    OD_SOURCE_CHAT_ID    — chat to poll for incoming OD alerts
    OD_DEST_CHAT_ID      — destination chat (-5568744831)
"""

import os
import sys
import time
import logging
import requests
from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────────────

BOT_TOKEN       = os.environ["TELEGRAM_BOT_TOKEN"]
SOURCE_CHAT_ID  = os.environ.get("OD_SOURCE_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", ""))
DEST_CHAT_ID    = os.environ.get("OD_DEST_CHAT_ID", "-5568744831")
TG_API          = f"https://api.telegram.org/bot{BOT_TOKEN}"
ET              = ZoneInfo("America/New_York")
POLL_INTERVAL   = 5  # seconds

# Run schedule (hour, minute) in ET
RUN_SCHEDULE = [
    (9,  5, "RUN 1/3", "9:05 ET — Early Pre-Market",  "⏱ Next Update: 9:20 ET"),
    (9, 20, "RUN 2/3", "9:20 ET — Mid Pre-Market",    "⏱ Next Update: 9:35 ET (Final)"),
    (9, 35, "RUN 3/3", "9:35 ET ⭐ FINAL Pre-Market", "🔴 Pre-market signals END after this alert\n⏱ Market opens in ~5 mins — trade ready!"),
]
HARD_STOP = (9, 45)

# Signal codes for OD classification
SIGNAL_CODES = {
    "gap_up_drive":       "[S1]  Gap Up + Drive Up continuation",
    "gap_down_drive":     "[S2]  Gap Down + Drive Down continuation",
    "pdh_breakout":       "[S3]  PDH Breakout Drive",
    "pdl_breakdown":      "[S4]  PDL Breakdown Drive",
    "vwap_reclaim":       "[S5]  VWAP Reclaim Drive Up",
    "vwap_rejection":     "[S6]  VWAP Rejection Drive Down",
    "pmh_break":          "[S7]  Pre-Market High Break Drive",
    "pml_break":          "[S8]  Pre-Market Low Break Drive",
    "orb_up":             "[S9]  Open Range Breakout Drive Up",
    "orb_down":           "[S10] Open Range Breakdown Drive Down",
    "momentum_surge":     "[S11] Momentum Surge Drive Up",
    "momentum_collapse":  "[S12] Momentum Collapse Drive Down",
    "pd_low_breakdown":   "[S23] Prev Day Low Breakdown Drive",
    "pd_high_breakout":   "[S24] Prev Day High Breakout Drive",
}

# ── State ─────────────────────────────────────────────────────────────────────

_last_update_id: int    = 0
_today: Optional[date]  = None

# Buffer of raw OD alert dicts keyed by ticker, per run window
# Structure: { ticker: { ...alert_fields, "run_first_seen": int, "status": "new"|"updated"|"confirmed" } }
_confirmed: dict = {}   # cumulative across all runs today
_current_window: dict = {}  # alerts collected in current window
_runs_fired: list[int] = []  # which run indices (0,1,2) have already fired

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s ET %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("od_aggregator")


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _tg_get_updates() -> list[dict]:
    global _last_update_id
    try:
        r = requests.get(
            f"{TG_API}/getUpdates",
            params={"offset": _last_update_id + 1, "timeout": 30},
            timeout=40,
        )
        r.raise_for_status()
        updates = r.json().get("result", [])
        if updates:
            _last_update_id = updates[-1]["update_id"]
        return updates
    except Exception as exc:
        logger.error("getUpdates error: %s", exc)
        return []


def _tg_send(text: str, retries: int = 3) -> bool:
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(
                f"{TG_API}/sendMessage",
                json={
                    "chat_id": DEST_CHAT_ID,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            r.raise_for_status()
            logger.info("Sent to %s (attempt %d)", DEST_CHAT_ID, attempt)
            return True
        except Exception as exc:
            logger.warning("Send attempt %d failed: %s", attempt, exc)
            if attempt < retries:
                time.sleep(30)
    logger.error("❌ FAILED: alert not sent to %s", DEST_CHAT_ID)
    return False


def _tg_send_split(text: str) -> bool:
    """Send message, splitting into parts if > 4096 chars."""
    LIMIT = 4096
    if len(text) <= LIMIT:
        return _tg_send(text)
    parts = []
    while text:
        chunk = text[:LIMIT]
        # try to split at newline boundary
        split_at = chunk.rfind("\n")
        if split_at > LIMIT // 2:
            chunk = text[:split_at]
        parts.append(chunk)
        text = text[len(chunk):]
    success = True
    for i, part in enumerate(parts, 1):
        header = f"🚀 *OD SUMMARY — PART {i}/{len(parts)}*\n\n"
        success = _tg_send(header + part) and success
        time.sleep(1)
    return success


# ── OD Signal Parsing ─────────────────────────────────────────────────────────

def _parse_od_alert(text: str) -> Optional[dict]:
    """
    Parse an incoming pre-market OD alert into a structured dict.
    Expects alerts in the format emitted by run_signals.ps1.
    Returns None if not a valid OD alert.
    """
    import re

    # Must contain OD signal indicator
    if "OD:" not in text and "Open Drive" not in text and "OPEN DRIVE" not in text.upper():
        return None

    ticker_m = re.search(r"\b([A-Z]{1,5})\b.*?(?:OD:|Open Drive)", text)
    if not ticker_m:
        ticker_m = re.search(r"Ticker\s*[:\-]\s*([A-Z]{1,5})", text, re.IGNORECASE)
    if not ticker_m:
        return None

    ticker = ticker_m.group(1).upper()

    # Direction
    direction = "LONG"
    if re.search(r"SHORT|sell|DN|put|bearish|drive.*down|down.*drive", text, re.IGNORECASE):
        direction = "SHORT"
    if re.search(r"LONG|buy|UP|call|bullish|drive.*up|up.*drive", text, re.IGNORECASE):
        direction = "LONG"

    # Price
    price_m = re.search(r"(?:Price|Entry|price)\s*[=:\$]?\s*([\d.]+)", text, re.IGNORECASE)
    price = float(price_m.group(1)) if price_m else 0.0

    # RVOL
    rvol_m = re.search(r"RVOL\s*[=:]?\s*([\d.]+)x?", text, re.IGNORECASE)
    rvol = float(rvol_m.group(1)) if rvol_m else 1.0

    # RSI
    rsi_m = re.search(r"RSI\s*[=:]?\s*([\d.]+)", text, re.IGNORECASE)
    rsi = float(rsi_m.group(1)) if rsi_m else 50.0

    # Gap
    gap_m = re.search(r"Gap\s*[=:]?\s*([+-]?[\d.]+)%?", text, re.IGNORECASE)
    gap = float(gap_m.group(1)) if gap_m else 0.0

    # Confidence / Top
    conf_m = re.search(r"(?:Top|Conf|top)\s*[=:]?\s*(\d+)%?", text, re.IGNORECASE)
    base_conf = int(conf_m.group(1)) if conf_m else 65

    # VWAP position
    vwap_above = True
    if re.search(r"below\s+VWAP|VWAP.*below", text, re.IGNORECASE):
        vwap_above = False

    # Determine signal code
    has_gap = abs(gap) > 0.5
    if direction == "LONG":
        if has_gap and gap > 0:
            signal_code = "[S1]"
            signal_desc = "Gap Up + Drive Up continuation"
        elif vwap_above:
            signal_code = "[S5]"
            signal_desc = "VWAP Reclaim Drive Up"
        else:
            signal_code = "[S9]"
            signal_desc = "Open Range Breakout Drive Up"
    else:
        if has_gap and gap < 0:
            signal_code = "[S2]"
            signal_desc = "Gap Down + Drive Down continuation"
        elif not vwap_above:
            signal_code = "[S6]"
            signal_desc = "VWAP Rejection Drive Down"
        else:
            signal_code = "[S10]"
            signal_desc = "Open Range Breakdown Drive Down"

    # Confidence scoring (cap 95)
    conf = base_conf
    if rvol >= 2.0:
        conf = min(95, conf + 8)
    if vwap_above == (direction == "LONG"):
        conf = min(95, conf + 8)
    if has_gap:
        conf = min(95, conf + 10)

    # Scorecard
    if conf >= 80:
        scorecard = "STRONG"
    elif conf >= 65:
        scorecard = "WATCH"
    else:
        scorecard = "CAUTION"

    # Option strike (nearest $5 increment OTM)
    if direction == "LONG":
        strike = round(price / 5 + 0.5) * 5
        option = f"CALL ${strike:.0f}"
        option_note = f"(stock above ${price:.2f})"
    else:
        strike = round(price / 5 - 0.5) * 5
        option = f"PUT ${strike:.0f}"
        option_note = f"(stock below ${price:.2f})"

    # Entry / Stop / Targets
    atr_m = re.search(r"ATR\s*[=:]?\s*([\d.]+)", text, re.IGNORECASE)
    atr = float(atr_m.group(1)) if atr_m else price * 0.02
    if direction == "LONG":
        entry = price
        stop  = round(price - atr * 0.5, 2)
        t1    = round(price + atr * 1.5, 2)
        t2    = round(price + atr * 3.0, 2)
    else:
        entry = price
        stop  = round(price + atr * 0.5, 2)
        t1    = round(price - atr * 1.5, 2)
        t2    = round(price - atr * 3.0, 2)

    vwap_status = "above" if vwap_above else "below"

    return {
        "ticker":      ticker,
        "direction":   direction,
        "price":       price,
        "rvol":        rvol,
        "rsi":         rsi,
        "gap":         gap,
        "vwap_above":  vwap_above,
        "signal_code": signal_code,
        "signal_desc": signal_desc,
        "scorecard":   scorecard,
        "confidence":  conf,
        "option":      option,
        "option_note": option_note,
        "entry":       entry,
        "stop":        stop,
        "t1":          t1,
        "t2":          t2,
        "note":        f"RVOL={rvol:.2f}x | {vwap_status} VWAP | Gap={gap:+.1f}% | RSI={rsi:.1f}",
        "raw":         text,
    }


def _is_valid_od(alert: dict) -> bool:
    """Filter: keep only genuine OD signals."""
    # Must have reasonable confidence
    if alert["confidence"] < 60:
        return False
    # Must have some volume
    if alert["rvol"] < 0.8:
        return False
    return True


# ── Alert Collection ──────────────────────────────────────────────────────────

def _collect_update(update: dict) -> None:
    """Process one Telegram update, extracting OD alerts into current window."""
    msg = update.get("message") or update.get("channel_post")
    if not msg:
        return
    text = msg.get("text", "")
    if not text:
        return

    # Only process messages from the source chat
    chat_id = str(msg.get("chat", {}).get("id", ""))
    if SOURCE_CHAT_ID and chat_id != str(SOURCE_CHAT_ID):
        return

    alert = _parse_od_alert(text)
    if alert is None:
        return
    if not _is_valid_od(alert):
        logger.info("Filtered out %s (conf=%d, rvol=%.2f)", alert["ticker"], alert["confidence"], alert["rvol"])
        return

    ticker = alert["ticker"]
    _current_window[ticker] = alert
    logger.info("Collected OD alert: %s %s conf=%d%%", ticker, alert["direction"], alert["confidence"])


# ── Message Building ──────────────────────────────────────────────────────────

def _ticker_block(alert: dict, status_label: str) -> str:
    d = alert
    dir_str = "[UP] LONG" if d["direction"] == "LONG" else "[DN] SHORT"
    lines = [
        f"──────────────────────────────────────",
        f"Ticker : {d['ticker']}  {status_label}",
        f"--- OPENING DRIVE (Pre-Market Setup) ---",
        f"{d['signal_code']} {d['signal_desc']}",
        f"Scorecard  : {d['scorecard']}",
        f"Direction  : {dir_str}",
        f"Option     : {d['option']}",
        f"             {d['option_note']}",
        f"Confidence : {d['confidence']}%",
        f"Entry(stk) : ${d['entry']:.2f}  Stop(stk) : ${d['stop']:.2f}",
        f"Target 1   : ${d['t1']:.2f}  Target 2  : ${d['t2']:.2f}",
        f"Note       : {d['note']}",
        f"──────────────────────────────────────",
    ]
    return "\n".join(lines)


def _ranking_score(alert: dict) -> int:
    score = 0
    if alert["rvol"] >= 2.0:
        score += 2
    if abs(alert["gap"]) > 0.5:
        score += 2
    if alert["vwap_above"] == (alert["direction"] == "LONG"):
        score += 2
    if alert["scorecard"] == "STRONG":
        score += 2
    score += min(2, int(alert["confidence"] / 10) - 7)
    return score


def _build_alert(run_idx: int, new_alerts: dict, updated_alerts: dict, carried_alerts: dict,
                 all_scanned: int, excluded: list) -> str:
    run_label, run_time, footer = RUN_SCHEDULE[run_idx][2], RUN_SCHEDULE[run_idx][3], RUN_SCHEDULE[run_idx][4]
    today_str = datetime.now(ET).strftime("%B %d, %Y")

    total_od = len(new_alerts) + len(updated_alerts) + len(carried_alerts)

    lines = [
        f"🚀 *PRE-MARKET OPEN DRIVE SUMMARY*",
        f"🕘 {run_label} | {run_time}",
        f"📅 {today_str}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Scanned      : {all_scanned} tickers",
        f"✅ OD Confirmed : {total_od}",
        f"🆕 New This Run : {len(new_alerts)}",
        f"🔄 Updated      : {len(updated_alerts)}",
        f"✅ Carried Fwd  : {len(carried_alerts)}",
        f"❌ Filtered Out : {len(excluded)}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # Bullish
    bullish = {t: a for t, a in {**new_alerts, **updated_alerts, **carried_alerts}.items() if a["direction"] == "LONG"}
    bearish = {t: a for t, a in {**new_alerts, **updated_alerts, **carried_alerts}.items() if a["direction"] == "SHORT"}

    if bullish:
        lines.append(f"\n🟢 *BULLISH OPEN DRIVES* ({len(bullish)})")
        lines.append("════════════════════════════════════")
        for ticker, alert in sorted(bullish.items()):
            if ticker in new_alerts:
                label = "🆕 New"
            elif ticker in updated_alerts:
                label = "🔄 Updated"
            else:
                label = "✅ Confirmed"
            lines.append(_ticker_block(alert, label))

    if bearish:
        lines.append(f"\n🔴 *BEARISH OPEN DRIVES* ({len(bearish)})")
        lines.append("════════════════════════════════════")
        for ticker, alert in sorted(bearish.items()):
            if ticker in new_alerts:
                label = "🆕 New"
            elif ticker in updated_alerts:
                label = "🔄 Updated"
            else:
                label = "✅ Confirmed"
            lines.append(_ticker_block(alert, label))

    if total_od == 0:
        carried_count = len(carried_alerts)
        lines.append(f"\n⚠️ {run_label}: No new OD signals this window.")
        if carried_count:
            lines.append(f" {carried_count} signals carried from prior run(s).")
        lines.append(" Monitoring continues...")

    # Top picks
    all_alerts = {**new_alerts, **updated_alerts, **carried_alerts}
    if all_alerts:
        ranked = sorted(all_alerts.values(), key=_ranking_score, reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⭐ *TOP OD PICKS THIS RUN*")
        lines.append("────────────────────────────────────")
        for i, alert in enumerate(ranked[:3]):
            dir_emoji = "📈" if alert["direction"] == "LONG" else "📉"
            lines.append(f"{medals[i]} {alert['ticker']} — {dir_emoji} {alert['direction']} | {alert['signal_code']} | Conf {alert['confidence']}% | RVOL {alert['rvol']:.2f}x")

    # Excluded / watch list
    if total_od == 1:
        lines.append("\n⚠️ Low OD day — only 1 confirmed drive.")
        lines.append(" Consider reduced position size.")

    if excluded:
        lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚠️ *EXCLUDED / WATCH LIST*")
        lines.append("────────────────────────────────────")
        for item in excluded:
            lines.append(f"{item['ticker']} — {item['reason']}")

    # Market pulse placeholder
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📌 *MARKET PULSE*")
    lines.append("────────────────────────────────────")
    lines.append("SPY  → Monitoring | Check VWAP position")
    lines.append("QQQ  → Monitoring | Check VWAP position")
    lines.append("VIX  → Monitoring | Check fear level")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    lines.append(f"\n{footer}")
    lines.append(f"🤖 OD Aggregator | Chat: {DEST_CHAT_ID}")

    return "\n".join(lines)


# ── Run Execution ─────────────────────────────────────────────────────────────

def _fire_run(run_idx: int) -> None:
    global _confirmed, _current_window

    run_label = RUN_SCHEDULE[run_idx][2]
    logger.info("Firing %s", run_label)

    new_alerts     = {}
    updated_alerts = {}
    carried_alerts = {}
    excluded       = []

    # Classify alerts in current window
    for ticker, alert in _current_window.items():
        if ticker in _confirmed:
            prev = _confirmed[ticker]
            if prev["direction"] != alert["direction"] or prev["signal_code"] != alert["signal_code"]:
                updated_alerts[ticker] = alert
                alert["run_first_seen"] = run_idx
            # else same signal — will be in carried
        else:
            new_alerts[ticker] = alert
            alert["run_first_seen"] = run_idx

    # Carried = in confirmed from prior run but NOT in current window new/updated
    for ticker, alert in _confirmed.items():
        if ticker not in new_alerts and ticker not in updated_alerts:
            carried_alerts[ticker] = alert

    # Update cumulative confirmed
    _confirmed.update(new_alerts)
    _confirmed.update(updated_alerts)

    # Excluded: anything that came in this window but failed filter
    # (already filtered before reaching _current_window, logged separately)

    all_scanned = len(_confirmed) + len(excluded)

    text = _build_alert(run_idx, new_alerts, updated_alerts, carried_alerts, all_scanned, excluded)
    _tg_send_split(text)

    # Clear window for next run
    _current_window.clear()
    _runs_fired.append(run_idx)
    logger.info("%s complete. Confirmed tickers: %s", run_label, list(_confirmed.keys()))


# ── Daily Reset ───────────────────────────────────────────────────────────────

def _reset_day() -> None:
    global _confirmed, _current_window, _runs_fired, _today
    _confirmed.clear()
    _current_window.clear()
    _runs_fired.clear()
    _today = datetime.now(ET).date()
    logger.info("Daily reset. New trading day: %s", _today)


# ── Main Loop ─────────────────────────────────────────────────────────────────

def run() -> None:
    global _today

    logger.info("OD Aggregator started. Destination: %s", DEST_CHAT_ID)
    _reset_day()

    while True:
        now = datetime.now(ET)

        # Daily reset at midnight
        if now.date() != _today:
            _reset_day()

        h, m = now.hour, now.minute

        # Hard stop check
        if (h, m) >= HARD_STOP and now.date() == _today:
            # After 9:45 — just poll and discard, log if needed
            updates = _tg_get_updates()
            if updates:
                logger.info("🔴 SYSTEM STOPPED — Pre-market window closed at 9:45 ET. %d updates discarded.", len(updates))
            time.sleep(POLL_INTERVAL)
            continue

        # Collect incoming alerts (only during 9:00–9:45 window)
        if h == 9 and m < 45:
            updates = _tg_get_updates()
            for upd in updates:
                _collect_update(upd)

        # Check if any run should fire
        for idx, (run_h, run_m, label, _, _) in enumerate(RUN_SCHEDULE):
            if idx in _runs_fired:
                continue
            if h > run_h or (h == run_h and m >= run_m):
                # Only fire if within send window
                if (h, m) < HARD_STOP:
                    _fire_run(idx)
                else:
                    logger.warning("⚠️ Delayed send for %s pushed past 9:45 ET — skipping.", label)
                    _runs_fired.append(idx)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
