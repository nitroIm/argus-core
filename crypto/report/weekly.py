# ============================================================
# ARGUS-Trader — НЕДЕЛЬНЫЙ ОТЧЁТ ПО КРИПТО (v1)
# ------------------------------------------------------------
# Читает данные из Supabase + сентимент новостей из JSON.
# Отправляет сводку в Telegram одним сообщением.
# Запускается по cron раз в неделю или вручную.
# ------------------------------------------------------------
# v1: начальная версия
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent        # crypto/report/
CRYPTO_ROOT = SCRIPT_DIR.parent                     # crypto/
DATA_DIR = CRYPTO_ROOT / "data"                     # crypto/data/
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection, close_connection

# ============================================================
# КОНСТАНТЫ
# ============================================================
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


# ============================================================
# СБОР ДАННЫХ ИЗ SUPABASE
# ============================================================
def fetch_stats() -> dict:
    """Собирает статистику по всем таблицам."""
    stats = {
        "candles": {"total": 0, "btc_1h": 0, "eth_1h": 0, "oldest": None, "latest": None},
        "funding_rates": {"total": 0, "latest": None},
        "open_interest": {"total": 0, "latest": None},
        "long_short_ratio": {"total": 0, "latest": None},
        "taker_flow": {"total": 0, "latest": None},
        "market_context": {"total": 0, "latest": None},
        "cross_check": {"total": 0, "anomalies": 0},
        "anomaly_log": {"total": 0, "week": 0},
        "collect_log_week": {"total": 0, "ok": 0, "fail": 0},
    }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # --- Candles ---
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

                # --- Остальные метрики ---
                for table in ["funding_rates", "open_interest", "long_short_ratio",
                              "taker_flow", "market_context"]:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    stats[table]["total"] = cur.fetchone()[0]
                    cur.execute(f"SELECT MAX(timestamp) FROM {table}")
                    stats[table]["latest"] = cur.fetchone()[0]

                # --- Cross-check ---
                cur.execute("SELECT COUNT(*) FROM cross_check")
                stats["cross_check"]["total"] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM cross_check WHERE is_anomaly = TRUE")
                stats["cross_check"]["anomalies"] = cur.fetchone()[0]

                # --- Anomalies ---
                cur.execute("SELECT COUNT(*) FROM anomaly_log")
                stats["anomaly_log"]["total"] = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM anomaly_log WHERE created_at > NOW() - INTERVAL '7 days'"
                )
                stats["anomaly_log"]["week"] = cur.fetchone()[0]

                # --- Collect log за неделю ---
                cur.execute(
                    "SELECT COUNT(*), "
                    "SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) "
                    "FROM collect_log WHERE started_at > NOW() - INTERVAL '7 days'"
                )
                row = cur.fetchone()
                if row:
                    stats["collect_log_week"]["total"] = row[0] or 0
                    stats["collect_log_week"]["ok"] = row[1] or 0
                    stats["collect_log_week"]["fail"] = row[2] or 0

    except Exception as e:
        print(f"⚠️ Ошибка чтения из БД: {e}")

    return stats


# ============================================================
# ФОРМИРОВАНИЕ ОТЧЁТА
# ============================================================
def format_age(dt) -> str:
    """Превращает datetime в 'X минут назад'."""
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

    # --- Данные из БД ---
    stats = fetch_stats()
    c = stats["candles"]

    lines.append("📊 <b>Данные в БД</b>")
    if c["total"] > 0:
        lines.append(f"  Свечей: <b>{c['total']:,}</b>")
        lines.append(f"    BTC 1h: {c['btc_1h']:,} | ETH 1h: {c['eth_1h']:,}")
        if c["oldest"] and c["latest"]:
            oldest = c["oldest"].strftime('%d.%m.%Y') if hasattr(c["oldest"], "strftime") else str(c["oldest"])[:10]
            lines.append(f"    Диапазон: {oldest} → {format_age(c['latest'])}")
    else:
        lines.append("  ⚠️ Свечей нет — cron не работает?")
    lines.append("")

    lines.append("💹 <b>Деривативы</b>")
    lines.append(f"  Funding: {stats['funding_rates']['total']:,}")
    lines.append(f"  Open Interest: {stats['open_interest']['total']:,}")
    lines.append(f"  Long/Short: {stats['long_short_ratio']['total']:,}")
    lines.append(f"  Taker: {stats['taker_flow']['total']:,}")
    lines.append("")

    # --- Сбор за неделю ---
    cl = stats["collect_log_week"]
    if cl["total"] > 0:
        success_rate = round(cl["ok"] / cl["total"] * 100, 1)
        lines.append("⚙️ <b>Сбор за неделю</b>")
        lines.append(f"  Запусков: {cl['total']}")
        lines.append(f"  Успешно: {cl['ok']} ({success_rate}%)")
        if cl["fail"] > 0:
            lines.append(f"  Сбоев: {cl['fail']} ⚠️")
        lines.append("")

    # --- Аномалии ---
    anom = stats["anomaly_log"]
    cc = stats["cross_check"]
    if anom["total"] > 0 or cc["anomalies"] > 0:
        lines.append("🚨 <b>Аномалии</b>")
        lines.append(f"  Cross-check: {cc['anomalies']} из {cc['total']}")
        lines.append(f"  Алертов за неделю: {anom['week']}")
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

        # Топ новости
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


# ============================================================
# MAIN
# ============================================================
def main():
    print("🪙 ARGUS-Trader weekly report")
    print("=" * 50)

    try:
        message = build_report()

        # Проверка размера (Telegram лимит 4096)
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