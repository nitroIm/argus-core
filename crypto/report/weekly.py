# ============================================================
# ARGUS-Trader — НЕДЕЛЬНЫЙ ОТЧЁТ ПО КРИПТО (v2)
# ------------------------------------------------------------
# v2: fix статистики сбора — считает прогоны, а не записи.
#     pipeline пишет 1 запись на прогон, отчёт это учитывает.
#     + добавлено: сколько строк добавлено за неделю.
# ------------------------------------------------------------
# v1: начальная версия
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

from db import get_connection, close_connection

SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def send_telegram(text: str, silent: bool = False) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID — вывод в консоль")
        clean = (text
                 .replace("<b>", "").replace("</b>", "")
                 .replace("<i>", "").replace("</i>", "")
                 .replace("<code>", "").replace("</code>", ""))
        print(clean)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": silent,
            },
            timeout=15,
        )
        if r.status_code == 200:
            print("📤 Отчёт отправлен")
            return True
        print(f"⚠️ Telegram {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"⚠️ Telegram ошибка: {e}")
        return False


def fetch_stats() -> dict:
    stats = {
        "candles": {"total": 0, "btc_1h": 0, "eth_1h": 0, "oldest": None, "latest": None},
        "funding_rates": {"total": 0},
        "open_interest": {"total": 0},
        "long_short_ratio": {"total": 0},
        "taker_flow": {"total": 0},
        "market_context": {"total": 0},
        "cross_check": {"total": 0, "anomalies": 0},
        "anomaly_log": {"total": 0, "week": 0},
        "collect_week": {
            "runs": 0, "ok": 0, "partial": 0, "fail": 0,
            "rows_added": 0,
        },
        "collect_total": {"runs": 0, "rows_added": 0},
    }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM candles")
                stats["candles"]["total"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM candles WHERE symbol = 'BTCUSDT' AND timeframe = '1h'")
                stats["candles"]["btc_1h"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM candles WHERE symbol = 'ETHUSDT' AND timeframe = '1h'")
                stats["candles"]["eth_1h"] = cur.fetchone()[0]

                cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM candles")
                row = cur.fetchone()
                if row:
                    stats["candles"]["oldest"] = row[0]
                    stats["candles"]["latest"] = row[1]

                for table in ["funding_rates", "open_interest", "long_short_ratio",
                              "taker_flow", "market_context"]:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[table]["total"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM cross_check")
                stats["cross_check"]["total"] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM cross_check WHERE is_anomaly = TRUE")
                stats["cross_check"]["anomalies"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM anomaly_log")
                stats["anomaly_log"]["total"] = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM anomaly_log WHERE created_at > NOW() - INTERVAL '7 days'"
                )
                stats["anomaly_log"]["week"] = cur.fetchone()[0]

                # --- Прогоны pipeline за неделю (группировка по job_name) ---
                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%') AS runs,
                        COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%' AND status = 'ok') AS ok,
                        COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%' AND status = 'partial') AS partial,
                        COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%' AND status = 'fail') AS fail,
                        COALESCE(SUM(records_added) FILTER (WHERE job_name LIKE 'pipeline_%'), 0) AS rows_added
                    FROM collect_log
                    WHERE started_at > NOW() - INTERVAL '7 days'
                """)
                row = cur.fetchone()
                if row:
                    stats["collect_week"]["runs"] = row[0] or 0
                    stats["collect_week"]["ok"] = row[1] or 0
                    stats["collect_week"]["partial"] = row[2] or 0
                    stats["collect_week"]["fail"] = row[3] or 0
                    stats["collect_week"]["rows_added"] = row[4] or 0

                # --- Всего за всё время ---
                cur.execute("""
                    SELECT
                        COUNT(*) FILTER (WHERE job_name LIKE 'pipeline_%') AS runs,
                        COALESCE(SUM(records_added) FILTER (WHERE job_name LIKE 'pipeline_%'), 0) AS rows_added
                    FROM collect_log
                """)
                row = cur.fetchone()
                if row:
                    stats["collect_total"]["runs"] = row[0] or 0
                    stats["collect_total"]["rows_added"] = row[1] or 0

    except Exception as e:
        print(f"⚠️ Ошибка чтения из БД: {e}")

    return stats


def format_age(dt) -> str:
    if not dt:
        return "?"
    try:
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = now - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 60:
            return f"{minutes}м назад"
        elif minutes < 1440:
            return f"{minutes // 60}ч назад"
        else:
            return f"{minutes // 1440}д назад"
    except Exception:
        return str(dt)[:16]


def build_report() -> str:
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    lines = []
    lines.append("🪙 <b>ARGUS-Trader — недельный отчёт</b>")
    lines.append(f"📅 {now.strftime('%d.%m.%Y %H:%M')} (UTC)")
    lines.append(f"<i>Период: {week_ago.strftime('%d.%m')} — {now.strftime('%d.%m')}</i>")
    lines.append("")

    stats = fetch_stats()
    c = stats["candles"]

    # --- Данные в БД ---
    lines.append("📊 <b>Данные в БД</b>")
    if c["total"] > 0:
        lines.append(f"  Свечей: <b>{c['total']:,}</b>")
        lines.append(f"    BTC 1h: {c['btc_1h']:,} | ETH 1h: {c['eth_1h']:,}")
        if c["oldest"] and c["latest"]:
            oldest = c["oldest"].strftime('%d.%m.%Y') if hasattr(c["oldest"], "strftime") else str(c["oldest"])[:10]
            lines.append(f"    {oldest} → {format_age(c['latest'])}")
    else:
        lines.append("  ⚠️ Свечей нет — cron не работает?")
    lines.append("")

    # --- Деривативы ---
    lines.append("💹 <b>Деривативы</b>")
    lines.append(f"  Funding: {stats['funding_rates']['total']:,}")
    lines.append(f"  Open Interest: {stats['open_interest']['total']:,}")
    lines.append(f"  Long/Short: {stats['long_short_ratio']['total']:,}")
    lines.append(f"  Taker: {stats['taker_flow']['total']:,}")
    lines.append("")

    # --- Сбор за неделю (правильно) ---
    cw = stats["collect_week"]
    if cw["runs"] > 0:
        lines.append("⚙️ <b>Сбор за неделю</b>")
        lines.append(f"  Прогонов: {cw['runs']}")
        lines.append(f"  Успешных: {cw['ok']} ✅")
        if cw["partial"] > 0:
            lines.append(f"  Частичных: {cw['partial']} ⚠️")
        if cw["fail"] > 0:
            lines.append(f"  Сбоев: {cw['fail']} ❌")
        lines.append(f"  Добавлено строк: {cw['rows_added']:,}")
        lines.append("")
    else:
        lines.append("⚙️ <b>Сбор за неделю:</b> нет записей")
        lines.append("")

    # --- Всего за всё время ---
    ct = stats["collect_total"]
    if ct["runs"] > 0:
        lines.append("📈 <b>Сбор всего</b>")
        lines.append(f"  Прогонов: {ct['runs']}")
        lines.append(f"  Строк добавлено: {ct['rows_added']:,}")
        lines.append("")

    # --- Аномалии ---
    anom = stats["anomaly_log"]
    cc = stats["cross_check"]
    if anom["total"] > 0 or cc["anomalies"] > 0:
        lines.append("🚨 <b>Аномалии</b>")
        lines.append(f"  Cross-check: {cc['anomalies']} из {cc['total']}")
        lines.append(f"  За неделю: {anom['week']}")
        lines.append("")
    else:
        lines.append("🚨 <b>Аномалии:</b> не обнаружено ✅")
        lines.append("")

    # --- Новости ---
    sentiment = load_json(SENTIMENT_FILE, {})
    if sentiment and sentiment.get("total_news", 0) > 0:
        lines.append("📰 <b>Новости рынка</b>")
        lines.append(f"  Всего: {sentiment.get('total_news', 0)}")
        lines.append(f"  Настроение: {sentiment.get('mood', '?')}")
        lines.append(f"  Сентимент: {sentiment.get('avg_sentiment', 0):+.3f}")
        lines.append(
            f"  🟢 {sentiment.get('bullish_count', 0)} | "
            f"🔴 {sentiment.get('bearish_count', 0)} | "
            f"🟡 {sentiment.get('neutral_count', 0)}"
        )

        top_bull = sentiment.get("top_bullish", [])[:2]
        top_bear = sentiment.get("top_bearish", [])[:2]

        if top_bull:
            lines.append("")
            lines.append("  🟢 Топ бычьих:")
            for n in top_bull:
                title = n.get("title", "")[:80]
                lines.append(f"    • {title}")
        if top_bear:
            lines.append("")
            lines.append("  🔴 Топ медвежьих:")
            for n in top_bear:
                title = n.get("title", "")[:80]
                lines.append(f"    • {title}")
        lines.append("")

    lines.append("🔧 <i>ARGUS-Trader работает стабильно.</i>")

    return "\n".join(lines)


def main():
    print("🪙 ARGUS-Trader weekly report v2")
    print("=" * 50)

    try:
        message = build_report()
        if len(message) > 4000:
            message = message[:3950] + "\n\n<i>... (обрезано)</i>"
        send_telegram(message)
    except Exception as e:
        import traceback
        traceback.print_exc()
        send_telegram(f"❌ <b>ARGUS-Trader:</b> ошибка отчёта\n<code>{str(e)[:200]}</code>")
        sys.exit(1)
    finally:
        close_connection()

    print("=" * 50)


if __name__ == "__main__":
    main()