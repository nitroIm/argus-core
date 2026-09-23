# ============================================================
# ARGUS-Trader — ВЕЧЕРНИЙ ТЕХОТЧЁТ v2.1 [PRODUCTION]
# ------------------------------------------------------------
# v2.1: активность за 24ч (события, аномалии).
#       Убрал events/anomaly из "свежести".
# v2.0: продакшн-стиль, logger, короткие строки.
# ------------------------------------------------------------
# Требования:
#   pip install requests psycopg[binary]
# ============================================================

import os
import sys
import logging
import requests
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection, close_connection

# --- Логгер ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("crypto.evening")

# --- Telegram ---
BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()

# --- Константы ---
LOOKBACK_HOURS = 24
MAX_MESSAGE_LEN = 3800

# Свежесть: только регулярные данные
FRESHNESS_EXPECTED = {
    "candles": 90,
    "features_hourly": 120,
    "price_patterns": 120,
}

# Таблицы для подсчёта
DB_TABLES = [
    ("candles", "candles"),
    ("funding_rates", "funding"),
    ("open_interest", "OI"),
    ("long_short_ratio", "LS"),
    ("taker_flow", "taker"),
    ("features_hourly", "features"),
    ("price_patterns", "patterns"),
    ("events", "events"),
    ("causal_links", "causal"),
    ("anomaly_log", "anomaly"),
]

# Проверки свежести: (таблица, колонка, метка)
FRESHNESS_CHECKS = [
    ("candles", "timestamp", "свечи"),
    ("features_hourly", "timestamp", "features"),
    ("price_patterns", "timestamp", "patterns"),
]


# ============================================================
# TELEGRAM
# ============================================================
def split_text(text: str, max_len: int) -> list:
    """Режет длинный текст по строкам."""
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


def send_message(text: str) -> bool:
    """Отправляет текст в Telegram. Длинный — частями."""
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("TELEGRAM not configured")
        log.info(text)
        return False

    parts = split_text(text, MAX_MESSAGE_LEN)
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
                log.error(
                    "tg err %d: %s",
                    r.status_code, r.text[:200],
                )
                ok_all = False
            else:
                log.info(
                    "part %d/%d sent",
                    i + 1, len(parts),
                )
        except Exception as e:
            log.exception("send_message: %s", e)
            ok_all = False

    return ok_all


# ============================================================
# DB QUERIES
# ============================================================
def fetch_runs(hours: int = LOOKBACK_HOURS) -> list:
    """Запуски collect_log за N часов."""
    since = datetime.now(timezone.utc)
    since = since - timedelta(hours=hours)
    out = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT job_name, status, "
                    "started_at, records_added "
                    "FROM collect_log "
                    "WHERE started_at > %s "
                    "ORDER BY started_at DESC"
                )
                cur.execute(sql, (since,))
                for row in cur.fetchall():
                    out.append({
                        "job": row[0] or "?",
                        "status": row[1] or "?",
                        "started": row[2],
                        "added": row[3] or 0,
                    })
    except Exception as e:
        log.exception("fetch_runs: %s", e)
    return out


def group_runs(runs: list) -> dict:
    """Группирует запуски по job_name."""
    grouped = {}
    for r in runs:
        key = r["job"]
        if key not in grouped:
            grouped[key] = {
                "ok": 0, "fail": 0, "added": 0,
            }
        g = grouped[key]
        if r["status"] == "ok":
            g["ok"] += 1
        else:
            g["fail"] += 1
        g["added"] += r["added"]
    return grouped


def fetch_stats() -> dict:
    """COUNT(*) по всем таблицам."""
    stats = {}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for table, _ in DB_TABLES:
                    try:
                        cur.execute(
                            "SELECT COUNT(*) FROM " + table
                        )
                        stats[table] = cur.fetchone()[0] or 0
                    except Exception as e:
                        log.warning(
                            "count %s: %s", table, e
                        )
                        stats[table] = -1
    except Exception as e:
        log.exception("fetch_stats: %s", e)
    return stats


def _max_ts(cur, table: str, col: str):
    """MAX(col) как UTC datetime или None."""
    try:
        cur.execute(
            "SELECT MAX(" + col + ") FROM " + table
        )
        row = cur.fetchone()
        if row and row[0]:
            ts = row[0]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
    except Exception as e:
        log.warning("max_ts %s: %s", table, e)
    return None


def fetch_freshness() -> dict:
    """Свежесть ключевых таблиц."""
    now = datetime.now(timezone.utc)
    out = {}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for table, col, label in FRESHNESS_CHECKS:
                    ts = _max_ts(cur, table, col)
                    if ts:
                        mins = int(
                            (now - ts).total_seconds() / 60
                        )
                        out[table] = {
                            "label": label,
                            "age_min": mins,
                        }
                    else:
                        out[table] = {
                            "label": label,
                            "age_min": None,
                        }
    except Exception as e:
        log.exception("fetch_freshness: %s", e)
    return out


def fetch_events_24h() -> dict:
    """События за 24ч, группировка по типу."""
    since = datetime.now(timezone.utc)
    since = since - timedelta(hours=24)
    out = {"total": 0, "by_type": {}}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT event_type, COUNT(*) "
                    "FROM events "
                    "WHERE timestamp > %s "
                    "GROUP BY event_type "
                    "ORDER BY 2 DESC"
                )
                cur.execute(sql, (since,))
                rows = cur.fetchall()
                for r in rows:
                    t = r[0] or "?"
                    n = r[1] or 0
                    out["by_type"][t] = n
                    out["total"] += n
    except Exception as e:
        log.exception("fetch_events_24h: %s", e)
    return out


