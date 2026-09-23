# ============================================================
# ARGUS - УТРЕННИЙ ОТЧЁТ v4.1
# ------------------------------------------------------------
# v4: + spot цена (актуальная, не от 1h свечи)
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
TMP_DIR = Path("/tmp/argus_charts")
TMP_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection
from db import close_connection
from report.charts import plot_candles
from report.charts import plot_pattern
from report.charts import plot_rsi
from report.charts import plot_funding
from report.charts import plot_oi
from report.charts import compute_rsi

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()

# Биржи для spot (fallback)
SPOT_SOURCES = [
    {
        "name": "MEXC",
        "url": "https://api.mexc.com/api/v3/ticker/price?symbol={sym}",
        "parse": lambda j: float(j["price"]),
    },
    {
        "name": "Binance",
        "url": "https://api.binance.com/api/v3/ticker/price?symbol={sym}",
        "parse": lambda j: float(j["price"]),
    },
    {
        "name": "Bybit",
        "url": "https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}",
        "parse": lambda j: float(j["result"]["list"][0]["lastPrice"]),
    },
    {
        "name": "OKX",
        "url": "https://www.okx.com/api/v5/market/ticker?instId={sym_dash}",
        "parse": lambda j: float(j["data"][0]["last"]),
        "sym_fmt": lambda s: s.replace("USDT", "-USDT"),
    },
]


def fetch_spot(symbol):
    """Текущая spot цена через fallback бирж."""
    for src in SPOT_SOURCES:
        try:
            url = src["url"]
            if "sym_fmt" in src:
                url = url.format(
                    sym_dash=src["sym_fmt"](symbol)
                )
            else:
                url = url.format(sym=symbol)
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                return src["parse"](r.json())
        except Exception:
            continue
    return None


