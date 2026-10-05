# ============================================================
# ARGUS-Trader - NOTIFY v3 [PRODUCTION]
# ------------------------------------------------------------
# v3: get_last_close via symbol_conn — SOL/BNB from DB2.
#     v2 called get_connection() -> price "?" for SOL/BNB.
# v2: sanity for conf/prob_up in [0,1].
#     model_acc from signals. predicted_return_pct line.
#     state reset on model_version change.
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

for _p in CRYPTO_ROOT.rglob("db2.py"):
    _d = str(_p.parent)
    if "__pycache__" in _d:
        continue
    if _d not in sys.path:
        sys.path.insert(0, _d)
    break

from db import get_connection

DB2_OK = False
get_conn_db2 = None
if (os.getenv("ARGUS_DB_URL_2") or "").strip():
    try:
        from db2 import (
            get_connection as get_conn_db2,
        )
        _t = get_conn_db2()
        with _t as _c:
            with _c.cursor() as _cur:
                _cur.execute("SELECT 1")
                _cur.fetchone()
        DB2_OK = True
    except Exception as e:
        print("DB2 fail: " + str(e))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.notify")

DB2_SYMBOLS = {
    s.strip().upper()
    for s in (
        os.getenv("DB2_SYMBOLS")
        or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}

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


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        try:
            return get_conn_db2()
        except Exception as e:
            log.warning(
                "db2 conn %s: %s", symbol, e,
            )
    return get_connection()


def load_json(path, default=None):
    if not path.exists():
        return default
    try:
        with open(
            path, "r", encoding="utf-8"
        ) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(
        path, "w", encoding="utf-8"
    ) as f:
        json.dump(
            data, f,
            ensure_ascii=False, indent=2,
        )


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
        log.warning(
            "tg %d: %s",
            r.status_code, r.text[:200],
        )
    except Exception as e:
        log.error("tg: %s", e)
    return False


def get_last_close(symbol):
    try:
        with symbol_conn(symbol) as conn:
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
        log.error(
            "get_close %s: %s", symbol, e,
        )
    return None


def get_levels(symbol):
    data = load_json(
        DATA_DIR / "levels_analysis.json", {}
    )
    return data.get(
        "symbols", {}
    ).get(symbol, {})


def _fmt_model_line(sig, signals_data):
    acc = signals_data.get("model_accuracy")
    version = sig.get("model_version")
    parts = []
    if version:
        parts.append(version)
    if acc is not None:
        parts.append("acc=" + str(acc))
    if not parts:
        return ""
    return "Модель: " + ", ".join(parts)


def _fmt_pred_line(sig):
    v = sig.get("predicted_return_pct")
    if v is None:
        return None
    sign = "+" if v >= 0 else ""
    return (
        "Прогноз: " + sign
        + str(round(v, 3)) + "%"
    )


def fmt_signal(sig, price, levels, signals_data):
    action = sig["action"]
    sym = sig["symbol"]
    conf = float(sig.get("confidence", 0) or 0)
    prob = float(sig.get("prob_up", 0.5) or 0.5)

    if conf < 0:
        conf = 0.0
    if conf > 1:
        conf = 1.0
    if prob < 0:
        prob = 0.0
    if prob > 1:
        prob = 1.0

    short = sym.replace("USDT", "/USDT")

    emoji = "🟢" if action == "BUY" else "🔴"

    L = []
    L.append(
        emoji + " <b>" + action + " "
        + short + "</b>"
    )
    L.append("")
    L.append(
        "Уверенность: "
        + str(round(conf * 100, 1)) + "%"
    )
    L.append(
        "P(up): " + str(round(prob * 100, 1))
        + "%"
    )

    pred_line = _fmt_pred_line(sig)
    if pred_line:
        L.append(pred_line)

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
            ns = max(
                below, key=lambda x: x["price"]
            )
            L.append(
                "Support: $"
                + format(ns["price"], ",.2f")
            )

        above = [
            r for r in res
            if r.get("price", 0) > price
        ]
        if above:
            nr = min(
                above, key=lambda x: x["price"]
            )
            L.append(
                "Resistance: $"
                + format(nr["price"], ",.2f")
            )

    model_line = _fmt_model_line(sig, signals_data)
    if model_line:
        L.append("")
        L.append(model_line)

    return "\n".join(L)


def _state_key(symbol, sig):
    v = sig.get("model_version") or "?"
    return symbol + "|" + str(v)


def main():
    log.info("=" * 50)
    log.info("ARGUS NOTIFY v3")
    log.info(
        "DB2_SYMBOLS=%s (DB2_OK=%s)",
        sorted(DB2_SYMBOLS), DB2_OK,
    )
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
    log.info(
        "signals in file: %d", len(signals)
    )

    sent = 0
    skipped_wait = 0
    skipped_weak = 0
    skipped_dup = 0
    skipped_bad = 0

    for sig in signals:
        symbol = sig.get("symbol")
        action = sig.get("action", "WAIT")
        conf = float(sig.get("confidence", 0) or 0)

        if not symbol:
            skipped_bad += 1
            continue

        if action == "WAIT":
            skipped_wait += 1
            continue
        if conf < MIN_CONF:
            skipped_weak += 1
            continue
        if conf > 1.0:
            log.warning(
                "%s: bad conf=%.4f -> skip",
                symbol, conf,
            )
            skipped_bad += 1
            continue

        key = _state_key(symbol, sig)
        prev = state.get(key, {})
        prev_action = prev.get("action")
        prev_ts = prev.get("ts")

        if prev_action == action and prev_ts:
            try:
                prev_dt = datetime.fromisoformat(
                    prev_ts
                )
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

        text = fmt_signal(
            sig, price, levels, signals_data,
        )
        if send_tg(text):
            sent += 1
            state[key] = {
                "action": action,
                "ts": now.isoformat(),
                "conf": conf,
                "model_version": sig.get(
                    "model_version"
                ),
            }
            log.info(
                "%s: SENT %s (conf=%.2f)",
                symbol, action, conf,
            )

    save_json(STATE_FILE, state)

    log.info(
        "sent=%d wait=%d weak=%d dup=%d bad=%d",
        sent, skipped_wait,
        skipped_weak, skipped_dup,
        skipped_bad,
    )


if __name__ == "__main__":
    main()