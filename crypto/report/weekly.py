# ============================================================
# ARGUS-Trader — НЕДЕЛЬНЫЙ ОТЧЁТ v5
# ------------------------------------------------------------
# v5: осмысленные подписи к графикам:
#     - свечи: цена, %, support, resistance
#     - паттерн: up/down, P(1|0), топ n-грамма
#     - Markov: P(1|1), P(1|0), интерпретация
# ------------------------------------------------------------
# v4: графики через charts.py
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

try:
    from report.charts import plot_candles
    from report.charts import plot_pattern
    from report.charts import plot_markov
    HAS_CHARTS = True
except Exception as e:
    HAS_CHARTS = False
    print("charts: " + str(e))

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


def fmt_price(p):
    if p >= 1000:
        return f"${int(p):,}"
    if p >= 1:
        return f"${p:,.2f}"
    return f"${p:.4f}"


def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("no token — console only")
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
        print("tg " + str(r.status_code))
        return False
    except Exception as e:
        print("tg error: " + str(e))
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
            r = requests.post(
                url, data=data, files=files, timeout=30,
            )
        if r.status_code == 200:
            print("photo: " + path)
            return True
        print("sendPhoto " + str(r.status_code))
        return False
    except Exception as e:
        print("sendPhoto: " + str(e))
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
        "collect_week": {
            "runs": 0, "ok": 0, "fail": 0, "rows": 0,
        },
    }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM candles")
                stats["candles"]["total"] = cur.fetchone()[0]

                sql = (
                    "SELECT COUNT(*) FROM candles "
                    "WHERE symbol='BTCUSDT' "
                    "AND timeframe='1h'"
                )
                cur.execute(sql)
                stats["candles"]["btc_1h"] = cur.fetchone()[0]

                sql = (
                    "SELECT COUNT(*) FROM candles "
                    "WHERE symbol='ETHUSDT' "
                    "AND timeframe='1h'"
                )
                cur.execute(sql)
                stats["candles"]["eth_1h"] = cur.fetchone()[0]

                tables = [
                    "funding_rates", "open_interest",
                    "long_short_ratio", "taker_flow",
                    "features_hourly", "price_patterns",
                    "events", "causal_links",
                ]
                for t in tables:
                    cur.execute("SELECT COUNT(*) FROM " + t)
                    stats[t]["total"] = cur.fetchone()[0]

                sql = (
                    "SELECT COUNT(*) FROM events "
                    "WHERE created_at > "
                    "NOW() - INTERVAL '7 days'"
                )
                cur.execute(sql)
                stats["events"]["week"] = cur.fetchone()[0] or 0

                sql = (
                    "SELECT event_type, COUNT(*) FROM events "
                    "WHERE created_at > "
                    "NOW() - INTERVAL '7 days' "
                    "GROUP BY event_type "
                    "ORDER BY 2 DESC LIMIT 5"
                )
                cur.execute(sql)
                stats["events"]["by_type"] = {
                    r[0]: r[1] for r in cur.fetchall()
                }

                sql = (
                    "SELECT COUNT(*) FROM anomaly_log "
                    "WHERE created_at > "
                    "NOW() - INTERVAL '7 days'"
                )
                cur.execute(sql)
                stats["anomaly_log"]["week"] = cur.fetchone()[0] or 0

                sql = (
                    "SELECT "
                    "COUNT(*) FILTER "
                    "(WHERE job_name LIKE 'pipeline_%'), "
                    "COUNT(*) FILTER "
                    "(WHERE job_name LIKE 'pipeline_%' "
                    "AND status='ok'), "
                    "COUNT(*) FILTER "
                    "(WHERE job_name LIKE 'pipeline_%' "
                    "AND status='fail'), "
                    "COALESCE(SUM(records_added) FILTER "
                    "(WHERE job_name LIKE 'pipeline_%'), 0) "
                    "FROM collect_log "
                    "WHERE started_at > "
                    "NOW() - INTERVAL '7 days'"
                )
                cur.execute(sql)
                row = cur.fetchone()
                if row:
                    stats["collect_week"]["runs"] = row[0] or 0
                    stats["collect_week"]["ok"] = row[1] or 0
                    stats["collect_week"]["fail"] = row[2] or 0
                    stats["collect_week"]["rows"] = row[3] or 0

    except Exception as e:
        print("db: " + str(e))

    return stats


def fetch_candles_for_chart(symbol, limit=100):
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


