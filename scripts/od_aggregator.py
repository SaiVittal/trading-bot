"""
OD Aggregator Bot - Single-Run Mode
=====================================
Runs pre-market signals, parses OD=N from stdout,
reconstructs OD details from available data,
sends merged summary to OD destination chat.

Usage:
    python scripts/od_aggregator.py --run 1   # 9:05 ET
    python scripts/od_aggregator.py --run 2   # 9:20 ET
    python scripts/od_aggregator.py --run 3   # 9:35 ET FINAL
"""

import os, sys, json, re, time, logging, argparse, subprocess, requests
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "8752800861:AAGUp376nhu0E-PoFhuKmx9-x572qUO95kw")
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
    "GOOGL","IBM","ORCL","DELL","SNOW","COST","CRWD","CRM","ZM","ROSS","OSIS",
}

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
    log.info("Running: %s", SIGNALS_PS1)
    result = subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(SIGNALS_PS1)],
        capture_output=True, text=True, timeout=180
    )
    output = result.stdout + result.stderr
    log.info("Done. Output: %d chars", len(output))
    return output

# ── OD Signal Parser ──────────────────────────────────────────────────────────

def parse_od_signals(output: str) -> list[dict]:
    """
    Parse run_signals stdout. Finds ticker blocks where OD >= 1,
    extracts available fields and reconstructs OD alert dict.
    stdout format per ticker:
        --- TICKER ---
          Price=X  RSI=X  RSI1m=X  EMA9=X  ATR=X
          RVOL=Xx  VWAP=X  BBW=X  VolSpike=X  Trend=X
          Daily: dRSI=X  dEMA9=X  dTrend=X
          Sigs: OD=N 0DTE=N SW=N SC=N MO=N  TopConf=X%  Consensus=X  Dir=LONG/SHORT
    """
    alerts = []

    # Split into per-ticker blocks
    blocks = re.split(r"\n--- ([A-Z]{1,5}) ---\n", output)

    i = 1
    while i < len(blocks) - 1:
        ticker = blocks[i].strip()
        block  = blocks[i + 1]
        i += 2

        if ticker not in TICKERS:
            continue

        # Only process if OD signal fired
        od_m = re.search(r"Sigs:.*?OD=(\d+)", block)
        if not od_m or int(od_m.group(1)) == 0:
            continue

        # Extract fields from stdout
        def get(pattern, cast=float, default=0.0):
            m = re.search(pattern, block)
            try:
                return cast(m.group(1)) if m else default
            except Exception:
                return default

        price     = get(r"Price=([\d.]+)")
        atr       = get(r"ATR=([\d.]+)")
        rvol      = get(r"RVOL=([\d.]+)x")
        rsi       = get(r"RSI=([\d.]+)")
        vwap      = get(r"VWAP=([\d.]+)")
        confidence= get(r"TopConf=(\d+)%", int, 65)
        direction = (re.search(r"Dir=(LONG|SHORT)", block) or type('', (), {'group': lambda s,x: 'LONG'})()).group(1)
        trend     = (re.search(r"Trend=(UP|DOWN|NEUTRAL)", block) or type('', (), {'group': lambda s,x: 'NEUTRAL'})()).group(1)

        if price == 0.0 or atr == 0.0:
            log.warning("Skipping %s — missing price/ATR", ticker)
            continue

        # Reconstruct entry/stop/targets from ATR
        if direction == "LONG":
            stop = round(price - atr * 0.5, 2)
            t1   = round(price + atr * 1.5, 2)
            t2   = round(price + atr * 3.0, 2)
            strike = int(round(price / 5 + 0.5) * 5)
            option = f"CALL ${strike}"
        else:
            stop = round(price + atr * 0.5, 2)
            t1   = round(price - atr * 1.5, 2)
            t2   = round(price - atr * 3.0, 2)
            strike = int(round(price / 5 - 0.5) * 5)
            option = f"PUT ${strike}"

        # Scorecard from confidence
        if confidence >= 85:
            scorecard = "BUY"
        elif confidence >= 75:
            scorecard = "STRONG"
        elif confidence >= 65:
            scorecard = "WATCH"
        else:
            scorecard = "CAUTION"

        vwap_pos = "above" if price > vwap else "below"
        note = f"RVOL={rvol:.2f}x | {vwap_pos} VWAP=${vwap:.2f} | RSI={rsi:.1f} | Trend={trend}"

        alerts.append({
            "ticker": ticker, "direction": direction,
            "signal_code": "[OD]", "signal_desc": "Opening Drive Signal",
            "scorecard": scorecard, "confidence": confidence,
            "option": option, "entry": price,
            "stop": stop, "t1": t1, "t2": t2,
            "note": note, "rvol": rvol,
        })
        log.info("Captured OD: %s %s conf=%d%% RVOL=%.2fx", ticker, direction, confidence, rvol)

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
    if a["scorecard"] == "BUY": score += 4
    elif a["scorecard"] == "STRONG": score += 3
    elif a["scorecard"] == "WATCH": score += 1
    score += max(0, int(a["confidence"] / 10) - 7)
    return score

def build_message(run_num: int, new_: dict, updated: dict, carried: dict) -> str:
    label, run_time, footer = RUN_META[run_num]
    today_str  = datetime.now(ET).strftime("%B %d, %Y")
    total      = len(new_) + len(updated) + len(carried)
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
            lines.append(f"{medals[i]} {a['ticker']} - {dir_e} | {a['scorecard']} | Conf {a['confidence']}% | RVOL {a['rvol']:.2f}x")

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
    log.info("OD Aggregator RUN %d at %s ET", run_num, now_et.strftime("%H:%M"))

    if now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 45):
        log.warning("Past 9:45 ET hard stop - aborting.")
        sys.exit(0)

    output  = run_signals()
    current = parse_od_signals(output)
    log.info("Found %d OD signals", len(current))

    state     = load_state()
    confirmed = state.get("confirmed", {})
    new_: dict    = {}
    updated: dict = {}
    carried: dict = {}

    for alert in current:
        t = alert["ticker"]
        if t in confirmed:
            prev = confirmed[t]
            if prev["direction"] != alert["direction"]:
                updated[t] = alert
        else:
            new_[t] = alert

    for t, alert in confirmed.items():
        if t not in new_ and t not in updated:
            carried[t] = alert

    confirmed.update(new_)
    confirmed.update(updated)
    state["confirmed"] = confirmed
    save_state(state)

    text = build_message(run_num, new_, updated, carried)
    tg_send_split(text)
    log.info("RUN %d sent. Confirmed: %s", run_num, list(confirmed.keys()))

if __name__ == "__main__":
    main()
