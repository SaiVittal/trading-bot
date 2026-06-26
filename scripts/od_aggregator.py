"""
OD Aggregator Bot - Single-Run Mode
=====================================
Runs pre-market signals, parses OD sections from stdout,
builds merged summary, sends to OD destination chat.

Usage:
    python scripts/od_aggregator.py --run 1   # 9:05 ET
    python scripts/od_aggregator.py --run 2   # 9:20 ET
    python scripts/od_aggregator.py --run 3   # 9:35 ET FINAL
"""

import os
import sys
import json
import re
import time
import logging
import argparse
import subprocess
import requests
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
DEST_CHAT_ID = os.environ.get("OD_DEST_CHAT_ID", "-5568744831")
TG_API       = f"https://api.telegram.org/bot{BOT_TOKEN}"
ET           = ZoneInfo("America/New_York")

REPO_ROOT    = Path(__file__).resolve().parent.parent
STATE_DIR    = REPO_ROOT / "state"
STATE_FILE   = STATE_DIR / f"od_state_{date.today()}.json"
SIGNALS_PS1  = REPO_ROOT / "scripts" / "run_signals_now.ps1"

RUN_META = {
    1: ("RUN 1/3", "9:05 ET - Early Pre-Market",  "Next Update: 9:20 ET"),
    2: ("RUN 2/3", "9:20 ET - Mid Pre-Market",     "Next Update: 9:35 ET (Final)"),
    3: ("RUN 3/3", "9:35 ET FINAL Pre-Market",     "Market opens in ~5 mins - trade ready!"),
}

TICKERS = {
    "APP","TSLA","NVDA","QQQ","SPY","META","MSFT","AMZN",
    "AAPL","INTC","NOW","HOOD","PLTR","NFLX","NBIS","RKLB","AMD","IREN",
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("od_aggregator")

# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    STATE_DIR.mkdir(exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"confirmed": {}}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── Telegram ──────────────────────────────────────────────────────────────────

def tg_send(text: str, retries: int = 3) -> bool:
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(
                f"{TG_API}/sendMessage",
                json={"chat_id": DEST_CHAT_ID, "text": text,
                      "parse_mode": "Markdown", "disable_web_page_preview": True},
                timeout=15,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            log.warning("Send attempt %d failed: %s", attempt, e)
            if attempt < retries:
                time.sleep(30)
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

# ── Signal Runner ─────────────────────────────────────────────────────────────

def run_signals() -> str:
    """Run the pre-market signals PS1 and return stdout."""
    log.info("Running signals script: %s", SIGNALS_PS1)
    result = subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(SIGNALS_PS1)],
        capture_output=True, text=True, timeout=120
    )
    output = result.stdout + result.stderr
    log.info("Signals script complete. Output length: %d chars", len(output))
    return output

# ── OD Signal Parser ──────────────────────────────────────────────────────────