def build_report():
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    lines = []
    lines.append("🪙 <b>ARGUS-Trader — недельный v5</b>")
    lines.append(f"📅 {now.strftime('%d.%m.%Y %H:%M')} UTC")
    lines.append(
        f"<i>{week_ago.strftime('%d.%m')} — "
        f"{now.strftime('%d.%m')}</i>"
    )
    lines.append("")

    stats = fetch_stats()
    c = stats["candles"]

    lines.append("📊 <b>Данные в БД</b>")
    lines.append(
        f"  Свечей: <b>{c['total']:,}</b> "
        f"(BTC {c['btc_1h']} | ETH {c['eth_1h']})"
    )
    lines.append(
        f"  Features: "
        f"{stats['features_hourly']['total']:,}"
    )
    lines.append(
        f"  Patterns: "
        f"{stats['price_patterns']['total']:,}"
    )
    lines.append(f"  Events: {stats['events']['total']:,}")
    lines.append(
        f"  Causal: {stats['causal_links']['total']:,}"
    )
    lines.append("")

    cw = stats["collect_week"]
    if cw["runs"] > 0:
        lines.append("⚙️ <b>Сбор за неделю</b>")
        lines.append(
            f"  Прогонов: {cw['runs']} | "
            f"OK: {cw['ok']}"
        )
        if cw["fail"] > 0:
            lines.append(f"  ⚠️ Сбоев: {cw['fail']}")
        lines.append(f"  Строк: {cw['rows']:,}")
        lines.append("")

    ev = stats["events"]
    if ev["week"] > 0:
        lines.append(f"⚡ <b>События: {ev['week']}</b>")
        for t, n in list(ev["by_type"].items())[:4]:
            lines.append(f"  • {t}: {n}")
        lines.append("")

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
            lines.append(
                f"    P(1|1)={p11:.2f} | "
                f"P(1|0)={p10:.2f}"
            )
            top = data.get("ngrams_top", [])[:2]
            for item in top:
                ng = item["ngram"]
                p_up = item["p_up"] * 100
                n = item["count"]
                lines.append(
                    f"    `{ng}` → ↑ {p_up:.0f}% (N={n})"
                )
        lines.append("")

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
                key=lambda x: (
                    x["confidence"], x["samples"]
                ),
                reverse=True,
            )
            lines.append("🧠 <b>Закономерности</b>")
            for r in all_rules[:5]:
                d = r["direction"]
                arrow = "↑" if d == "up" else "↓"
                conf = r["confidence"] * 100
                lines.append(
                    f"  {arrow} [{r['symbol']}] "
                    f"{r['rule']}\n"
                    f"     <i>{conf:.0f}% "
                    f"(N={r['samples']})</i>"
                )
            lines.append("")

    levels = load_json(LEVELS_FILE, {})
    if levels and levels.get("symbols"):
        lines.append("📍 <b>Уровни</b>")
        for sym, data in levels["symbols"].items():
            name = sym.replace("USDT", "")
            price = data.get("current_price", 0)
            sup = data.get("supports", [])
            res = data.get("resistances", [])
            lines.append(
                f"  <b>{name}</b>: {fmt_price(price)}"
            )
            if sup:
                s = sup[0]
                lines.append(
                    f"    🛡 {fmt_price(s['price'])} "
                    f"({-s['distance_pct']:.2f}%)"
                )
            if res:
                r = res[0]
                lines.append(
                    f"    ⚔️ {fmt_price(r['price'])} "
                    f"(+{r['distance_pct']:.2f}%)"
                )
        lines.append("")

    anom = stats["anomaly_log"]
    if anom["week"] > 0:
        lines.append(f"🚨 <b>Аномалии: {anom['week']}</b>")
    else:
        lines.append("🚨 <b>Аномалии:</b> нет ✅")
    lines.append("")

    sent = load_json(SENTIMENT_FILE, {})
    if sent and sent.get("total_news", 0) > 0:
        lines.append("📰 <b>Новости</b>")
        lines.append(
            f"  Всего: {sent.get('total_news', 0)}"
        )
        lines.append(
            f"  Настроение: {sent.get('mood', '?')}"
        )
        lines.append("")

    lines.append("🔧 <i>ARGUS-Trader стабилен.</i>")

    return "\n".join(lines)


