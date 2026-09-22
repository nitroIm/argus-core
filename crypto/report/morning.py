# ============================================================
# ARGUS - УТРЕННИЙ ОТЧЁТ РЫНКА
# ------------------------------------------------------------
# Каждое утро в 07:00 UTC (09:00 Калининград):
#   1. Читает свечи, паттерны, уровни, новости
#   2. Генерит 6 графиков (BTC/ETH candles/pattern/markov)
#   3. Отправляет текст + 6 фото в Telegram
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

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()


# ------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------
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
        clean = text.replace("<b>", "").replace("</b>", "")
        clean = clean.replace("<i>", "").replace("</i>", "")
        print(clean)
        return False
    try:
        url = "https://api.telegram.org/bot"
        url += BOT_TOKEN + "/sendMessage"
        r = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        return r.status_code == 200
    except Exception as e:
        print("send: " + str(e))
        return False


def send_photo(path, caption=""):
    if not BOT_TOKEN or not CHAT_ID:
        return False
    if not path or not Path(path).exists():
        return False
    try:
        url = "https://api.telegram.org/bot"
        url += BOT_TOKEN + "/sendPhoto"
        with open(path, "rb") as f:
            files = {"photo": f}
            data = {"chat_id": CHAT_ID}
            if caption:
                data["caption"] = caption[:1000]
                data["parse_mode"] = "HTML"
            r = requests.post(
                url, data=data, files=files, timeout=40,
            )
        return r.status_code == 200
    except Exception as e:
        print("photo: " + str(e))
        return False


def fetch_candles(symbol, limit=100):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT timestamp, open, high, low, "
                    "close, volume FROM candles "
                    "WHERE symbol = %s "
                    "AND timeframe = '1h' "
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


