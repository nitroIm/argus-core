# ============================================================
# ARGUS - EVENING REPORT v17.1
# ------------------------------------------------------------
# Вечерний отчёт. Период 08:00 → 20:00 КЛГ.
# Только BTC. Только Aiven DB. Только candles.
# Только текст. Без графиков.
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
DAY_START_H = 8
DAY_END_H = 20


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


def fmt_signed_usd(v):
    if v is None:
        return "?"
    sign = "+" if v >= 0 else "-"
    return sign + "$" + format(abs(v), ",.2f")


def fmt_age(mins):
    if mins is None:
        return "?"
    if mins < 60:
        return str(int(mins)) + " мин"
    if mins < 1440:
        return str(int(mins // 60)) + " ч"
    return str(int(mins // 1440)) + " д"


def _to_dt(ts):
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _to_local(ts):
    ts = _to_dt(ts)
    if ts is None:
        return "?"
    return ts.astimezone(TZ).strftime("%H:%M")


def _to_local_full(ts):
    ts = _to_dt(ts)
    if ts is None:
        return "?"
    return ts.astimezone(TZ).strftime("%d.%m %H:%M")


def get_conn():
    if not DB_URL:
        raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(
        DB_URL, connect_timeout=30,
        application_name="argus-evening",
    )


# ============================================================
# INDICATORS
# ============================================================
def ema_series(values, period):
    if len(values) < period:
        return [None] * len(values)
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    out = [None] * (period - 1) + [e]
    for i in range(period, len(values)):
        e = values[i] * k + e * (1 - k)
        out.append(e)
    return out


def rsi_series(closes, period=14):
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
    ag = gains / period
    al = losses / period
    rs = ag / al if al > 0 else 999
    out.append(100 - 100 / (1 + rs))
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g = d if d > 0 else 0
        l = -d if d < 0 else 0
        ag = (ag * (period - 1) + g) / period
        al = (al * (period - 1) + l) / period
        rs = ag / al if al > 0 else 999
        out.append(100 - 100 / (1 + rs))
    return out


def atr_value(candles, period=14):
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
# PERIOD STATS
# ============================================================
def compute_period(candles):
    if not candles:
        return None
    first = candles[0]
    last = candles[-1]
    price = last["close"]
    open_p = first["open"]
    change_usd = price - open_p
    change_pct = (change_usd / open_p * 100) if open_p else 0

    hi = max(c["high"] for c in candles)
    lo = min(c["low"] for c in candles)
    hi_pct = (hi - open_p) / open_p * 100 if open_p else 0
    lo_pct = (lo - open_p) / open_p * 100 if open_p else 0

    vol_btc = sum(c["volume"] for c in candles)
    vol_usd = sum(c["quote_volume"] for c in candles)
    taker_buy = sum(c["taker_buy_volume"] for c in candles)
    taker_sell = vol_btc - taker_buy
    buy_pct = (taker_buy / vol_btc * 100) if vol_btc else 0
    sell_pct = 100 - buy_pct if vol_btc else 0
    trades = sum(c["count"] for c in candles)
    avg_trade_usd = (vol_usd / trades) if trades else 0

    green = sum(1 for c in candles if c["close"] >= c["open"])
    red = len(candles) - green

    best = max(candles, key=lambda c: c["high"] - c["open"])
    best_pct = (best["high"] - best["open"]) / best["open"] * 100
    worst = min(candles, key=lambda c: c["low"] - c["open"])
    worst_pct = (worst["low"] - worst["open"]) / worst["open"] * 100

    return {
        "open": open_p,
        "close": price,
        "change_usd": change_usd,
        "change_pct": change_pct,
        "hi": hi,
        "lo": lo,
        "hi_pct": hi_pct,
        "lo_pct": lo_pct,
        "range_usd": hi - lo,
        "range_pct": ((hi - lo) / lo * 100) if lo else 0,
        "vol_btc": vol_btc,
        "vol_usd": vol_usd,
        "taker_buy": taker_buy,
        "taker_sell": taker_sell,
        "buy_pct": buy_pct,
        "sell_pct": sell_pct,
        "trades": trades,
        "avg_trade_usd": avg_trade_usd,
        "green": green,
        "red": red,
        "count": len(candles),
        "best_pct": best_pct,
        "best_ts": best["timestamp"],
        "worst_pct": worst_pct,
        "worst_ts": worst["timestamp"],
    }


def filter_day(candles):
    now_kly = now_local()
    day_start_kly = now_kly.replace(
        hour=DAY_START_H, minute=0, second=0, microsecond=0,
    )
    day_start_utc = day_start_kly.astimezone(timezone.utc)

    out = []
    for c in candles:
        ts = _to_dt(c["timestamp"])
        if ts is not None and ts >= day_start_utc:
            out.append(c)
    return out


# ============================================================
# TEXT BLOCKS
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
        last_dt = _to_dt(last_ts)
        age_min = int(
            (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
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
        last_dt = _to_dt(last_ts)
        age_min = int(
            (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
        )
        lines.append(
            "  Последняя: " + _to_local_full(last_ts)
            + " КЛГ (" + fmt_age(age_min) + " назад)"
        )
    return lines


def fmt_day_block(s):
    lines = []
    if not s:
        lines.append("  нет данных за день")
        return lines

    line = "  " + fmt_price(s["open"])
    line += " → " + fmt_price(s["close"])
    line += " | " + fmt_pct(s["change_pct"])
    line += " (" + fmt_signed_usd(s["change_usd"]) + ")"
    lines.append(line)

    lines.append(
        "  Макс: " + fmt_price(s["hi"])
        + " (" + fmt_pct(s["hi_pct"]) + ")"
    )
    lines.append(
        "  Мин:  " + fmt_price(s["lo"])
        + " (" + fmt_pct(s["lo_pct"]) + ")"
    )
    lines.append(
        "  Диапазон: " + fmt_price(s["range_usd"])
        + " (" + format(s["range_pct"], ".2f") + "%)"
    )
    lines.append(
        "  Свечей: " + str(s["count"])
        + " (🟢 " + str(s["green"]) + " / 🔴 " + str(s["red"]) + ")"
    )
    return lines


def fmt_volume_block(s):
    lines = []
    if not s:
        lines.append("  нет данных")
        return lines

    lines.append(
        "  Объём: " + fmt_btc(s["vol_btc"])
        + " | " + fmt_usd(s["vol_usd"])
    )

    bar_len = 16
    b = int(round(s["buy_pct"] / 100 * bar_len))
    b = max(0, min(b, bar_len))
    bar = "🟢" * b + "🔴" * (bar_len - b)

    lines.append(
        "  Taker buy:  " + fmt_btc(s["taker_buy"])
        + " (" + format(s["buy_pct"], ".1f") + "%)"
    )
    lines.append(
        "  Taker sell: " + fmt_btc(s["taker_sell"])
        + " (" + format(s["sell_pct"], ".1f") + "%)"
    )
    lines.append("  " + bar)
    lines.append("  Сделок: " + format(s["trades"], ","))
    lines.append("  Средняя сделка: " + fmt_usd(s["avg_trade_usd"]))
    return lines


def fmt_indicators_block(candles):
    lines = []
    if not candles:
        lines.append("  нет данных")
        return lines

    price = candles[-1]["close"]
    a = atr_value(candles, 14)
    a_pct = (a / price * 100) if a and price else 0

    closes = [c["close"] for c in candles]
    r15 = rsi_series(closes, 14)[-1]

    c1h = []
    for i in range(0, len(candles) - 3, 4):
        chunk = candles[i:i + 4]
        c1h.append(chunk[-1]["close"])
    r1h = rsi_series(c1h, 14)[-1] if c1h else None

    e9 = ema_series(closes, 9)[-1]
    e21 = ema_series(closes, 21)[-1]
    e50 = ema_series(closes, 50)[-1]

    if a:
        lines.append(
            "  ATR(14) 15m: " + fmt_price(a)
            + " (" + format(a_pct, ".2f") + "%)"
        )
    if r15 is not None:
        st = "перекуп" if r15 >= 70 else "перепрод" if r15 <= 30 else "нейтр"
        lines.append("  RSI(14) 15m: " + format(r15, ".1f") + " " + st)
    if r1h is not None:
        st = "перекуп" if r1h >= 70 else "перепрод" if r1h <= 30 else "нейтр"
        lines.append("  RSI(14) 1h:  " + format(r1h, ".1f") + " " + st)
    if e9:
        lines.append(
            "  EMA(9)  15m: " + fmt_price(e9)
            + " (" + ("выше" if price >= e9 else "ниже") + ")"
        )
    if e21:
        lines.append(
            "  EMA(21) 15m: " + fmt_price(e21)
            + " (" + ("выше" if price >= e21 else "ниже") + ")"
        )
    if e50:
        lines.append(
            "  EMA(50) 15m: " + fmt_price(e50)
            + " (" + ("выше" if price >= e50 else "ниже") + ")"
        )
    return lines


def fmt_extremes_block(s):
    lines = []
    if not s:
        lines.append("  нет данных")
        return lines
    lines.append(
        "  Макс. рост:    " + fmt_pct(s["best_pct"])
        + " в " + _to_local(s["best_ts"]) + " КЛГ"
    )
    lines.append(
        "  Макс. падение: " + fmt_pct(s["worst_pct"])
        + " в " + _to_local(s["worst_ts"]) + " КЛГ"
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
            url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
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


# ============================================================
# REPORT
# ============================================================
def build_report_text(candles, stats):
    now = now_local()
    lines = []
    lines.append("🌙 <b>ARGUS — ВЕЧЕРНИЙ ОТЧЁТ</b>")
    lines.append(now.strftime("%d.%m.%Y %H:%M") + " КЛГ")
    lines.append(
        "📅 Период: " + str(DAY_START_H).zfill(2)
        + ":00 → " + str(DAY_END_H).zfill(2) + ":00 КЛГ"
    )
    lines.append("")

    lines.extend(fmt_collector_block(stats))
    lines.append("")
    lines.append("─" * 20)
    lines.append("")

    day_candles = filter_day(candles)
    s_day = compute_period(day_candles)

    lines.append("📊 <b>ИТОГ ДНЯ</b>")
    lines.extend(fmt_day_block(s_day))
    lines.append("")

    lines.append("💵 <b>Объёмы за день</b>")
    lines.extend(fmt_volume_block(s_day))
    lines.append("")

    lines.append("📈 <b>Индикаторы</b>")
    lines.extend(fmt_indicators_block(candles))
    lines.append("")

    lines.append("🕯️ <b>Крайние точки дня</b>")
    lines.extend(fmt_extremes_block(s_day))

    if len(candles) >= 96:
        s_24 = compute_period(candles[-96:])
        lines.append("")
        lines.append("─" * 20)
        lines.append("")
        lines.append("📉 <b>КОНТЕКСТ 24ч</b> <i>(справочно)</i>")
        line = "  " + fmt_price(s_24["open"])
        line += " → " + fmt_price(s_24["close"])
        line += " | " + fmt_pct(s_24["change_pct"])
        lines.append(line)
        lines.append(
            "  Объём: " + fmt_btc(s_24["vol_btc"])
            + " | " + fmt_usd(s_24["vol_usd"])
        )

    return "\n".join(lines)


def main():
    print("Evening report v17.1 - start")

    stats = fetch_stats(SYMBOL)
    candles = fetch_candles(SYMBOL, 300)

    text = build_report_text(candles, stats)
    print("len: " + str(len(text)))
    send_message(text)
    print("done")


if __name__ == "__main__":
    main()