def build_candle_caption(symbol, candles,
                         supports, resistances):
    name = symbol.replace("USDT", "")
    lines = [f"📊 <b>{name} — 100h</b>"]

    if candles:
        first = candles[0]["close"]
        last = candles[-1]["close"]
        ch = (last - first) / first * 100
        lines.append(
            f"💰 {fmt_price(last)} ({ch:+.2f}%)"
        )

        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        lines.append(
            f"⬆️ High: {fmt_price(max(highs))}"
        )
        lines.append(
            f"⬇️ Low: {fmt_price(min(lows))}"
        )

    if supports:
        s = supports[0]
        lines.append(
            f"🛡 Support: {fmt_price(s['price'])} "
            f"({-s['distance_pct']:.2f}%)"
        )
    if resistances:
        r = resistances[0]
        lines.append(
            f"⚔️ Resist: {fmt_price(r['price'])} "
            f"(+{r['distance_pct']:.2f}%)"
        )

    return "\n".join(lines)


def build_pattern_caption(symbol, data):
    name = symbol.replace("USDT", "")
    lines = [f"🧩 <b>{name} — 50h pattern</b>"]

    up = data.get("up_count", 0)
    down = data.get("down_count", 0)
    ratio = data.get("up_ratio", 0) * 100
    total = up + down

    lines.append(
        f"⬆️ {up} | ⬇️ {down} "
        f"({ratio:.0f}% up, N={total})"
    )
    lines.append(
        f"📈 Max streak up: "
        f"{data.get('max_streak_up', 0)}"
    )
    lines.append(
        f"📉 Max streak down: "
        f"{data.get('max_streak_down', 0)}"
    )

    top = data.get("ngrams_top", [])[:1]
    if top:
        t = top[0]
        p_up = t["p_up"] * 100
        lines.append(
            f"🔥 Top: `{t['ngram']}` → "
            f"↑ {p_up:.0f}% (N={t['count']})"
        )

    return "\n".join(lines)


def build_markov_caption(symbol, markov):
    name = symbol.replace("USDT", "")
    lines = [f"🧠 <b>{name} — Markov</b>"]

    p11 = markov.get("p_1_given_1", 0)
    p10 = markov.get("p_1_given_0", 0)
    p00 = markov.get("p_0_given_0", 0)
    p01 = markov.get("p_0_given_1", 0)

    lines.append(f"P(1|1) = {p11:.2f} — после роста")
    lines.append(f"P(1|0) = {p10:.2f} — после падения")
    lines.append(f"P(0|0) = {p00:.2f}")
    lines.append(f"P(0|1) = {p01:.2f}")
    lines.append("")

    if p10 > 0.55:
        lines.append("📌 Mean reversion: после падения — отскок")
    elif p10 < 0.45:
        lines.append("📌 Momentum: после падения — падение")
    else:
        lines.append("📌 Neutral: нет явной тенденции")

    return "\n".join(lines)


def main():
    print("ARGUS-Trader weekly v5")
    print("=" * 50)

    try:
        message = build_report()
        if len(message) > 4000:
            message = message[:3950] + "\n..."
        send_message(message)

        if not HAS_CHARTS:
            print("charts off")
            return

        print("generating charts...")

        patterns = load_json(PATTERNS_FILE, {})
        levels = load_json(LEVELS_FILE, {})

        for symbol in ["BTCUSDT", "ETHUSDT"]:
            print("--- " + symbol)

            # --- 1. Свечи ---
            candles = fetch_candles_for_chart(
                symbol, limit=100,
            )
            if candles:
                sym_levels = levels.get(
                    "symbols", {},
                ).get(symbol, {})
                supports = sym_levels.get(
                    "supports", [],
                )
                resistances = sym_levels.get(
                    "resistances", [],
                )

                path = plot_candles(
                    symbol, candles,
                    supports=supports,
                    resistances=resistances,
                )
                if path:
                    cap = build_candle_caption(
                        symbol, candles,
                        supports, resistances,
                    )
                    send_photo(path, cap)

            # --- 2. Паттерн ---
            sym_p = patterns.get(
                "symbols", {},
            ).get(symbol, {})
            binary = sym_p.get("binary_string", "")
            if binary:
                path = plot_pattern(symbol, binary)
                if path:
                    cap = build_pattern_caption(
                        symbol, sym_p,
                    )
                    send_photo(path, cap)

            # --- 3. Markov ---
            mk = sym_p.get("markov", {})
            if mk:
                path = plot_markov(symbol, mk)
                if path:
                    cap = build_markov_caption(
                        symbol, mk,
                    )
                    send_photo(path, cap)

    except Exception as e:
        import traceback
        traceback.print_exc()
        send_message(
            "❌ ARGUS ошибка:\n"
            f"<code>{str(e)[:200]}</code>"
        )
        sys.exit(1)
    finally:
        close_connection()

    print("=" * 50)


if __name__ == "__main__":
    main()