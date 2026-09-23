# ============================================================
# ARGUS - УТРЕННИЙ ОТЧЁТ РЫНКА v3.2
# ------------------------------------------------------------
# v3.2: fix — escape < и > в правилах (HTML parse error)
# v3.1: fix — разбивка длинного текста
# v3: + ATR, RSI, funding, OI, стоп-лоссы
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
REPO_ROOT = CRYPTO_ROOT.parent
DATA_DIR = CRYPTO_ROOT / "data"
TMP_DIR = Path("/tmp/argus_charts")
TMP_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection, close_connection
from report.charts import plot_candles
from report.charts import plot_pattern
from report.charts import plot_markov
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


def escape_html(text):
    """Экранирование < > для Telegram HTML."""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace(
        "<", "&lt;"
    ).replace(">", "&gt;")


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def fmt_price(p):
    if p >= 1000:
        return "$" + format(int(p), ",")
    if p >= 1:
        return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")


def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("no token")
        return False

    MAX = 3800
    if len(text) <= MAX:
        parts = [text]
    else:
        parts = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > MAX:
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

    ok_all = True
    for i, part in enumerate(parts):
        try:
            url = "https://api.telegram.org/bot"
            url += BOT_TOKEN + "/sendMessage"
            r = requests.post(
                url,
                json={
                    "chat_id": CHAT_ID,
                    "text": part,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )
            if r.status_code != 200:
                print("send err " + str(r.status_code)
                      + ": " + r.text[:200])
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
        attach_name = "file" + str(i)
        media_item = {
            "type": "photo",
            "media": "attach://" + attach_name,
        }
        if i == 0 and caption:
            media_item["caption"] = caption[:1000]
            media_item["parse_mode"] = "HTML"
        media.append(media_item)

        files[attach_name] = (
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
                    "SELECT timestamp, open, high, low, "
                    "close, volume FROM candles "
                    "WHERE symbol = %s AND timeframe = '1h' "
                    "ORDER BY timestamp DESC LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
                rows = list(reversed(cur.fetchall()))
                result = []
                for r in rows:
                    result.append({
                        "timestamp": r[0],
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                    })
                return result
    except Exception as e:
        print("candles: " + str(e))
        return []


def fetch_funding(symbol, limit=50):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT timestamp, rate FROM funding_rates "
                    "WHERE symbol = %s AND rate IS NOT NULL "
                    "ORDER BY timestamp DESC LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
                rows = list(reversed(cur.fetchall()))
                return [
                    {"timestamp": r[0], "rate": float(r[1])}
                    for r in rows
                ]
    except Exception:
        return []


def fetch_oi(symbol, limit=100):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT timestamp, oi FROM open_interest "
                    "WHERE symbol = %s AND oi IS NOT NULL "
                    "ORDER BY timestamp DESC LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
                rows = list(reversed(cur.fetchall()))
                return [
                    {"timestamp": r[0], "oi": float(r[1])}
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
    p = patterns.get("symbols", {}).get(name + "USDT", {})
    mk = p.get("markov", {})
    p10 = mk.get("p_1_given_0", 0)
    p11 = mk.get("p_1_given_1", 0)

    if p10 > 0.58:
        return "после падения — отскок"
    if p10 < 0.42:
        return "падение продолжается"
    if p11 > 0.58:
        return "рост продолжается"
    return "нейтрально, ждём пробоя"


def build_trade_advice(name, symbol, candles, levels,
                       patterns, funding_data, oi_data):
    lines = []
    if not candles:
        return lines

    price = candles[-1]["close"]
    atr = compute_atr(candles, 14)

    sym_lvl = levels.get("symbols", {}).get(symbol, {})
    supports = sym_lvl.get("supports", [])
    resistances = sym_lvl.get("resistances", [])

    if atr:
        stop_tight = round(price - atr * 1.5, 2)
        stop_wide = round(price - atr * 2.5, 2)
        lines.append(
            "  ATR(14): " + fmt_price(atr)
            + " | стоп: "
            + fmt_price(stop_tight)
            + " / " + fmt_price(stop_wide)
        )

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
        lines.append(
            "  RSI(14): " + format(r, ".1f") + " — " + state
        )

    if funding_data:
        cur_f = funding_data[-1]["rate"] * 100
        if cur_f > 0.01:
            state = "перегрев лонгов"
        elif cur_f < -0.01:
            state = "перегрев шортов"
        else:
            state = "сбалансирован"
        lines.append(
            "  Funding: " + format(cur_f, "+.4f")
            + "% — " + state
        )

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
            lines.append(
                "  OI: " + format(oi_ch, "+.2f")
                + "% — " + state
            )

    if supports and resistances:
        s1 = supports[0]["price"]
        r1 = resistances[0]["price"]
        lines.append(
            "  Уровни: " + fmt_price(s1)
            + " / " + fmt_price(r1)
        )

    return lines


def build_report_text():
    now = datetime.now(timezone.utc)
    lines = []
    lines.append("☀️ ARGUS — утренний отчёт")
    lines.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    lines.append("")

    levels = load_json(DATA_DIR / "levels_analysis.json")
    patterns = load_json(DATA_DIR / "_ "patterns_analysis.json")
    corr = load_json(DATAc_DIR / "correlations.json")

    pairs = [
andles        ("BTCUSDT",.png "BTC", "BTC"),
        ("ETHUSDT", "ETH", "ETH"),
    ]

    for symbol, name, _ in pairs:
        candles = fetch_candles(symbol, 200)
        if not candles:
            continue

        price = candles[-1]["close"]
        change_24h = 0
        if len(candles) >= 25:
            prev = candles[-25]["close"]
            if prev:
                change_24h = (price - prev) / prev * 100

        lines.append(
            "💰 <b>" + name + "</b>: " + fmt_price(price)
            + " (" + format(change_24h, "+.2f") + "% 24ч)"
        )

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
            key=lambda x: (x["confidence"], x["samples"]),
            reverse=True,
        )
        if rules:
            lines.append("🧠 Закономерности:")
            for r in rules[:3]:
                arrow = "↑" if r["direction"] == "up" else "↓"
                sym_safe = escape_html(r["symbol"])
                rule_safe = escape_html(r["rule"])
                line = "  " + arrow + " [" + sym_safe + "] "
                line += rule_safe
                line += " (" + format(
                    r["confidence"] * 100, ".0f"
                )
                line += "%, N=" + str(r["samples"]) + ")"
                lines.append(line)
            lines.append("")

    lines.append("📊 Графики ниже одним альбомом")

    return "\n".join(lines)


def main():
    print("Morning report v3.2 - start")

    text = build_report_text()
    print("text len: " + str(len(text)))
    send_message(text)

    levels = load_json(DATA_DIR / "levels_analysis.json")
    patterns = load_json(DATA_DIR / "patterns_analysis.json")

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

        sym_lvl = levels.get("symbols", {}).get(symbol, {})
        sup = sym_lvl.get("supports", [])
        res = sym_lvl.get("resistances", [])

        path = TMP_DIR / (prefix +")
        plot_candles(
            symbol, candles,
            supports=sup, resistances=res,
            output_path=str(path),
            title=name,
        )
        photos.append((str(path), ""))

        path = TMP_DIR / (prefix + "_rsi.png")
        if plot_rsi(symbol, candles, output_path=str(path)):
            photos.append((str(path), ""))

        funding_data = fetch_funding(symbol, 50)
        if funding_data:
            path = TMP_DIR / (prefix + "_funding.png")
            if plot_funding(
                symbol, funding_data, output_path=str(path)
            ):
                photos.append((str(path), ""))

        oi_data = fetch_oi(symbol, 100)
        if oi_data:
            path = TMP_DIR / (prefix + "_oi.png")
            if plot_oi(
                symbol, oi_data, output_path=str(path)
            ):
                photos.append((str(path), ""))

        sym_p = patterns.get("symbols", {}).get(symbol, {})
        binary = sym_p.get("binary_string", "")
        if binary:
            path = TMP_DIR / (prefix + "_pattern.png")
            if plot_pattern(
                symbol, binary, output_path=str(path)
            ):
                photos.append((str(path), ""))

    if photos:
        first_cap = "📊 " + str(len(photos)) + " графиков"
        photos_with_cap = [(photos[0][0], first_cap)] + photos[1:]
        ok = send_media_group(photos_with_cap)
        print("album: " + str(ok))
    else:
        print("no charts")

    close_connection()
    print("Morning report - done")


if __name__ == "__main__":
    main()