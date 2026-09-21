# ============================================================
# ARGUS-Trader — НЕДЕЛЬНЫЙ ОТЧЁТ v4
# ------------------------------------------------------------
# v4: + графики (свечи, паттерн, Markov) через charts.py
#     Отправляет текст + 3 картинки через sendPhoto.
# ------------------------------------------------------------
# v3: события, паттерны, корреляции, уровни
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection
from db import close_connection

# Опциональный импорт charts
try:
    from report.charts import plot_candles
    from report.charts import plot_pattern
    from report.charts import plot_markov
    HAS_CHARTS = True
except Exception as e:
    HAS_CHARTS = False
    print("charts недоступен: " + str(e))

SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"
PATTERNS_FILE = DATA_DIR / "patterns_analysis.json"
LEVELS_FILE = DATA_DIR / "levels_analysis.json"
CORRELATIONS_FILE = DATA_DIR / "correlations.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("Нет токена/чата — вывод в консоль")
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
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if r.status_code == 200:
            print("message sent")
            return True
        print("Telegram " + str(r.status_code))
        return False
    except Exception as e:
        print("Telegram error: " + str(e))
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
                data["caption"] = caption
                data["parse_mode"] = "HTML"
            r = requests.post(url, data=data, files=files, timeout=30)
        if r.status_code == 200:
            print("photo sent: " + path)
            return True
        print("sendPhoto " + str(r.status_code))
        return False
    except Exception as e:
        print("sendPhoto error: " + str(e))
        return False


def fetch_stats():
    stats = {
        "candles": {"total": 0, "btc_1h": 0, "eth_1h": 0},
        "funding_rates": {"total": 0},
        "open_interest": {"total": 0},
        "long_short_ratio": {"total": 0},
        "taker_flow": {"total": 0},
        "features_hourly": {"total": 0},
        "price_patterns": {"total": 0},
        "events": {"total": 0, "week": 0, "by_type": {}},
        "causal_links": {"total": 0},
        "anomaly_log": {"total": 0, "week": 0},
        "collect_week": {"runs": 0, "ok": 0, "fail": 0, "rows": 0},
    }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM candles")
                stats["candles"]["total"] = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM candles "
                    "WHERE symbol='BTCUSDT' AND timeframe='1h'"
                )
                stats["candles"]["btc_1h"] = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM candles "
                    "WHERE symbol='ETHUSDT' AND timeframe='1h'"
                )
                stats["candles"]["eth_1h"] = cur.fetchone()[0]

                for t in ["funding_rates", "open_interest",
                          "long_short_ratio", "taker_flow",
                          "features_hourly", "price_patterns",
                          "events", "causal_links"]:
                    cur.execute("SELECT COUNT(*) FROM " + t)
                    stats[t]["total"] = cur.fetchone()[0]

                sql = (
                    "SELECT COUNT(*) FROM events "
                    "WHERE created_at > NOW() - INTERVAL '7 days'"
                )
                cur.execute(sql)
                stats["events"]["week"] = cur.fetchone()[0] or 0

                sql = (
                    "SELECT event_type, COUNT(*) FROM events "
                    "WHERE created_at > NOW() - INTERVAL '7 days' "
                    "GROUP BY event_type ORDER BY 2 DESC LIMIT 5"
                )
                cur.execute(sql)
                stats["events"]["by_type"] = {
                    r[0]: r[1] for r in cur.fetchall()
                }

                sql = (
                    "SELECT COUNT(*) FROM anomaly_log "
                    "WHERE created_at > NOW() - INTERVAL '7 days'"
                )
                cur.execute(sql)
                stats["anomaly_log"]["week"] = cur.fetchone()[0] or 0

                sql = (
                    "SELECT COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%'), "
                    "COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%' "
                    "AND status = 'ok'), "
                    "COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%' "
                    "AND status = 'fail'), "
                    "COALESCE(SUM(records_added) FILTER "
                    "(WHERE job_name LIKE 'pipeline_%'), 0) "
                    "FROM collect_log "
                    "WHERE started_at > NOW() - INTERVAL '7 days'"
                )
                cur.execute(sql)
                row = cur.fetchone()
                if row:
                    stats["collect_week"]["runs"] = row[0] or 0
                    stats["collect_week"]["ok"] = row[1] or 0
                    stats["collect_week"]["fail"] = row[2] or 0
                    stats["collect_week"]["rows"] = row[3] or 0

    except Exception as e:
        print("db error: " + str(e))

    return stats