# ------------------------------------------------------------
# ТЕКСТ ОТЧЁТА
# ------------------------------------------------------------
def build_report_text():
    now = datetime.now(timezone.utc)
    lines = []
    lines.append("☀️ ARGUS - отчёт рынка")
    lines.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    lines.append("")

    levels = load_json(DATA_DIR / "levels_analysis.json")
    patterns = load_json(DATA_DIR / "patterns_analysis.json")
    corr = load_json(DATA_DIR / "correlations.json")
    sentiment = load_json(DATA_DIR / "news_sentiment.json")

    # --- Цены и уровни ---
    lines.append("💰 Цены и уровни")
    if levels and levels.get("symbols"):
        for sym, d in levels["symbols"].items():
            name = sym.replace("USDT", "")
            price = d.get("current_price", 0)
            lines.append(name + ": " + fmt_price(price))
            sup = d.get("supports", [])
            if sup:
                s1 = sup[0]
                line = "  support " + fmt_price(s1["price"])
                line += " (" + str(s1.get("touches", 0))
                line += " касаний)"
                lines.append(line)
            res = d.get("resistances", [])
            if res:
                r1 = res[0]
                line = "  resist " + fmt_price(r1["price"])
                line += " (" + str(r1.get("touches", 0))
                line += " касаний)"
                lines.append(line)
    else:
        lines.append("нет данных")
    lines.append("")

    # --- Паттерны ---
    lines.append("🧩 Паттерны")
    if patterns and patterns.get("symbols"):
        for sym, d in patterns["symbols"].items():
            name = sym.replace("USDT", "")
            up = d.get("up_ratio", 0) * 100
            mk = d.get("markov", {})
            p10 = mk.get("p_1_given_0", 0)
            p11 = mk.get("p_1_given_1", 0)
            lines.append(name + ":")
            lines.append(
                "  " + format(up, ".0f") + "% up"
            )
            lines.append(
                "  P(1|0)=" + format(p10, ".2f")
                + "  P(1|1)=" + format(p11, ".2f")
            )
    else:
        lines.append("нет данных")
    lines.append("")

    # --- Сценарий ---
    lines.append("🎯 Сценарий на день")
    if patterns and patterns.get("symbols"):
        for sym, d in patterns["symbols"].items():
            name = sym.replace("USDT", "")
            mk = d.get("markov", {})
            p10 = mk.get("p_1_given_0", 0)
            if p10 > 0.58:
                mood = "бычий (mean reversion)"
            elif p10 < 0.42:
                mood = "медвежий (momentum)"
            else:
                mood = "нейтральный"
            lines.append(name + ": " + mood)
    lines.append("")

    # --- Закономерности ---
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
            lines.append("🧠 Закономерности")
            for r in rules[:3]:
                arrow = "up" if r["direction"] == "up" else "down"
                line = "  " + r["symbol"] + ": "
                line += r["rule"]
                line += " -> " + arrow
                line += " (" + format(r["confidence"] * 100, ".0f")
                line += "%, N=" + str(r["samples"]) + ")"
                lines.append(line)
            lines.append("")

    # --- Новости ---
    if sentiment and sentiment.get("total_news", 0) > 0:
        lines.append("📰 Новости")
        lines.append("  Всего: " + str(sentiment.get("total_news", 0)))
        lines.append("  Настроение: " + str(sentiment.get("mood", "?")))
        lines.append(
            "  " + format(sentiment.get("avg_sentiment", 0), "+.3f")
        )
        lines.append("")

        top_bull = sentiment.get("top_bullish", [])[:2]
        if top_bull:
            lines.append("  Top bullish:")
            for n in top_bull:
                t = n.get("title", "")[:70]
                lines.append("   + " + t)
            lines.append("")

        top_bear = sentiment.get("top_bearish", [])[:2]
        if top_bear:
            lines.append("  Top bearish:")
            for n in top_bear:
                t = n.get("title", "")[:70]
                lines.append("   - " + t)
            lines.append("")

    lines.append("📊 Графики ниже")

    return "\n".join(lines)


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
def main():
    print("Morning report - start")

    # 1. Текст
    text = build_report_text()
    send_message(text)
    print("text sent")

    # 2. Данные для графиков
    levels = load_json(DATA_DIR / "levels_analysis.json")
    patterns = load_json(DATA_DIR / "patterns_analysis.json")

    pairs = [
        ("BTCUSDT", "BTC", "btc"),
        ("ETHUSDT", "ETH", "eth"),
    ]

    for symbol, name, prefix in pairs:
        print("--- " + symbol)

        # Свечи
        candles = fetch_candles(symbol, 100)
        if candles:
            sym_lvl = levels.get("symbols", {}).get(symbol, {})
            sup = sym_lvl.get("supports", [])
            res = sym_lvl.get("resistances", [])
            path = TMP_DIR / (prefix + "_candles.png")
            plot_candles(
                symbol, candles,
                supports=sup, resistances=res,
                output_path=str(path),
                title=name,
            )

            # Caption
            first = candles[0]["close"]
            last = candles[-1]["close"]
            ch = (last - first) / first * 100
            cap = "📊 " + name + " - 100h свечи\n"
            cap += fmt_price(last) + " "
            cap += "(" + format(ch, "+.2f") + "%)"
            send_photo(path, cap)

        # Паттерн
        sym_p = patterns.get("symbols", {}).get(symbol, {})
        binary = sym_p.get("binary_string", "")
        if binary:
            path = TMP_DIR / (prefix + "_pattern.png")
            plot_pattern(symbol, binary, output_path=str(path))
            up = sym_p.get("up_count", 0)
            dn = sym_p.get("down_count", 0)
            cap = "🧩 " + name + " - 50h pattern\n"
            cap += "up " + str(up) + " / down " + str(dn)
            send_photo(path, cap)

        # Markov
        mk = sym_p.get("markov", {})
        if mk:
            path = TMP_DIR / (prefix + "_markov.png")
            plot_markov(symbol, mk, output_path=str(path))
            p10 = mk.get("p_1_given_0", 0)
            p11 = mk.get("p_1_given_1", 0)
            cap = "🧠 " + name + " - Markov\n"
            cap += "P(1|0)=" + format(p10, ".2f")
            cap += "  P(1|1)=" + format(p11, ".2f")
            send_photo(path, cap)

    close_connection()
    print("Morning report - done")


if __name__ == "__main__":
    main()