def escape_html(text):
    if not text:
        return ""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def load_json(path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def fmt_price(p):
    if p >= 1000:
        return "$" + format(int(p), ",")
    if p >= 1:
        return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")


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
            if current:
                current = current + "\n" + line
            else:
                current = line
    if current:
        parts.append(current)
    return parts


def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("no token")
        return False

    parts = split_text(text, 3800)
    ok_all = True

    for i, part in enumerate(parts):
        try:
            url = "https://api.telegram.org/bot"
            url += BOT_TOKEN + "/sendMessage"
            payload = {
                "chat_id": CHAT_ID,
                "text": part,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            r = requests.post(
                url, json=payload, timeout=20,
            )
            if r.status_code != 200:
                msg = "send err "
                msg += str(r.status_code)
                print(msg)
                ok_all = False
        except Exception as e:
            print("send: " + str(e))
            ok_all = False

    print("text sent: " + str(len(parts)) + " parts")
    return ok_all


def send_media_group(photos):
    if not BOT_TOKEN or not CHAT_ID:
        return False
    if not photos:
        return False

    photos = photos[:10]
    media = []
    files = {}

    for i, (path, caption) in enumerate(photos):
        if not Path(path).exists():
            continue
        attach = "file" + str(i)
        item = {
            "type": "photo",
            "media": "attach://" + attach,
        }
        if i == 0 and caption:
            item["caption"] = caption[:1000]
            item["parse_mode"] = "HTML"
        media.append(item)

        files[attach] = (
            Path(path).name,
            open(path, "rb"),
            "image/png",
        )

    if not media:
        return False

    try:
        url = "https://api.telegram.org/bot"
        url += BOT_TOKEN + "/sendMediaGroup"
        data = {
            "chat_id": CHAT_ID,
            "media": json.dumps(media),
        }
        r = requests.post(
            url, data=data, files=files, timeout=60,
        )
        for f in files.values():
            try:
                f[1].close()
            except Exception:
                pass
        if r.status_code == 200:
            print("album: " + str(len(media)))
            return True
        print("album err: " + r.text[:200])
        return False
    except Exception as e:
        print("album: " + str(e))
        return False


def fetch_candles(symbol, limit=200):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT timestamp, open, high, "
                    "low, close, volume FROM candles "
                    "WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "ORDER BY timestamp DESC LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
                rows = list(reversed(cur.fetchall()))
                out = []
                for r in rows:
                    out.append({
                        "timestamp": r[0],
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                    })
                return out
    except Exception as e:
        print("candles: " + str(e))
        return []


def fetch_funding(symbol, limit=50):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT timestamp, rate "
                    "FROM funding_rates "
                    "WHERE symbol = %s "
                    "AND rate IS NOT NULL "
                    "ORDER BY timestamp DESC LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
                rows = list(reversed(cur.fetchall()))
                return [
                    {
                        "timestamp": r[0],
                        "rate": float(r[1]),
                    }
                    for r in rows
                ]
    except Exception:
        return []


def fetch_oi(symbol, limit=100):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT timestamp, oi "
                    "FROM open_interest "
                    "WHERE symbol = %s "
                    "AND oi IS NOT NULL "
                    "ORDER BY timestamp DESC LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
                rows = list(reversed(cur.fetchall()))
                return [
                    {
                        "timestamp": r[0],
                        "oi": float(r[1]),
                    }
                    for r in rows
                ]
    except Exception:
        return []


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
    atr = sum(trs[-period:]) / period
    return round(atr, 4)


def build_scenario(name, patterns, levels):
    p = patterns.get("symbols", {}).get(
        name + "USDT", {}
    )
    mk = p.get("markov", {})
    p10 = mk.get("p_1_given_0", 0)
    p11 = mk.get("p_1_given_1", 0)

    if p10 > 0.58:
        return "после падения - отскок"
    if p10 < 0.42:
        return "падение продолжается"
    if p11 > 0.58:
        return "рост продолжается"
    return "нейтрально, ждём пробоя"


def build_trade_advice(
    name, symbol, candles, levels,
    patterns, funding_data, oi_data,
):
    lines = []
    if not candles:
        return lines

    price = candles[-1]["close"]
    atr = compute_atr(candles, 14)

    sym_lvl = levels.get("symbols", {}).get(
        symbol, {}
    )
    supports = sym_lvl.get("supports", [])
    resistances = sym_lvl.get("resistances", [])

    if atr:
        stop_tight = round(price - atr * 1.5, 2)
        stop_wide = round(price - atr * 2.5, 2)
        line = "  ATR(14): " + fmt_price(atr)
        line += " | стоп: "
        line += fmt_price(stop_tight)
        line += " / " + fmt_price(stop_wide)
        lines.append(line)

    closes = [c["close"] for c in candles]
    rsi = compute_rsi(closes, 14)
    if rsi and rsi[-1] is not None:
        r = rsi[-1]
        if r >= 70:
            state = "перекуплен"
        elif r <= 30:
            state = "перепродан"
        else:
            state = "нейтрально"
        line = "  RSI(14): "
        line += format(r, ".1f")
        line += " - " + state
        lines.append(line)

    if funding_data:
        cur_f = funding_data[-1]["rate"] * 100
        if cur_f > 0.01:
            state = "перегрев лонгов"
        elif cur_f < -0.01:
            state = "перегрев шортов"
        else:
            state = "сбалансирован"
        line = "  Funding: "
        line += format(cur_f, "+.4f")
        line += "% - " + state
        lines.append(line)

    if oi_data and len(oi_data) >= 2:
        first = oi_data[0]["oi"]
        cur = oi_data[-1]["oi"]
        if first:
            oi_ch = (cur - first) / first * 100
            if oi_ch > 1:
                state = "тренд усиливается"
            elif oi_ch < -1:
                state = "тренд слабеет"
            else:
                state = "флэт"
            line = "  OI: "
            line += format(oi_ch, "+.2f")
            line += "% - " + state
            lines.append(line)

    if supports and resistances:
        s1 = supports[0]["price"]
        r1 = resistances[0]["price"]
        line = "  Уровни: " + fmt_price(s1)
        line += " / " + fmt_price(r1)
        lines.append(line)

    return lines


def build_report_text():
    now = datetime.now(timezone.utc)
    lines = []
    lines.append("☀️ ARGUS — утренний отчёт")
    lines.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    lines.append("")

    levels = load_json(
        DATA_DIR / "levels_analysis.json"
    )
    patterns = load_json(
        DATA_DIR / "patterns_analysis.json"
    )
    corr = load_json(DATA_DIR / "correlations.json")

    pairs = [
        ("BTCUSDT", "BTC", "BTC"),
        ("ETHUSDT", "ETH", "ETH"),
    ]

    for symbol, name, _ in pairs:
        candles = fetch_candles(symbol, 200)
        if not candles:
            continue

        price_close = candles[-1]["close"]
        spot = fetch_spot(symbol)

        change_24h = 0
        if len(candles) >= 25:
            prev = candles[-25]["close"]
            if prev:
                change_24h = (
                    (price_close - prev) / prev * 100
                )

        line = "💰 <b>" + name + "</b>: "
        if spot:
            line += fmt_price(spot) + " (spot)"
        else:
            line += fmt_price(price_close)
        line += " | свеча: "
        line += format(change_24h, "+.2f") + "% 24ч"
        lines.append(line)

        funding_data = fetch_funding(symbol, 50)
        oi_data = fetch_oi(symbol, 100)

        advice = build_trade_advice(
            name, symbol, candles, levels,
            patterns, funding_data, oi_data,
        )
        lines.extend(advice)

        scenario = build_scenario(name, patterns, levels)
        lines.append("  🎯 Сценарий: " + scenario)
        lines.append("")

    if corr and corr.get("symbols"):
        rules = []
        for sym, d in corr["symbols"].items():
            for r in d.get("rules", []):
                r2 = dict(r)
                r2["symbol"] = sym.replace("USDT", "")
                rules.append(r2)
        rules.sort(
            key=lambda x: (
                x["confidence"], x["samples"]
            ),
            reverse=True,
        )
        if rules:
            lines.append("🧠 Закономерности:")
            for r in rules[:3]:
                if r["direction"] == "up":
                    arrow = "↑"
                else:
                    arrow = "↓"
                sym_safe = escape_html(r["symbol"])
                rule_safe = escape_html(r["rule"])
                line = "  " + arrow + " ["
                line += sym_safe + "] "
                line += rule_safe
                line += " ("
                line += format(r["confidence"] * 100, ".0f")
                line += "%, N=" + str(r["samples"]) + ")"
                lines.append(line)
            lines.append("")

    lines.append("📊 Графики ниже одним альбомом")

    return "\n".join(lines)


def main():
    print("Morning report v4.1 - start")

    text = build_report_text()
    print("text len: " + str(len(text)))
    send_message(text)

    levels = load_json(
        DATA_DIR / "levels_analysis.json"
    )
    patterns = load_json(
        DATA_DIR / "patterns_analysis.json"
    )

    pairs = [
        ("BTCUSDT", "BTC", "btc"),
        ("ETHUSDT", "ETH", "eth"),
    ]

    photos = []

    for symbol, name, prefix in pairs:
        print("--- " + symbol)

        candles = fetch_candles(symbol, 200)
        if not candles:
            continue

        sym_lvl = levels.get("symbols", {}).get(
            symbol, {}
        )
        sup = sym_lvl.get("supports", [])
        res = sym_lvl.get("resistances", [])

        path = TMP_DIR / (prefix + "_candles.png")
        plot_candles(
            symbol, candles,
            supports=sup, resistances=res,
            output_path=str(path),
            title=name,
        )
        photos.append((str(path), ""))

        path = TMP_DIR / (prefix + "_rsi.png")
        if plot_rsi(
            symbol, candles, output_path=str(path)
        ):
            photos.append((str(path), ""))

        funding_data = fetch_funding(symbol, 50)
        if funding_data:
            path = TMP_DIR / (prefix + "_funding.png")
            if plot_funding(
                symbol, funding_data,
                output_path=str(path),
            ):
                photos.append((str(path), ""))

        oi_data = fetch_oi(symbol, 100)
        if oi_data:
            path = TMP_DIR / (prefix + "_oi.png")
            if plot_oi(
                symbol, oi_data,
                output_path=str(path),
            ):
                photos.append((str(path), ""))

        sym_p = patterns.get("symbols", {}).get(
            symbol, {}
        )
        binary = sym_p.get("binary_string", "")
        if binary:
            path = TMP_DIR / (prefix + "_pattern.png")
            if plot_pattern(
                symbol, binary,
                output_path=str(path),
            ):
                photos.append((str(path), ""))

    if photos:
        first_cap = "📊 " + str(len(photos))
        first_cap += " графиков"
        photos_cap = [(photos[0][0], first_cap)]
        photos_cap += photos[1:]
        ok = send_media_group(photos_cap)
        print("album: " + str(ok))
    else:
        print("no charts")

    close_connection()
    print("Morning report - done")


if __name__ == "__main__":
    main()