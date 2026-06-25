"""
OD Aggregator Bot - Single-Run Mode
====================================
Called once per scheduled run (9:05, 9:20, 9:35 ET).
Reads recent Telegram messages, filters OD signals,
loads/saves cumulative state, sends merged alert.

Usage:
    python scripts/od_aggregator.py --run 1   # 9:05 ET (RUN 1/3)
    python scripts/od_aggregator.py --run 2   # 9:20 ET (RUN 2/3)
    python scripts/od_aggregator.py --run 3   # 9:35 ET (RUN 3/3 FINAL)
"""

import os
import sys
import json
import time
import logging
import argparse
import requests
import re
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
SOURCE_CHAT_ID = os.environ.get("OD_SOURCE_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", ""))
DEST_CHAT_ID   = os.environ.get("OD_DEST_CHAT_ID", "-5568744831")
TG_API         = f"https://api.telegram.org/bot{BOT_TOKEN}"
ET             = ZoneInfo("America/New_York")

REPO_ROOT  = Path(__file__).resolve().parent.parent
STATE_DIR  = REPO_ROOT / "state"
STATE_FILE = STATE_DIR / f"od_state_{date.today()}.json"

TICKERS = {
    "APP", "TSLA", "NVDA", "QQQ", "SPY", "META", "MSFT", "AMZN",
    "AAPL", "INTC", "NOW", "HOOD", "PLTR", "NFLX", "NBIS",
}

