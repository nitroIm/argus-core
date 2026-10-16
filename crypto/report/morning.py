# ============================================================
# ARGUS - MORNING REPORT v15.5
# ------------------------------------------------------------
# Только BTC. Только Aiven DB. Только таблица candles.
# Полный обзор за 24 часа: цена, объёмы, волатильность,
# индикаторы, структура свечей.
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
H24 = 96  # свечей 15m за 24 часа


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


def fmt_usd(v):
    if v is None:
        return "?"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1_000_000_000:
        return sign + "$" + format(a / 1e9, ".2f") + "B"
    if a >= 1_000_000:
        return sign + "$" + format(a / 1e6, ".2f") + "M"
    if a >= 1_000:
        return sign + "$" + format(a / 1e3, ".1f") + "K"
    return sign + "$" + format(a, ".2f")


def fmt_btc(v):
    if v is None:
        return "?"
    if v >= 1000:
        return format(v, ",.1f") + " BTC"
    if v >= 1:
        return format(v, ".2f") + " BTC"
    return format(v, ".4f") + " BTC"


def fmt_pct(v):
    if v is None:
        return "?"
    return format(v, "+.2f") + "%"


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


# ============================================================
# INDICATORS
# ============================================================
def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
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
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0
        loss = -d if d < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    a = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
    return a


# ============================================================
# DB
# ============================================================
def fetch_candles(symbol, limit=300):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT open_time, open, high, low, close, "
                    "volume, taker_buy_volume, quote_volume, count "
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
                        "volume": float(r[5]) if r[5] is not None else 0.0,
                        "taker_buy_volume": (
                            float(r[6]) if r[6] is not None else 0.0
                        ),
                        "quote_volume": (
                            float(r[7]) if r[7] is not None else 0.0
                        ),
                        "count": int(r[8]) if r[8] is not None else 0,
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

        return {
            "total": total,
            "by_market": by_market,
            "last_ts": last_ts,
            "rows_24h": rows_24h,
        }
    except Exception as e:
        print("stats: " + str(e))
        return None


# ============================================================
# BLOCKS
# ============================================================
def fmt_collector_block(stats):
    lines = ["📡 <b>Сбор данных</b>"]

    if stats is None:
        lines.append("  ❌ DB недоступна")
        return lines

    last_ts = stats["last_ts"]

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

    return lines


