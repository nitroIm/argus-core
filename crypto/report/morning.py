# ============================================================
# ARGUS - УТРЕННИЙ ОТЧЁТ РЫНКА v2
# ------------------------------------------------------------
# v2: все графики в ОДНОМ сообщении (media group).
#     Раньше: 7 сообщений. Теперь: 2 (текст + альбом).
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone
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


def send_media_group(photos):
    """
    Отправляет все графики одним альбомом.
    photos: список (path, caption).
    Максимум 10.
    """
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
            print("album sent: " + str(len(media)) + " photos")
            return True
        print("album err: " + r.text[:200])
        return False
    except Exception as e:
        print("album: " + str(e))
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

    # Цены и уровни
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

    # Паттерны
    lines.append("🧩 Паттерны")
    if patterns and patterns.get("symbols"):
        for sym, d in patterns["symbols"].items():
            name = sym.replace("USDT", "")
            up = d.get("up_ratio", 0) * 100
            mk = d.get("markov", {})
            p10 = mk.get("p_1_given_0", 0)
            p11 = mk.get("p_1_given_1", 0)
            lines.append(name + ":")
            lines.append("  " + format(up, ".0f") + "% up")
            lines.append(
                "  P(1|0)=" + format(p10, ".2f")
                + "  P(1|1)=" + format(p11, ".2f")
            )
    else:
        lines.append("нет данных")
    lines.append("")

    # Сценарий
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

    # Закономерности
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

    # Новости
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

    lines.append("📊 Графики ниже одним альбомом")

    return "\n".join(lines)


def main():
    print("Morning report v2 - start")

    # 1. Текст
    text = build_report_text()
    send_message(text)
    print("text sent")

    # 2. Собираем все графики
    levels = load_json(DATA_DIR / "levels_analysis.json")
    patterns = load_json(DATA_DIR / "patterns_analysis.json")

    pairs = [
        ("BTCUSDT", "BTC", "btc"),
        ("ETHUSDT", "ETH", "eth"),
    ]

    photos = []

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
            photos.append((str(path), ""))

        # Паттерн
        sym_p = patterns.get("symbols", {}).get(symbol, {})
        binary = sym_p.get("binary_string", "")
        if binary:
            path = TMP_DIR / (prefix + "_pattern.png")
            plot_pattern(symbol, binary, output_path=str(path))
            photos.append((str(path), ""))

        # Markov
        mk = sym_p.get("markov", {})
        if mk:
            path = TMP_DIR / (prefix + "_markov.png")
            plot_markov(symbol, mk, output_path=str(path))
            photos.append((str(path), ""))

    # 3. Отправляем альбомом
    if photos:
        first_cap = "📊 Графики: BTC/ETH\nсвечи, паттерны, Markov"
        photos_with_cap = [(photos[0][0], first_cap)] + photos[1:]
        ok = send_media_group(photos_with_cap)
        print("album: " + str(ok))
    else:
        print("no charts")

    close_connection()
    print("Morning report - done")


if __name__ == "__main__":
    main()