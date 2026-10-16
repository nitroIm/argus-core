# ============================================================
# ARGUS - EVENING REPORT v16.1
# ------------------------------------------------------------
# Только BTC. Только Aiven DB. Только candles.
# Ось X — реальное время. Объёмы с подписями. Метки пропусков.
# ============================================================

import os
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import psycopg
except ImportError:
    print("ERR: psycopg not installed")
    sys.exit(1)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.patches import Rectangle
    MPL_OK = True
except ImportError:
    MPL_OK = False
    print("WARN: matplotlib not installed")

TZ = ZoneInfo("Europe/Kaliningrad")
TMP_DIR = Path("/tmp/argus_charts")
TMP_DIR.mkdir(parents=True, exist_ok=True)

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
H24 = 96
H12 = 48


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
# HELPERS
# ============================================================
def period_stats(candles, label):
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
        "label": label,
        "price": price,
        "open": open_p,
        "change_usd": change_usd,
        "change_pct": change_pct,
        "hi": hi,
        "lo": lo,
        "hi_pct": hi_pct,
        "lo_pct": lo_pct,
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
        "best_pct": best_pct,
        "best_ts": best["timestamp"],
        "worst_pct": worst_pct,
        "worst_ts": worst["timestamp"],
    }


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
        lines.append(
            "  Последняя: " + _to_local(last_ts)
            + " КЛГ (" + fmt_age(age_min) + " назад)"
        )
    return lines


def fmt_day_block(s):
    lines = ["🌤️ <b>С утра</b>"]
    if not s:
        lines.append("  нет данных")
        return lines
    line = "  " + fmt_price(s["open"])
    line += " → " + fmt_price(s["price"])
    line += " | " + fmt_pct(s["change_pct"])
    line += " (" + ("+" if s["change_usd"] >= 0 else "")
    line += fmt_price(abs(s["change_usd"])) + ")"
    lines.append(line)
    lines.append(
        "  Макс: " + fmt_price(s["hi"])
        + " (" + fmt_pct(s["hi_pct"]) + ")"
    )
    lines.append(
        "  Мин: " + fmt_price(s["lo"])
        + " (" + fmt_pct(s["lo_pct"]) + ")"
    )
    lines.append(
        "  Диапазон: " + fmt_price(s["hi"] - s["lo"])
        + " (" + format((s["hi"] - s["lo"]) / s["lo"] * 100, ".2f") + "%)"
    )
    lines.append(
        "  Свечей: " + str(s["green"] + s["red"])
        + " (🟢 " + str(s["green"]) + " / 🔴 " + str(s["red"]) + ")"
    )
    return lines


def fmt_volume_block(s):
    lines = ["📊 <b>Объёмы за 24ч</b>"]
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
        "  Taker buy: " + fmt_btc(s["taker_buy"])
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
    lines = ["📈 <b>Индикаторы</b>"]
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
            + " (цена " + ("выше" if price >= e9 else "ниже") + ")"
        )
    if e21:
        lines.append(
            "  EMA(21) 15m: " + fmt_price(e21)
            + " (цена " + ("выше" if price >= e21 else "ниже") + ")"
        )
    if e50:
        lines.append(
            "  EMA(50) 15m: " + fmt_price(e50)
            + " (цена " + ("выше" if price >= e50 else "ниже") + ")"
        )
    return lines


def fmt_structure_block(s):
    lines = ["🕯️ <b>Структура 24ч</b>"]
    if not s:
        lines.append("  нет данных")
        return lines
    lines.append(
        "  Свечей: " + str(s["green"] + s["red"])
        + " (🟢 " + str(s["green"]) + " / 🔴 " + str(s["red"]) + ")"
    )
    lines.append(
        "  Макс. рост: " + fmt_pct(s["best_pct"])
        + " в " + _to_local(s["best_ts"])
    )
    lines.append(
        "  Макс. падение: " + fmt_pct(s["worst_pct"])
        + " в " + _to_local(s["worst_ts"])
    )
    return lines


# ============================================================
# CHART
# ============================================================
def _to_dt(ts):
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def find_gaps(candles, expected_min=15, tol_min=1):
    """Возвращает список (start_dt, end_dt) где разрыв > expected+tol."""
    out = []
    for i in range(1, len(candles)):
        prev = _to_dt(candles[i - 1]["timestamp"])
        cur = _to_dt(candles[i]["timestamp"])
        diff = (cur - prev).total_seconds() / 60
        if diff > expected_min + tol_min:
            out.append((prev, cur, int(diff)))
    return out


