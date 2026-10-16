# ============================================================
# ARGUS - MORNING REPORT v15.4
# ------------------------------------------------------------
# Только BTC. Только Aiven DB. Только таблица candles.
# Статус коллектора определяется по свежести последней свечи.
# ============================================================

import os
import sys
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

try:
    import psycopg
except ImportError:
    print("ERR: psycopg not installed")
    sys.exit(1)

TZ = ZoneInfo("Europe/Kaliningrad")

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

SYMBOL = "BTCUSDT"
NAME = "BTC"
TIMEFRAME = "15m"


def now_local():
    return datetime.now(timezone.utc).astimezone(TZ)


def fmt_price(p):
    if p is None:
        return "?"
    if p >= 1000:
        return "$" + format(int(p), ",")
    if p >= 1:
        return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")


def fmt_age(mins):
    if mins is None:
        return "?"
    if mins < 60:
        return str(int(mins)) + " мин"
    if mins < 1440:
        return str(int(mins // 60)) + " ч"
    return str(int(mins // 1440)) + " д"


def _to_local(ts):
    if ts is None:
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(TZ).strftime("%d.%m %H:%M")


def get_conn():
    if not DB_URL:
        raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(
        DB_URL, connect_timeout=15,
        application_name="argus-morning",
    )


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return [None] * len(closes)
    out = [None] * period
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    rs = avg_gain / avg_loss if avg_loss > 0 else 999
    out.append(100 - 100 / (1 + rs))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0
        loss = -d if d < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 999
        out.append(100 - 100 / (1 + rs))
    return out


def compute_atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr


def fetch_candles(symbol, limit=200):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT open_time, open, high, low, close, volume "
                    "FROM candles WHERE symbol=%s "
                    "AND timeframe=%s AND market_type='spot' "
                    "ORDER BY open_time DESC LIMIT %s",
                    (symbol, TIMEFRAME, limit),
                )
                rows = list(reversed(cur.fetchall()))
                return [
                    {
                        "timestamp": r[0],
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                    }
                    for r in rows
                ]
    except Exception as e:
        print("candles: " + str(e))
        return []


def fetch_stats(symbol):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM candles WHERE symbol=%s",
                    (symbol,),
                )
                total = cur.fetchone()[0] or 0

                cur.execute(
                    "SELECT market_type, COUNT(*) FROM candles "
                    "WHERE symbol=%s GROUP BY market_type",
                    (symbol,),
                )
                by_market = dict(cur.fetchall())

                cur.execute(
                    "SELECT MAX(open_time) FROM candles WHERE symbol=%s",
                    (symbol,),
                )
                last_ts = cur.fetchone()[0]

                cur.execute(
                    "SELECT COUNT(*) FROM candles WHERE symbol=%s "
                    "AND open_time > NOW() - INTERVAL '24 hours'",
                    (symbol,),
                )
                rows_24h = cur.fetchone()[0] or 0

                cur.execute(
                    "SELECT COUNT(*) FROM candles WHERE symbol=%s "
                    "AND (open IS NULL OR high IS NULL OR low IS NULL "
                    "OR close IS NULL OR volume IS NULL)",
                    (symbol,),
                )
                nulls = cur.fetchone()[0] or 0

        return {
            "total": total,
            "by_market": by_market,
            "last_ts": last_ts,
            "rows_24h": rows_24h,
            "nulls": nulls,
        }
    except Exception as e:
        print("stats: " + str(e))
        return None


def fmt_collector_block(stats):
    lines = ["📡 <b>Сбор данных</b>"]

    if stats is None:
        lines.append("  ❌ DB недоступна")
        return lines

    last_ts = stats["last_ts"]

    # Статус по свежести последней свечи
    if last_ts is None:
        lines.append("  Статус: ❌ нет данных")
    else:
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        age_min = int(
            (datetime.now(timezone.utc) - last_ts)
            .total_seconds() / 60
        )
        if age_min <= 30:
            lines.append("  Статус: 🟢 ON")
        elif age_min <= 120:
            lines.append("  Статус: ⚠️ задержка")
        else:
            lines.append("  Статус: 🔴 OFF")

    lines.append("  Свечей всего: " + str(stats["total"]))

    if stats["by_market"]:
        parts = []
        for mt, cnt in sorted(stats["by_market"].items()):
            parts.append(str(mt) + " " + str(cnt))
        lines.append("  " + " | ".join(parts))

    lines.append("  За 24ч: " + str(stats["rows_24h"]))

    if last_ts:
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        age_min = int(
            (datetime.now(timezone.utc) - last_ts)
            .total_seconds() / 60
        )
        line = "  Последняя: " + _to_local(last_ts) + " КЛГ"
        line += " (" + fmt_age(age_min) + " назад)"
        lines.append(line)

        if age_min <= 30:
            lines.append("  ✅ Данные свежие")
        elif age_min <= 120:
            lines.append("  ⚠️ Данные с задержкой")
        else:
            lines.append("  ❌ Данные устарели!")

    if stats["nulls"] == 0:
        lines.append("  ✅ Пустых полей нет")
    else:
        lines.append("  ⚠️ Пустых полей: " + str(stats["nulls"]))

    return lines


def fmt_btc_block(candles):
    lines = ["💰 <b>" + NAME + "</b>"]

    if not candles:
        lines.append("  нет данных")
        return lines

    price = candles[-1]["close"]

    change_24h = 0
    if len(candles) >= 96:
        prev = candles[-96]["close"]
        if prev:
            change_24h = (price - prev) / prev * 100

    line = "  " + fmt_price(price)
    line += " | " + format(change_24h, "+.2f") + "% 24ч"
    lines.append(line)

    atr = compute_atr(candles, 14)
    closes = [c["close"] for c in candles]
    rsi = compute_rsi(closes, 14)

    line = "  "
    if atr:
        line += "ATR " + fmt_price(atr)
    if rsi and rsi[-1] is not None:
        r = rsi[-1]
        if r >= 70:
            st = "перекуп"
        elif r <= 30:
            st = "перепрод"
        else:
            st = "нейтр"
        line += " | RSI " + format(r, ".1f") + " " + st
    lines.append(line)

    return lines


def split_text(text, max_len=3800):
    if len(text) <= max_len:
        return [text]
    parts = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current:
                parts.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        parts.append(current)
    return parts


def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("no token/chat")
        return False
    for part in split_text(text, 3800):
        try:
            url = (
                "https://api.telegram.org/bot"
                + BOT_TOKEN + "/sendMessage"
            )
            r = requests.post(url, json={
                "chat_id": CHAT_ID,
                "text": part,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, timeout=20)
            if r.status_code != 200:
                print("send err " + str(r.status_code))
        except Exception as e:
            print("send: " + str(e))
            return False
    return True


def build_report_text():
    now = now_local()
    lines = []
    lines.append("☀️ <b>ARGUS — утро</b>")
    lines.append(now.strftime("%d.%m.%Y %H:%M") + " КЛГ")
    lines.append("")

    stats = fetch_stats(SYMBOL)
    lines.extend(fmt_collector_block(stats))
    lines.append("")
    lines.append("─" * 20)
    lines.append("")

    candles = fetch_candles(SYMBOL, 200)
    lines.extend(fmt_btc_block(candles))

    return "\n".join(lines)


def main():
    print("Morning report v15.4 - start")
    text = build_report_text()
    print("len: " + str(len(text)))
    send_message(text)
    print("done")


if __name__ == "__main__":
    main()