def fmt_btc_block(candles):
    lines = []
    if not candles:
        lines.append("💰 <b>" + NAME + "</b>")
        lines.append("  нет данных")
        return lines

    # Последние 24ч
    last24 = candles[-H24:] if len(candles) >= H24 else candles
    first = last24[0]
    last = last24[-1]

    price = last["close"]
    price_open = first["open"]
    change_usd = price - price_open
    change_pct = (change_usd / price_open * 100) if price_open else 0

    hi = max(c["high"] for c in last24)
    lo = min(c["low"] for c in last24)
    hi_pct = (hi - price_open) / price_open * 100 if price_open else 0
    lo_pct = (lo - price_open) / price_open * 100 if price_open else 0

    # Объёмы
    vol_btc = sum(c["volume"] for c in last24)
    vol_usd = sum(c["quote_volume"] for c in last24)
    taker_buy = sum(c["taker_buy_volume"] for c in last24)
    taker_sell = vol_btc - taker_buy
    buy_pct = (taker_buy / vol_btc * 100) if vol_btc else 0
    sell_pct = (taker_sell / vol_btc * 100) if vol_btc else 0
    trades = sum(c["count"] for c in last24)
    avg_trade_usd = (vol_usd / trades) if trades else 0
    avg_trade_btc = (vol_btc / trades) if trades else 0

    # Структура свечей
    green = sum(1 for c in last24 if c["close"] >= c["open"])
    red = len(last24) - green

    best = max(last24, key=lambda c: c["high"] - c["open"])
    best_pct = (best["high"] - best["open"]) / best["open"] * 100

    worst = min(last24, key=lambda c: c["low"] - c["open"])
    worst_pct = (worst["low"] - worst["open"]) / worst["open"] * 100

    # ATR (в $ и в %)
    a = atr(candles, 14)
    a_pct = (a / price * 100) if a and price else 0

    # RSI 15m и 1h
    closes15 = [c["close"] for c in candles]
    r15 = rsi(closes15, 14)

    candles_1h = []
    for i in range(0, len(candles) - 3, 4):
        chunk = candles[i:i + 4]
        candles_1h.append({
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
        })
    closes1h = [c["close"] for c in candles_1h]
    r1h = rsi(closes1h, 14)

    # EMA (15m)
    e9 = ema(closes15, 9)
    e21 = ema(closes15, 21)
    e50 = ema(closes15, 50)

    # ============================================================
    # ЦЕНА
    # ============================================================
    lines.append("💰 <b>" + NAME + " (spot)</b>")
    line = "  " + fmt_price(price)
    line += " | " + fmt_pct(change_pct)
    line += " (" + ("+" if change_usd >= 0 else "")
    line += fmt_price(abs(change_usd)) + ")"
    lines.append(line)

    lines.append(
        "  Открытие 24ч: " + fmt_price(price_open)
    )
    lines.append(
        "  Максимум 24ч: " + fmt_price(hi)
        + " (" + fmt_pct(hi_pct) + ")"
    )
    lines.append(
        "  Минимум 24ч: " + fmt_price(lo)
        + " (" + fmt_pct(lo_pct) + ")"
    )
    lines.append("")

    # ============================================================
    # ОБЪЁМЫ
    # ============================================================
    lines.append("📊 <b>Объёмы за 24ч</b>")
    lines.append(
        "  Объём: " + fmt_btc(vol_btc)
        + " | " + fmt_usd(vol_usd)
    )

    bar_len = 16
    b = int(round(buy_pct / 100 * bar_len))
    s = bar_len - b
    bar = "🟢" * b + "🔴" * s

    lines.append(
        "  Покупки тейкеров: " + fmt_btc(taker_buy)
        + " (" + format(buy_pct, ".1f") + "%)"
    )
    lines.append(
        "  Продажи тейкеров: " + fmt_btc(taker_sell)
        + " (" + format(sell_pct, ".1f") + "%)"
    )
    lines.append("  " + bar)

    lines.append("  Сделок: " + format(trades, ","))
    lines.append(
        "  Средняя сделка: " + fmt_usd(avg_trade_usd)
        + " | " + fmt_btc(avg_trade_btc)
    )
    lines.append("")

    # ============================================================
    # ИНДИКАТОРЫ
    # ============================================================
    lines.append("📈 <b>Индикаторы</b>")

    if a:
        lines.append(
            "  ATR(14) 15m: " + fmt_price(a)
            + " (" + format(a_pct, ".2f") + "%)"
        )

    if r15 is not None:
        st15 = (
            "перекуп" if r15 >= 70
            else "перепрод" if r15 <= 30
            else "нейтр"
        )
        lines.append(
            "  RSI(14) 15m: " + format(r15, ".1f") + " " + st15
        )

    if r1h is not None:
        st1h = (
            "перекуп" if r1h >= 70
            else "перепрод" if r1h <= 30
            else "нейтр"
        )
        lines.append(
            "  RSI(14) 1h:  " + format(r1h, ".1f") + " " + st1h
        )

    if e9:
        pos9 = "выше" if price >= e9 else "ниже"
        lines.append(
            "  EMA(9)  15m: " + fmt_price(e9)
            + " (цена " + pos9 + ")"
        )
    if e21:
        pos21 = "выше" if price >= e21 else "ниже"
        lines.append(
            "  EMA(21) 15m: " + fmt_price(e21)
            + " (цена " + pos21 + ")"
        )
    if e50:
        pos50 = "выше" if price >= e50 else "ниже"
        lines.append(
            "  EMA(50) 15m: " + fmt_price(e50)
            + " (цена " + pos50 + ")"
        )
    lines.append("")

    # ============================================================
    # СТРУКТУРА
    # ============================================================
    lines.append("🕯️ <b>Структура 24ч</b>")
    lines.append(
        "  Свечей: " + str(len(last24))
        + " (🟢 " + str(green) + " / 🔴 " + str(red) + ")"
    )
    lines.append(
        "  Макс. рост:   " + fmt_pct(best_pct)
        + " в " + _to_local(best["timestamp"])
    )
    lines.append(
        "  Макс. падение: " + fmt_pct(worst_pct)
        + " в " + _to_local(worst["timestamp"])
    )

    return lines


# ============================================================
# SEND
# ============================================================
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

    candles = fetch_candles(SYMBOL, 300)
    lines.extend(fmt_btc_block(candles))

    return "\n".join(lines)


def main():
    print("Morning report v15.5 - start")
    text = build_report_text()
    print("len: " + str(len(text)))
    send_message(text)
    print("done")


if __name__ == "__main__":
    main()