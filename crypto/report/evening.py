# ============================================================
# ARGUS-Trader — ВЕЧЕРНИЙ ТЕХОТЧЁТ v2.2 [PRODUCTION]
# ------------------------------------------------------------
# Полный аудит: workflows, свежесть, активность,
# детали событий и аномалий, счётчики БД, проблемы.
# Отправка 18:00 UTC (20:00 КЛГ).
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
MIN_COLLECT_RUNS = 8

FRESHNESS_EXPECTED = {
    "candles": 90,
    "features_hourly": 120,
    "price_patterns": 120,
}

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

FRESHNESS_CHECKS = [
    ("candles", "timestamp", "свечи"),
    ("features_hourly", "timestamp", "features"),
    ("price_patterns", "timestamp", "patterns"),
]


# ============================================================
# TELEGRAM
# ============================================================
def split_text(text: str, max_len: int) -> list:
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
# HELPERS
# ============================================================
def _esc(s) -> str:
    if not s:
        return ""
    return str(s).replace("&", "&amp;").replace(
        "<", "&lt;").replace(">", "&gt;"
    )


def _since(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(
        hours=hours
    )


# ============================================================
# WORKFLOWS
# ============================================================
def fetch_runs(hours: int = LOOKBACK_HOURS) -> list:
    since = _since(hours)
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
    grouped = {}
    for r in runs:
        key = r["job"]
        if key not in grouped:
            grouped[key] = {
                "ok": 0,
                "fail": 0,
                "added": 0,
                "last_fail": None,
                "last_ok": None,
            }
        g = grouped[key]
        if r["status"] == "ok":
            g["ok"] += 1
            if g["last_ok"] is None:
                g["last_ok"] = r["started"]
        else:
            g["fail"] += 1
            if g["last_fail"] is None:
                g["last_fail"] = r["started"]
        g["added"] += r["added"]
    return grouped


# ============================================================
# FRESHNESS
# ============================================================
def _max_ts(cur, table: str, col: str):
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


# ============================================================
# ACTIVITY
# ============================================================
def fetch_events_24h() -> list:
    since = _since(24)
    out = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT symbol, timestamp, "
                    "event_type, change_pct "
                    "FROM events "
                    "WHERE timestamp > %s "
                    "ORDER BY timestamp DESC "
                    "LIMIT 20"
                )
                cur.execute(sql, (since,))
                for r in cur.fetchall():
                    out.append({
                        "symbol": r[0] or "?",
                        "timestamp": r[1],
                        "type": r[2] or "?",
                        "change_pct": float(r[3]) \
                            if r[3] is not None else 0,
                    })
    except Exception as e:
        log.exception("fetch_events_24h: %s", e)
    return out


def fetch_anomalies_24h() -> list:
    since = _since(24)
    out = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT symbol, timestamp, "
                    "anomaly_type, severity "
                    "FROM anomaly_log "
                    "WHERE created_at > %s "
                    "ORDER BY timestamp DESC "
                    "LIMIT 20"
                )
                cur.execute(sql, (since,))
                for r in cur.fetchall():
                    out.append({
                        "symbol": r[0] or "?",
                        "timestamp": r[1],
                        "type": r[2] or "?",
                        "severity": r[3] or "?",
                    })
    except Exception as e:
        log.exception("fetch_anomalies_24h: %s", e)
    return out


# ============================================================
# STATS
# ============================================================
def fetch_stats() -> dict:
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


# ============================================================
# FORMAT
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


def fmt_time_short(ts) -> str:
    if not ts:
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.strftime("%H:%M")


# ============================================================
# REPORT SECTIONS
# ============================================================
def _section_workflows(lines: list, grouped: dict,
                       runs: list) -> None:
    lines.append("⚙️ <b>Workflows за 24ч</b>")
    if not grouped:
        lines.append("  ⚠️ Запусков не было")
        lines.append("  ❗ Проверь cron-job.org")
        return

    total_runs = sum(g["ok"] + g["fail"]
                     for g in grouped.values())
    lines.append(
        "  всего запусков: " + str(total_runs)
    )

    for job in sorted(grouped.keys()):
        g = grouped[job]
        icon = "✅" if g["fail"] == 0 else "⚠️"
        line = "  " + icon + " " + _esc(job) + ": "
        line += str(g["ok"]) + " ok"
        if g["fail"]:
            line += " / " + str(g["fail"]) + " fail"
        line += " / +" + str(g["added"])
        lines.append(line)

        if g["fail"] > 0 and g["last_fail"]:
            t = fmt_time_short(g["last_fail"])
            lines.append("     last fail: " + t)
        elif g["last_ok"]:
            t = fmt_time_short(g["last_ok"])
            lines.append("     last ok: " + t)