def parse_od_signals(output: str) -> list[dict]:
    """
    Parse run_signals.ps1 stdout and extract OD signal blocks per ticker.
    Returns list of alert dicts for tickers that have OD signals.
    """
    alerts = []

    # Split output into per-ticker blocks by "--- TICKER ---" headers
    ticker_blocks = re.split(r"\n--- ([A-Z]{1,5}) ---\n", output)

    # ticker_blocks = [pre, TICKER1, block1, TICKER2, block2, ...]
    i = 1
    while i < len(ticker_blocks) - 1:
        ticker = ticker_blocks[i].strip()
        block  = ticker_blocks[i + 1]
        i += 2

        if ticker not in TICKERS:
            continue

        # Must have OD signal
        if "OPENING DRIVE" not in block:
            continue

        od_section_m = re.search(
            r"--- OPENING DRIVE.*?---\n(.*?)(?=\n---|\Z)",
            block, re.DOTALL
        )
        if not od_section_m:
            continue
        od_section = od_section_m.group(1)

        # Signal code + description
        sig_m = re.search(r"\[(S\d+)\]\s*(.+)", od_section)
        if not sig_m:
            continue
        signal_code = f"[{sig_m.group(1)}]"
        signal_desc = sig_m.group(2).strip()

        # Scorecard
        sc_m = re.search(r"Scorecard\s*:\s*\[(\w+)\]", od_section)
        scorecard = sc_m.group(1) if sc_m else "WATCH"

        # Direction
        dir_m = re.search(r"Direction\s*:\s*\[(\w+)\]\s*(\w+)", od_section)
        direction = dir_m.group(2) if dir_m else "LONG"

        # Option
        opt_m = re.search(r"Option\s*:\s*(.+)", od_section)
        option = opt_m.group(1).strip() if opt_m else ""

        # Confidence
        conf_m = re.search(r"Confidence\s*:\s*(\d+)%", od_section)
        confidence = int(conf_m.group(1)) if conf_m else 65

        # Entry / Stop
        entry_m = re.search(r"Entry\(stk\)\s*:\s*\$([\d.]+)", od_section)
        stop_m  = re.search(r"Stop\(stk\)\s*:\s*\$([\d.]+)", od_section)
        entry = float(entry_m.group(1)) if entry_m else 0.0
        stop  = float(stop_m.group(1))  if stop_m  else 0.0

        # Targets
        t1_m = re.search(r"Target 1\s*:\s*\$([\d.]+)", od_section)
        t2_m = re.search(r"Target 2\s*:\s*\$([\d.]+)", od_section)
        t1 = float(t1_m.group(1)) if t1_m else 0.0
        t2 = float(t2_m.group(1)) if t2_m else 0.0

        # Note
        note_m = re.search(r"Note\s*:\s*(.+)", od_section)
        note = note_m.group(1).strip() if note_m else ""

        # RVOL from main block
        rvol_m = re.search(r"RVOL\s*:\s*([\d.]+)x", block)
        rvol = float(rvol_m.group(1)) if rvol_m else 1.0

        alerts.append({
            "ticker": ticker, "direction": direction,
            "signal_code": signal_code, "signal_desc": signal_desc,
            "scorecard": scorecard, "confidence": confidence,
            "option": option, "entry": entry, "stop": stop,
            "t1": t1, "t2": t2, "note": note, "rvol": rvol,
        })
        log.info("Parsed OD signal: %s %s %s conf=%d%%", ticker, signal_code, direction, confidence)

    return alerts

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
    if a["scorecard"] == "STRONG": score += 3
    elif a["scorecard"] == "WATCH": score += 1
    score += max(0, int(a["confidence"] / 10) - 7)
    return score


def build_message(run_num: int, new_: dict, updated: dict, carried: dict) -> str:
    label, run_time, footer = RUN_META[run_num]
    today_str = datetime.now(ET).strftime("%B %d, %Y")
    total     = len(new_) + len(updated) + len(carried)
    all_alerts = {**new_, **updated, **carried}

    lines = [
        f"*PRE-MARKET OPEN DRIVE SUMMARY*",
        f"{label} | {run_time}",
        f"{today_str}",
        f"====================================",
        f"OD Confirmed : {total}",
        f"New This Run : {len(new_)}",
        f"Updated      : {len(updated)}",
        f"Carried Fwd  : {len(carried)}",
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
            lines.append(f"{medals[i]} {a['ticker']} - {dir_e} | {a['signal_code']} | {a['scorecard']} | Conf {a['confidence']}% | RVOL {a['rvol']:.2f}x")

    lines.append(f"\n====================================")
    lines.append(f"{footer}")
    lines.append(f"OD Aggregator | {DEST_CHAT_ID}")
    return "\n".join(lines)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=int, choices=[1, 2, 3], required=True)
    args = parser.parse_args()
    run_num = args.run

    now_et = datetime.now(ET)
    log.info("OD Aggregator RUN %d started at %s ET", run_num, now_et.strftime("%H:%M"))

    if now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 45):
        log.warning("Past 9:45 ET hard stop - aborting.")
        sys.exit(0)

    # Run signals and parse OD alerts from output
    output = run_signals()
    current_alerts = parse_od_signals(output)
    log.info("Found %d OD signals in output", len(current_alerts))

    # Load cumulative state
    state     = load_state()
    confirmed = state.get("confirmed", {})

    new_: dict    = {}
    updated: dict = {}
    carried: dict = {}

    for alert in current_alerts:
        ticker = alert["ticker"]
        if ticker in confirmed:
            prev = confirmed[ticker]
            if prev["signal_code"] != alert["signal_code"] or prev["direction"] != alert["direction"]:
                updated[ticker] = alert
            # same signal → will appear as carried
        else:
            new_[ticker] = alert

    for ticker, alert in confirmed.items():
        if ticker not in new_ and ticker not in updated:
            carried[ticker] = alert

    confirmed.update(new_)
    confirmed.update(updated)
    state["confirmed"] = confirmed
    save_state(state)

    text = build_message(run_num, new_, updated, carried)
    log.info("Sending RUN %d to %s...", run_num, DEST_CHAT_ID)
    tg_send_split(text)
    log.info("RUN %d done. Confirmed: %s", run_num, list(confirmed.keys()))


if __name__ == "__main__":
    main()
