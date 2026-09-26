# ============================================================
# ARGUS-Trader - NOTIFY v1 [PRODUCTION]
# ------------------------------------------------------------
# Отправляет BUY/SELL [strong] в Telegram.
# WAIT не отправляет (без спама).
# Anti-spam: не повторяет один сигнал 6ч.
# ============================================================

import os
import sys
import json
import logging
import requests
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.notify")

SIGNALS_FILE = SCRIPT_DIR / "last_signals.json"
STATE_FILE = SCRIPT_DIR / "notify_state.json"

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()

MIN_CONF = 0.30
RESEND_HOURS = 6


def load_json(path, default=None):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID:
        log.error("telegram not configured")
        return False
    url = (
        "https://api.telegram.org/bot"
        + BOT_TOKEN + "/sendMessage"
    )
    try:
        r = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code == 200:
            return True
        log.warning("tg %d: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.error("tg: %s", e)
    return False


def get_last_close(symbol):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT close FROM candles "
                    "WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (symbol,),
                )
                r = cur.fetchone()
                if r and r[0]:
                    return float(r[0])
    except Exception as e:
        log.error("get_close %s: %s", symbol, e)
    return None


def get_levels(symbol):
    data = load_json(
        DATA_DIR / "levels_analysis.json", {}
    )
    return data.get("symbols", {}).get(symbol, {})


def fmt_signal(sig, price, levels):
    action = sig["action"]
    sym = sig["symbol"]
    conf = sig.get("confidence", 0)
    prob = sig.get("prob_up", 0)

    short = sym.replace("USDT", "/USDT")

    if action == "BUY":
        emoji = "🟢"
    else:
        emoji = "🔴"

    L = []
    L.append(
        emoji + " <b>" + action + " " + short + "</b>"
    )
    L.append("")
    L.append(
        "Уверенность: "
        + str(round(conf * 100, 1)) + "%"
    )
    L.append(
        "P(up): " + str(round(prob * 100, 1)) + "%"
    )

    if price:
        L.append(
            "Цена: $" + format(price, ",.2f")
        )

    if action == "BUY" and price:
        sups = levels.get("supports", [])
        res = levels.get("resistances", [])

        below = [
            s for s in sups
            if s.get("price", 0) < price
        ]
        if below:
            ns = max(below, key=lambda x: x["price"])
            L.append(
                "Support: $"
                + format(ns["price"], ",.2f")
            )

        above = [
            r for r in res
            if r.get("price", 0) > price
        ]
        if above:
            nr = min(above, key=lambda x: x["price"])
            L.append(
                "Resistance: $"
                + format(nr["price"], ",.2f")
            )

    L.append("")
    L.append("Сумма: $10")
    L.append("Модель: молодая, edge +0.05")

    return "\n".join(L)


def main():
    log.info("=" * 50)
    log.info("ARGUS NOTIFY v1")
    log.info("=" * 50)

    if not BOT_TOKEN or not CHAT_ID:
        log.error("telegram not configured")
        return

    signals_data = load_json(SIGNALS_FILE, None)
    if not signals_data:
        log.error("no signals file")
        return

    state = load_json(STATE_FILE, {})
    now = datetime.now(timezone.utc)

    signals = signals_data.get("signals", [])
    log.info("signals in file: %d", len(signals))

    sent = 0
    skipped_wait = 0
    skipped_weak = 0
    skipped_dup = 0

    for sig in signals:
        symbol = sig["symbol"]
        action = sig.get("action", "WAIT")
        conf = sig.get("confidence", 0)

        if action == "WAIT":
            skipped_wait += 1
            continue
        if conf < MIN_CONF:
            skipped_weak += 1
            continue

        prev = state.get(symbol, {})
        prev_action = prev.get("action")
        prev_ts = prev.get("ts")

        if prev_action == action and prev_ts:
            try:
                prev_dt = datetime.fromisoformat(prev_ts)
                if prev_dt.tzinfo is None:
                    prev_dt = prev_dt.replace(
                        tzinfo=timezone.utc
                    )
                age_h = (
                    now - prev_dt
                ).total_seconds() / 3600
                if age_h < RESEND_HOURS:
                    log.info(
                        "%s: skip (sent %.1fh ago)",
                        symbol, age_h,
                    )
                    skipped_dup += 1
                    continue
            except Exception:
                pass

        price = get_last_close(symbol)
        levels = get_levels(symbol)

        text = fmt_signal(sig, price, levels)
        if send_tg(text):
            sent += 1
            state[symbol] = {
                "action": action,
                "ts": now.isoformat(),
                "conf": conf,
            }
            log.info(
                "%s: SENT %s (conf=%.2f)",
                symbol, action, conf,
            )

    save_json(STATE_FILE, state)

    log.info(
        "sent=%d wait=%d weak=%d dup=%d",
        sent, skipped_wait,
        skipped_weak, skipped_dup,
    )


if __name__ == "__main__":
    main()