def fetch_candles_for_chart(symbol, limit=100):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT timestamp, open, high, low, close, volume "
                    "FROM candles WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "ORDER BY timestamp DESC LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
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
        print("fetch_candles: " + str(e))
        return []


def build_report():
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    lines = []
    lines.append("🪙 <b>ARGUS-Trader — недельный отчёт v4</b>")
    lines.append(f"📅 {now.strftime('%d.%m.%Y %H:%M')} UTC")
    lines.append(f"<i>{week_ago.strftime('%d.%m')} — "
                 f"{now.strftime('%d.%m')}</i>")
    lines.append("")

    stats = fetch_stats()
    c = stats["candles"]

    lines.append("📊 <b>Данные в БД</b>")
    lines.append(f"  Свечей: <b>{c['total']:,}</b> "
                 f"(BTC {c['btc_1h']} | ETH {c['eth_1h']})")
    lines.append(f"  Features: {stats['features_hourly']['total']:,}")
    lines.append(f"  Patterns: {stats['price_patterns']['total']:,}")
    lines.append(f"  Events: {stats['events']['total']:,}")
    lines.append(f"  Causal: {stats['causal_links']['total']:,}")
    lines.append("")

    cw = stats["collect_week"]
    if cw["runs"] > 0:
        lines.append("⚙️ <b>Сбор за неделю</b>")
        lines.append(f"  Прогонов: {cw['runs']} | "
                     f"Успешных: {cw['ok']}")
        if cw["fail"] > 0:
            lines.append(f"  ⚠️ Сбоев: {cw['fail']}")
        lines.append(f"  Добавлено строк: {cw['rows']:,}")
        lines.append("")

    ev = stats["events"]
    if ev["week"] > 0:
        lines.append(f"⚡ <b>События: {ev['week']}</b>")
        for t, n in list(ev["by_type"].items())[:4]:
            lines.append(f"  • {t}: {n}")
        lines.append("")

    # Паттерны
    patterns = load_json(PATTERNS_FILE, {})
    if patterns and patterns.get("symbols"):
        lines.append("🧩 <b>Паттерны</b>")
        for sym, data in patterns["symbols"].items():
            name = sym.replace("USDT", "")
            mk = data.get("markov", {})
            up = data.get("up_ratio", 0) * 100
            lines.append(f"  <b>{name}</b>: {up:.1f}% роста")
            p11 = mk.get("p_1_given_1", 0)
            p10 = mk.get("p_1_given_0", 0)
            lines.append(f"    P(1|1)={p11:.2f} | P(1|0)={p10:.2f}")
            top = data.get("ngrams_top", [])[:2]
            for item in top:
                ng = item["ngram"]
                p_up = item["p_up"] * 100
                n = item["count"]
                lines.append(f"    `{ng}` → ↑ {p_up:.0f}% (N={n})")
        lines.append("")

    # Корреляции
    corr = load_json(CORRELATIONS_FILE, {})
    if corr and corr.get("symbols"):
        all_rules = []
        for sym, data in corr["symbols"].items():
            for rule in data.get("rules", []):
                r = dict(rule)
                r["symbol"] = sym.replace("USDT", "")
                all_rules.append(r)
        if all_rules:
            all_rules.sort(
                key=lambda x: (x["confidence"], x["samples"]),
                reverse=True,
            )
            lines.append("🧠 <b>Закономерности</b>")
            for r in all_rules[:5]:
                d = r["direction"]
                arrow = "↑" if d == "up" else "↓"
                conf = r["confidence"] * 100
                lines.append(
                    f"  {arrow} [{r['symbol']}] {r['rule']}\n"
                    f"     <i>{conf:.0f}% (N={r['samples']})</i>"
                )
            lines.append("")

    # Уровни
    levels = load_json(LEVELS_FILE, {})
    if levels and levels.get("symbols"):
        lines.append("📍 <b>Уровни</b>")
        for sym, data in levels["symbols"].items():
            name = sym.replace("USDT", "")
            price = data.get("current_price", 0)
            sup = data.get("supports", [])
            res = data.get("resistances", [])
            if price >= 1000:
                price_str = f"${int(price):,}"
            else:
                price_str = f"${price:,.2f}"
            lines.append(f"  <b>{name}</b>: {price_str}")
            if sup:
                s = sup[0]
                if s["price"] >= 1000:
                    sp = f"${int(s['price']):,}"
                else:
                    sp = f"${s['price']:,.2f}"
                lines.append(f"    🛡 Support: {sp} "
                             f"({-s['distance_pct']:.2f}%)")
            if res:
                r = res[0]
                if r["price"] >= 1000:
                    rp = f"${int(r['price']):,}"
                else:
                    rp = f"${r['price']:,.2f}"
                lines.append(f"    ⚔️ Resist: {rp} "
                             f"(+{r['distance_pct']:.2f}%)")
        lines.append("")

    # Аномалии
    anom = stats["anomaly_log"]
    if anom["week"] > 0:
        lines.append(f"🚨 <b>Аномалии: {anom['week']}</b>")
    else:
        lines.append("🚨 <b>Аномалии:</b> не обнаружено ✅")
    lines.append("")

    # Новости
    sent = load_json(SENTIMENT_FILE, {})
    if sent and sent.get("total_news", 0) > 0:
        lines.append("📰 <b>Новости</b>")
        lines.append(f"  Всего: {sent.get('total_news', 0)}")
        lines.append(f"  Настроение: {sent.get('mood', '?')}")
        lines.append("")

    lines.append("🔧 <i>ARGUS-Trader работает стабильно.</i>")

    return "\n".join(lines)