def plot_chart(candles, out_path):
    if not MPL_OK or not candles:
        return False
    try:
        # Время в днях (matplotlib формат)
        times = [mdates.date2num(_to_dt(c["timestamp"])) for c in candles]
        closes = [c["close"] for c in candles]

        e9 = ema_series(closes, 9)
        e21 = ema_series(closes, 21)
        e50 = ema_series(closes, 50)
        rsi = rsi_series(closes, 14)

        vol_max = max(c["volume"] for c in candles) if candles else 0

        # Ширина свечи = 15 мин в днях
        w = 15 / (24 * 60)

        fig, (ax1, ax2, ax3) = plt.subplots(
            3, 1, figsize=(14, 9),
            gridspec_kw={"height_ratios": [3, 1, 1]},
            sharex=True,
        )

        # ---- GAPS: вертикальные полосы на всех панелях ----
        gaps = find_gaps(candles)
        for (g_start, g_end, _) in gaps:
            x1 = mdates.date2num(g_start)
            x2 = mdates.date2num(g_end)
            for ax in (ax1, ax2, ax3):
                ax.axvspan(x1, x2, color="red", alpha=0.15, zorder=0)

        # ---- panel 1: свечи ----
        for i, c in enumerate(candles):
            x = times[i]
            color = "#26a69a" if c["close"] >= c["open"] else "#ef5350"
            # фитиль
            ax1.plot(
                [x, x], [c["low"], c["high"]],
                color=color, linewidth=0.8, zorder=2,
            )
            # тело
            y0 = min(c["open"], c["close"])
            h = abs(c["close"] - c["open"])
            if h == 0:
                h = (c["high"] - c["low"]) * 0.005
            ax1.add_patch(Rectangle(
                (x - w / 2, y0), w, h,
                color=color, zorder=3, linewidth=0,
            ))

        def plot_line(ax, series, color, label):
            xx, yy = [], []
            for i, v in enumerate(series):
                if v is not None:
                    xx.append(times[i])
                    yy.append(v)
            if xx:
                ax.plot(xx, yy, color=color, linewidth=1.0, label=label, zorder=4)

        plot_line(ax1, e9, "#ffb74d", "EMA 9")
        plot_line(ax1, e21, "#4fc3f7", "EMA 21")
        plot_line(ax1, e50, "#ba68c8", "EMA 50")

        ax1.set_ylabel("Price $", fontsize=11)
        ax1.set_title(NAME + " 15m — вечер", fontsize=13)
        ax1.grid(alpha=0.25)
        ax1.legend(loc="upper left", fontsize=9)

        # ---- panel 2: объёмы ----
        for i, c in enumerate(candles):
            color = "#26a69a" if c["close"] >= c["open"] else "#ef5350"
            ax2.bar(times[i], c["volume"], color=color, width=w * 0.85, zorder=2)

        ax2.set_ylabel("Volume BTC", fontsize=11)
        ax2.grid(alpha=0.25)
        ax2.set_ylim(0, vol_max * 1.15)

        # средний объём пунктиром
        if candles:
            avg_v = sum(c["volume"] for c in candles) / len(candles)
            ax2.axhline(avg_v, color="#888", linewidth=0.8, linestyle="--")
            ax2.text(
                times[-1], avg_v,
                "  avg " + format(avg_v, ",.0f"),
                fontsize=8, color="#666", va="center",
            )

        # подписи значений над самыми большими столбиками
        top_idx = sorted(
            range(len(candles)),
            key=lambda i: candles[i]["volume"],
            reverse=True,
        )[:3]
        for i in top_idx:
            v = candles[i]["volume"]
            if v > 0:
                ax2.text(
                    times[i], v,
                    format(v, ",.0f"),
                    fontsize=8, ha="center", va="bottom",
                    color="#333", zorder=5,
                )

        # ---- panel 3: RSI ----
        rx, ry = [], []
        for i, v in enumerate(rsi):
            if v is not None:
                rx.append(times[i])
                ry.append(v)
        if rx:
            ax3.plot(rx, ry, color="#ba68c8", linewidth=1.0, zorder=3)
        ax3.axhline(70, color="#ef5350", linewidth=0.6, alpha=0.7)
        ax3.axhline(30, color="#26a69a", linewidth=0.6, alpha=0.7)
        ax3.axhline(50, color="#888", linewidth=0.5, alpha=0.4)
        ax3.set_ylim(0, 100)
        ax3.set_ylabel("RSI", fontsize=11)
        ax3.grid(alpha=0.25)

        # ---- X axis: время ----
        ax3.xaxis.set_major_locator(mdates.HourLocator(interval=3))
        ax3.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TZ))
        plt.setp(ax3.xaxis.get_majorticklabels(), rotation=0, fontsize=9)

        plt.tight_layout()
        plt.savefig(out_path, dpi=95)
        plt.close(fig)
        return True
    except Exception as e:
        print("chart: " + str(e))
        return False


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


def send_photo(path, caption=""):
    if not BOT_TOKEN or not CHAT_ID:
        return False
    if not Path(path).exists():
        return False
    try:
        url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendPhoto"
        with open(path, "rb") as f:
            r = requests.post(
                url,
                data={"chat_id": CHAT_ID, "caption": caption},
                files={"photo": f},
                timeout=60,
            )
        return r.status_code == 200
    except Exception as e:
        print("photo: " + str(e))
        return False


# ============================================================
# REPORT
# ============================================================
def build_report_text(candles, stats):
    now = now_local()
    lines = []
    lines.append("🌙 <b>ARGUS — вечер</b>")
    lines.append(now.strftime("%d.%m.%Y %H:%M") + " КЛГ")
    lines.append("")

    lines.extend(fmt_collector_block(stats))
    lines.append("")
    lines.append("─" * 20)
    lines.append("")

    if len(candles) >= H12:
        s12 = period_stats(candles[-H12:], "12h")
    else:
        s12 = period_stats(candles, "all")
    lines.extend(fmt_day_block(s12))
    lines.append("")

    if len(candles) >= H24:
        s24 = period_stats(candles[-H24:], "24h")
    else:
        s24 = period_stats(candles, "all")

    lines.extend(fmt_volume_block(s24))
    lines.append("")
    lines.extend(fmt_indicators_block(candles))
    lines.append("")
    lines.extend(fmt_structure_block(s24))

    return "\n".join(lines)


def main():
    print("Evening report v16.1 - start")

    stats = fetch_stats(SYMBOL)
    candles = fetch_candles(SYMBOL, 300)

    text = build_report_text(candles, stats)
    print("len: " + str(len(text)))

    if MPL_OK and candles:
        path = TMP_DIR / "btc_evening.png"
        if plot_chart(candles, str(path)):
            send_photo(str(path), "")

    send_message(text)
    print("done")


if __name__ == "__main__":
    main()