def fetch_anomalies_24h() -> dict:
    """Аномалии за 24ч, группировка по типу."""
    since = datetime.now(timezone.utc)
    since = since - timedelta(hours=24)
    out = {"total": 0, "by_type": {}}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT anomaly_type, COUNT(*) "
                    "FROM anomaly_log "
                    "WHERE created_at > %s "
                    "GROUP BY anomaly_type "
                    "ORDER BY 2 DESC"
                )
                cur.execute(sql, (since,))
                rows = cur.fetchall()
                for r in rows:
                    t = r[0] or "?"
                    n = r[1] or 0
                    out["by_type"][t] = n
                    out["total"] += n
    except Exception as e:
        log.exception("fetch_anomalies_24h: %s", e)
    return out


# ============================================================
# FORMATTERS
# ============================================================
def fmt_age(mins) -> str:
    if mins is None:
        return "нет данных"
    if mins < 60:
        return str(mins) + " мин"
    if mins < 1440:
        return str(mins // 60) + " ч"
    return str(mins // 1440) + " д"


def icon_age(mins, expected: int) -> str:
    if mins is None:
        return "❌"
    if mins <= expected:
        return "✅"
    if mins <= expected * 2:
        return "⚠️"
    return "❌"


# ============================================================
# REPORT SECTIONS
# ============================================================
def _section_workflows(lines: list, grouped: dict) -> None:
    lines.append("⚙️ <b>Workflows за 24ч</b>")
    if not grouped:
        lines.append("  ⚠️ Запусков не было")
        return
    for job in sorted(grouped.keys()):
        g = grouped[job]
        icon = "✅" if g["fail"] == 0 else "⚠️"
        line = "  " + icon + " " + job + ": "
        line += str(g["ok"]) + " ok"
        if g["fail"]:
            line += " / " + str(g["fail"]) + " fail"
        line += " / +" + str(g["added"])
        lines.append(line)


def _section_freshness(lines: list, fresh: dict) -> None:
    lines.append("🕐 <b>Свежесть</b>")
    for table, info in fresh.items():
        exp = FRESHNESS_EXPECTED.get(table, 120)
        icon = icon_age(info["age_min"], exp)
        line = "  " + icon + " " + info["label"]
        line += ": " + fmt_age(info["age_min"])
        lines.append(line)


def _section_activity(lines: list, events: dict,
                      anomalies: dict) -> None:
    lines.append("📅 <b>Активность за 24ч</b>")

    # События
    if events["total"] == 0:
        lines.append("  • событий: 0")
    else:
        line = "  • событий: " + str(events["total"])
        types = []
        for t, n in events["by_type"].items():
            types.append(t + " " + str(n))
        if types:
            line += " (" + ", ".join(types) + ")"
        lines.append(line)

    # Аномалии
    if anomalies["total"] == 0:
        lines.append("  • аномалий: 0")
    else:
        line = "  🚨 аномалий: " + str(anomalies["total"])
        types = []
        for t, n in anomalies["by_type"].items():
            types.append(t + " " + str(n))
        if types:
            line += " (" + ", ".join(types) + ")"
        lines.append(line)


def _section_stats(lines: list, stats: dict) -> None:
    lines.append("📊 <b>Всего в БД</b>")
    for key, label in DB_TABLES:
        n = stats.get(key, -1)
        if n < 0:
            line = "  " + label + ": ошибка"
        else:
            line = "  " + label + ": "
            line += format(n, ",")
        lines.append(line)


def _collect_problems(grouped: dict, fresh: dict) -> list:
    """Только реальные проблемы (workflows + свежесть)."""
    problems = []
    for job, g in grouped.items():
        if g["fail"] > 0:
            problems.append(
                job + ": " + str(g["fail"]) + " fail"
            )
    for table, info in fresh.items():
        exp = FRESHNESS_EXPECTED.get(table, 120)
        age = info["age_min"]
        if age is None:
            problems.append(
                info["label"] + ": нет данных"
            )
        elif age > exp * 2:
            problems.append(
                info["label"] + ": " + fmt_age(age)
            )
    return problems


# ============================================================
# REPORT
# ============================================================
def build_report() -> str:
    """Собирает полный текст отчёта."""
    now = datetime.now(timezone.utc)
    lines = []
    lines.append("🌙 <b>ARGUS — техотчёт</b>")
    lines.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    lines.append("")

    runs = fetch_runs(LOOKBACK_HOURS)
    grouped = group_runs(runs)
    _section_workflows(lines, grouped)
    lines.append("")

    fresh = fetch_freshness()
    _section_freshness(lines, fresh)
    lines.append("")

    events = fetch_events_24h()
    anomalies = fetch_anomalies_24h()
    _section_activity(lines, events, anomalies)
    lines.append("")

    stats = fetch_stats()
    _section_stats(lines, stats)
    lines.append("")

    problems = _collect_problems(grouped, fresh)
    if not problems:
        lines.append("✨ <i>Всё штатно</i>")
    else:
        lines.append("⚠️ <b>Внимание</b>")
        for p in problems:
            lines.append("  • " + p)

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================
def main() -> None:
    log.info("=" * 50)
    log.info("evening report v2.1")
    log.info("=" * 50)

    exit_code = 0
    try:
        text = build_report()
        log.info("report built: %d chars", len(text))
        if not send_message(text):
            log.warning("send_message returned False")
        else:
            log.info("report sent")
    except Exception as e:
        log.exception("main: %s", e)
        try:
            send_message(
                "❌ Техотчёт упал\n"
                "<code>" + str(e)[:200] + "</code>"
            )
        except Exception:
            pass
        exit_code = 1
    finally:
        try:
            close_connection()
        except Exception:
            pass

    log.info("done")
    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()