def _section_freshness(lines: list, fresh: dict) -> None:
    lines.append("🕐 <b>Свежесть</b>")
    for table, info in fresh.items():
        exp = FRESHNESS_EXPECTED.get(table, 120)
        icon = icon_age(info["age_min"], exp)
        line = "  " + icon + " " + info["label"]
        line += ": " + fmt_age(info["age_min"])
        lines.append(line)


def _section_events(lines: list, events: list) -> None:
    lines.append("📅 <b>События за 24ч</b>")
    if not events:
        lines.append("  нет (рынок в боковике)")
        return

    # Группировка по типу
    by_type = {}
    for e in events:
        t = e["type"]
        by_type[t] = by_type.get(t, 0) + 1

    total = len(events)
    parts = []
    for t, n in sorted(
        by_type.items(), key=lambda x: -x[1]
    ):
        parts.append(_esc(t) + " " + str(n))
    lines.append(
        "  всего: " + str(total)
        + " (" + ", ".join(parts) + ")"
    )

    # Последние 5
    for e in events[:5]:
        sym = e["symbol"].replace("USDT", "")
        t = fmt_time_short(e["timestamp"])
        line = "    " + t + " " + sym
        line += " " + _esc(e["type"])
        if e["change_pct"]:
            line += " (" + format(
                e["change_pct"], "+.2f"
            ) + "%)"
        lines.append(line)


def _section_anomalies(lines: list, anomalies: list) -> None:
    lines.append("🚨 <b>Аномалии за 24ч</b>")
    if not anomalies:
        lines.append("  нет")
        return

    # Группировка по типу
    by_type = {}
    for a in anomalies:
        t = a["type"]
        by_type[t] = by_type.get(t, 0) + 1

    total = len(anomalies)
    parts = []
    for t, n in sorted(
        by_type.items(), key=lambda x: -x[1]
    ):
        parts.append(_esc(t) + " " + str(n))
    lines.append(
        "  всего: " + str(total)
        + " (" + ", ".join(parts) + ")"
    )

    # Последние 5 с деталями
    for a in anomalies[:5]:
        sym = a["symbol"].replace("USDT", "")
        t = fmt_time_short(a["timestamp"])
        sev = _esc(a["severity"])
        line = "    " + t + " " + sym
        line += " " + _esc(a["type"])
        line += " [" + sev + "]"
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


def _collect_problems(grouped: dict, fresh: dict,
                      runs: list) -> list:
    problems = []

    # Workflows
    for job, g in grouped.items():
        if g["fail"] > 0:
            problems.append(
                "workflow " + _esc(job)
                + ": " + str(g["fail"]) + " fail"
            )

    # Сколько запусков collect
    if grouped:
        main = max(
            grouped.items(),
            key=lambda x: x[1]["ok"] + x[1]["fail"],
        )
        total = main[1]["ok"] + main[1]["fail"]
        if total < MIN_COLLECT_RUNS:
            problems.append(
                "мало запусков (" + str(total)
                + " за 24ч, ожидаем 8+)"
            )
    else:
        problems.append("нет данных о запусках")

    # Freshness
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
    now = datetime.now(timezone.utc)
    lines = []
    lines.append("🌙 <b>ARGUS — техотчёт</b>")
    lines.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    lines.append("")

    runs = fetch_runs(LOOKBACK_HOURS)
    grouped = group_runs(runs)
    _section_workflows(lines, grouped, runs)
    lines.append("")

    fresh = fetch_freshness()
    _section_freshness(lines, fresh)
    lines.append("")

    events = fetch_events_24h()
    _section_events(lines, events)
    lines.append("")

    anomalies = fetch_anomalies_24h()
    _section_anomalies(lines, anomalies)
    lines.append("")

    stats = fetch_stats()
    _section_stats(lines, stats)
    lines.append("")

    problems = _collect_problems(
        grouped, fresh, runs,
    )
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
    log.info("evening report v2.2")
    log.info("=" * 50)

    exit_code = 0
    try:
        text = build_report()
        log.info("report: %d chars", len(text))
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