def main():
    print("ARGUS-Trader weekly report v4")
    print("=" * 50)

    try:
        message = build_report()
        if len(message) > 4000:
            message = message[:3950] + "\n\n<i>... (обрезано)</i>"
        send_message(message)

        # Графики
        if HAS_CHARTS:
            print("Генерирую графики...")

            patterns = load_json(PATTERNS_FILE, {})
            levels = load_json(LEVELS_FILE, {})

            for symbol in ["BTCUSDT", "ETHUSDT"]:
                name = symbol.replace("USDT", "")

                # 1. Свечной график
                candles = fetch_candles_for_chart(symbol, limit=100)
                if candles:
                    sym_levels = levels.get("symbols", {}).get(symbol, {})
                    supports = sym_levels.get("supports", [])
                    resistances = sym_levels.get("resistances", [])
                    path = plot_candles(
                        symbol, candles,
                        supports=supports,
                        resistances=resistances,
                    )
                    if path:
                        send_photo(path, f"📊 {name} — 100h")

                # 2. Паттерн 0/1
                sym_patterns = patterns.get("symbols", {}).get(symbol, {})
                binary = sym_patterns.get("binary_string", "")
                if binary:
                    path = plot_pattern(symbol, binary)
                    if path:
                        send_photo(path, f"🧩 {name} — pattern")

                # 3. Markov
                markov = sym_patterns.get("markov", {})
                if markov:
                    path = plot_markov(symbol, markov)
                    if path:
                        send_photo(path, f"🧠 {name} — Markov")

    except Exception as e:
        import traceback
        traceback.print_exc()
        send_message(
            "❌ <b>ARGUS-Trader</b> ошибка отчёта\n"
            f"<code>{str(e)[:200]}</code>"
        )
        sys.exit(1)
    finally:
        close_connection()

    print("=" * 50)


if __name__ == "__main__":
    main()