RUN_META = {
    1: ("RUN 1/3", "9:05 ET - Early Pre-Market",  "Next Update: 9:20 ET"),
    2: ("RUN 2/3", "9:20 ET - Mid Pre-Market",     "Next Update: 9:35 ET (Final)"),
    3: ("RUN 3/3", "9:35 ET FINAL Pre-Market",     "Market opens in ~5 mins - trade ready!"),
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("od_aggregator")

# ── State persistence ─────────────────────────────────────────────────────────

def load_state() -> dict:
    STATE_DIR.mkdir(exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"confirmed": {}, "last_update_id": 0}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Telegram ──────────────────────────────────────────────────────────────────

def tg_get_updates(last_id: int) -> tuple[list[dict], int]:
    try:
        r = requests.get(
            f"{TG_API}/getUpdates",
            params={"offset": last_id + 1, "limit": 100, "timeout": 10},
            timeout=20,
        )
        r.raise_for_status()
        updates = r.json().get("result", [])
        new_last = updates[-1]["update_id"] if updates else last_id
        return updates, new_last
    except Exception as e:
        log.error("getUpdates failed: %s", e)
        return [], last_id


def tg_send(text: str, retries: int = 3) -> bool:
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
            return True
        except Exception as e:
            log.warning("Send attempt %d failed: %s", attempt, e)
            if attempt < retries:
                time.sleep(30)
    log.error("FAILED to send alert to %s", DEST_CHAT_ID)
    return False


def tg_send_split(text: str) -> bool:
    LIMIT = 4096
    if len(text) <= LIMIT:
        return tg_send(text)
    parts = []
    while text:
        chunk = text[:LIMIT]
        split_at = chunk.rfind("\n")
        if split_at > LIMIT // 2:
            chunk = text[:split_at]
        parts.append(chunk)
        text = text[len(chunk):]
    ok = True
    for i, part in enumerate(parts, 1):
        ok = tg_send(f"OD SUMMARY - PART {i}/{len(parts)}\n\n{part}") and ok
        time.sleep(1)
    return ok


# ── OD Signal Parsing ─────────────────────────────────────────────────────────

def parse_od_alert(text: str) -> dict | None:
    if not re.search(r"OD:|Open Drive|OPEN DRIVE", text, re.IGNORECASE):
        return None

    ticker_m = re.search(r"\b([A-Z]{1,5})\b", text)
    if not ticker_m:
        return None
    ticker = ticker_m.group(1)

    direction = "LONG"
    if re.search(r"SHORT|sell|DN|put|bearish|drive.down|down.drive", text, re.IGNORECASE):
        direction = "SHORT"
    if re.search(r"LONG|buy|UP|call|bullish|drive.up|up.drive", text, re.IGNORECASE):
        direction = "LONG"

    price_m = re.search(r"(?:Price|Entry|price)\s*[=:\$]?\s*([\d.]+)", text, re.IGNORECASE)
    price = float(price_m.group(1)) if price_m else 0.0

    rvol_m = re.search(r"RVOL\s*[=:]?\s*([\d.]+)", text, re.IGNORECASE)
    rvol = float(rvol_m.group(1)) if rvol_m else 1.0

    rsi_m = re.search(r"RSI\s*[=:]?\s*([\d.]+)", text, re.IGNORECASE)
    rsi = float(rsi_m.group(1)) if rsi_m else 50.0

    gap_m = re.search(r"Gap\s*[=:]?\s*([+-]?[\d.]+)%?", text, re.IGNORECASE)
    gap = float(gap_m.group(1)) if gap_m else 0.0

    conf_m = re.search(r"(?:Top|Conf|top)\s*[=:]?\s*(\d+)%?", text, re.IGNORECASE)
    base_conf = int(conf_m.group(1)) if conf_m else 65

    vwap_above = not bool(re.search(r"below\s+VWAP|VWAP.*below", text, re.IGNORECASE))
    has_gap = abs(gap) > 0.5

    if direction == "LONG":
        if has_gap and gap > 0:
            signal_code, signal_desc = "[S1]", "Gap Up + Drive Up continuation"
        elif vwap_above:
            signal_code, signal_desc = "[S5]", "VWAP Reclaim Drive Up"
        else:
            signal_code, signal_desc = "[S9]", "Open Range Breakout Drive Up"
    else:
        if has_gap and gap < 0:
            signal_code, signal_desc = "[S2]", "Gap Down + Drive Down continuation"
        elif not vwap_above:
            signal_code, signal_desc = "[S6]", "VWAP Rejection Drive Down"
        else:
            signal_code, signal_desc = "[S10]", "Open Range Breakdown Drive Down"

    conf = base_conf
    if rvol >= 2.0: conf = min(95, conf + 8)
    if vwap_above == (direction == "LONG"): conf = min(95, conf + 8)
    if has_gap: conf = min(95, conf + 10)

    scorecard = "STRONG" if conf >= 80 else ("WATCH" if conf >= 65 else "CAUTION")

    if direction == "LONG":
        strike = round(price / 5 + 0.5) * 5
        option = f"CALL ${strike:.0f}"
    else:
        strike = round(price / 5 - 0.5) * 5
        option = f"PUT ${strike:.0f}"

    atr_m = re.search(r"ATR\s*[=:]?\s*([\d.]+)", text, re.IGNORECASE)
    atr = float(atr_m.group(1)) if atr_m else price * 0.02
    if direction == "LONG":
        stop = round(price - atr * 0.5, 2)
        t1   = round(price + atr * 1.5, 2)
        t2   = round(price + atr * 3.0, 2)
    else:
        stop = round(price + atr * 0.5, 2)
        t1   = round(price - atr * 1.5, 2)
        t2   = round(price - atr * 3.0, 2)

    return {
        "ticker": ticker, "direction": direction, "price": price,
        "rvol": rvol, "rsi": rsi, "gap": gap, "vwap_above": vwap_above,
        "signal_code": signal_code, "signal_desc": signal_desc,
        "scorecard": scorecard, "confidence": conf,
        "option": option, "entry": price, "stop": stop, "t1": t1, "t2": t2,
        "note": f"RVOL={rvol:.2f}x | {'above' if vwap_above else 'below'} VWAP | Gap={gap:+.1f}% | RSI={rsi:.1f}",
    }


def is_valid_od(alert: dict) -> bool:
    return alert["ticker"] in TICKERS and alert["confidence"] >= 60 and alert["rvol"] >= 0.8


# ── Message Building ──────────────────────────────────────────────────────────

def ticker_block(alert: dict, status: str) -> str:
    d = alert
    dir_str = "[UP] LONG" if d["direction"] == "LONG" else "[DN] SHORT"
    return "\n".join([
        "--------------------------------------",
        f"Ticker : {d['ticker']}  [{status}]",
        "--- OPENING DRIVE (Pre-Market Setup) ---",
        f"{d['signal_code']} {d['signal_desc']}",
        f"Scorecard  : {d['scorecard']}",
        f"Direction  : {dir_str}",
        f"Option     : {d['option']}",
        f"Confidence : {d['confidence']}%",
        f"Entry(stk) : ${d['entry']:.2f}  Stop(stk) : ${d['stop']:.2f}",
        f"Target 1   : ${d['t1']:.2f}  Target 2  : ${d['t2']:.2f}",
        f"Note       : {d['note']}",
        "--------------------------------------",
    ])


def ranking_score(a: dict) -> int:
    score = 0
    if a["rvol"] >= 2.0: score += 2
    if abs(a["gap"]) > 0.5: score += 2
    if a["vwap_above"] == (a["direction"] == "LONG"): score += 2
    if a["scorecard"] == "STRONG": score += 2
    score += max(0, int(a["confidence"] / 10) - 7)
    return score


def build_message(run_num: int, new_: dict, updated: dict, carried: dict, excluded: list) -> str:
    label, run_time, footer = RUN_META[run_num]
    today_str = datetime.now(ET).strftime("%B %d, %Y")
    total = len(new_) + len(updated) + len(carried)
    all_alerts = {**new_, **updated, **carried}

    lines = [
        f"*PRE-MARKET OPEN DRIVE SUMMARY*",
        f"{label} | {run_time}",
        f"{today_str}",
        f"====================================",
        f"Scanned      : {total + len(excluded)} tickers",
        f"OD Confirmed : {total}",
        f"New This Run : {len(new_)}",
        f"Updated      : {len(updated)}",
        f"Carried Fwd  : {len(carried)}",
        f"Filtered Out : {len(excluded)}",
        f"====================================",
    ]

    bullish = {t: a for t, a in all_alerts.items() if a["direction"] == "LONG"}
    bearish = {t: a for t, a in all_alerts.items() if a["direction"] == "SHORT"}

    if bullish:
        lines.append(f"\nBULLISH OPEN DRIVES ({len(bullish)})")
        for t, a in sorted(bullish.items()):
            status = "New" if t in new_ else ("Updated" if t in updated else "Confirmed")
            lines.append(ticker_block(a, status))

    if bearish:
        lines.append(f"\nBEARISH OPEN DRIVES ({len(bearish)})")
        for t, a in sorted(bearish.items()):
            status = "New" if t in new_ else ("Updated" if t in updated else "Confirmed")
            lines.append(ticker_block(a, status))

    if total == 0:
        lines.append(f"\nNo OD signals detected this run.")

    if all_alerts:
        ranked = sorted(all_alerts.values(), key=ranking_score, reverse=True)
        medals = ["1.", "2.", "3."]
        lines.append("\n====================================")
        lines.append("TOP OD PICKS THIS RUN")
        lines.append("------------------------------------")
        for i, a in enumerate(ranked[:3]):
            dir_e = "UP" if a["direction"] == "LONG" else "DN"
            lines.append(f"{medals[i]} {a['ticker']} - {dir_e} | {a['signal_code']} | Conf {a['confidence']}% | RVOL {a['rvol']:.2f}x")

    if excluded:
        lines.append("\n====================================")
        lines.append("EXCLUDED / WATCH LIST")
        lines.append("------------------------------------")
        for item in excluded:
            lines.append(f"{item['ticker']} - {item['reason']}")

    lines.append(f"\n====================================")
    lines.append(f"{footer}")
    lines.append(f"OD Aggregator | Dest: {DEST_CHAT_ID}")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=int, choices=[1, 2, 3], required=True,
                        help="Which scheduled run: 1=9:05, 2=9:20, 3=9:35")
    args = parser.parse_args()
    run_num = args.run

    now_et = datetime.now(ET)
    log.info("OD Aggregator RUN %d started at %s ET", run_num, now_et.strftime("%H:%M"))

    if now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 45):
        log.warning("Past 9:45 ET hard stop - aborting.")
        sys.exit(0)

    state = load_state()
    confirmed: dict = state.get("confirmed", {})
    last_update_id: int = state.get("last_update_id", 0)

    updates, last_update_id = tg_get_updates(last_update_id)
    log.info("Fetched %d Telegram updates", len(updates))

    current_window: dict = {}
    excluded: list = []

    for upd in updates:
        msg = upd.get("message") or upd.get("channel_post")
        if not msg:
            continue
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if SOURCE_CHAT_ID and chat_id != str(SOURCE_CHAT_ID):
            continue
        text = msg.get("text", "")
        if not text:
            continue
        alert = parse_od_alert(text)
        if alert is None:
            continue
        if not is_valid_od(alert):
            excluded.append({"ticker": alert["ticker"], "reason": f"conf={alert['confidence']}% rvol={alert['rvol']:.2f}x"})
            continue
        current_window[alert["ticker"]] = alert
        log.info("Collected: %s %s conf=%d%%", alert["ticker"], alert["direction"], alert["confidence"])

    new_: dict    = {}
    updated: dict = {}
    carried: dict = {}

    for ticker, alert in current_window.items():
        if ticker in confirmed:
            prev = confirmed[ticker]
            if prev["direction"] != alert["direction"] or prev["signal_code"] != alert["signal_code"]:
                updated[ticker] = alert
        else:
            new_[ticker] = alert

    for ticker, alert in confirmed.items():
        if ticker not in new_ and ticker not in updated:
            carried[ticker] = alert

    confirmed.update(new_)
    confirmed.update(updated)

    state["confirmed"] = confirmed
    state["last_update_id"] = last_update_id
    save_state(state)

    text = build_message(run_num, new_, updated, carried, excluded)
    log.info("Sending RUN %d summary to %s...", run_num, DEST_CHAT_ID)
    tg_send_split(text)
    log.info("RUN %d complete. Confirmed: %s", run_num, list(confirmed.keys()))


if __name__ == "__main__":
    main()
