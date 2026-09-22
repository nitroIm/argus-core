# ============================================================
# ARGUS-Trader — ВЕЧЕРНИЙ ТЕХОТЧЁТ v1
# ------------------------------------------------------------
# Отправляет в Telegram: workflows за 24ч, свежесть,
# всего в БД, проблемы. 18:00 UTC = 20:00 КЛГ.
# ============================================================

import os
import sys
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection, close_connection

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
)
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXPECTED = {
    "pipeline_collect": 24,
    "pipeline_enrich": 24,
    "pipeline_detect": 24,
}


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print("[" + ts + "] " + str(msg), flush=True)


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        log("no telegram")
        print(text)
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
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        log("tg err: " + str(e))
        return False


def fetch_runs(hours=24):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT job_name, status, started_at,
                           records_added
                    FROM collect_log
                    WHERE started_at > %s
                    ORDER BY started_at DESC
                    LIMIT 200
                """, (since,))
                for row in cur.fetchall():
                    result.append({
                        "job": row[0],
                        "status": row[1],
                        "started": row[2],
                        "added": row[3] or 0,
                    })
    except Exception as e:
        log("runs err: " + str(e))
    return result


def fetch_stats():
    stats = {}
    tables = [
        "candles", "features_hourly",
        "price_patterns", "events",
        "causal_links", "anomaly_log",
    ]
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for t in tables:
                    try:
                        cur.execute(
                            "SELECT COUNT(*) FROM " + t
                        )
                        stats[t] = cur.fetchone()[0] or 0
                    except Exception:
                        stats[t] = -1
    except Exception as e:
        log("stats err: " + str(e))
    return stats


def fetch_freshness():
    fresh = {}
    checks = [
        ("candles", "timestamp"),
        ("features_hourly", "timestamp"),
        ("events", "created_at"),
    ]
    now = datetime.now(timezone.utc)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for table, col in checks:
                    try:
                        cur.execute(
                            "SELECT MAX(" + col + ") FROM " + table
                        )
                        row = cur.fetchone()
                        ts = row[0] if row else None
                        if ts and ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if ts:
                            mins = int(
                                (now - ts).total_seconds() / 60
                            )
                            fresh[table] = mins
                        else:
                            fresh[table] = None
                    except Exception:
                        fresh[table] = -1
    except Exception as e:
        log("fresh err: " + str(e))
    return fresh


def fmt_age(mins):
    if mins is None:
        return "нет данных"
    if mins == -1:
        return "ошибка"
    if mins < 60:
        return str(mins) + " мин назад"
    if mins < 1440:
        return str(mins // 60) + " ч назад"
    return str(mins // 1440) + " д назад"


def fmt_icon(mins, expected):
    if mins is None or mins == -1:
        return "❌"
    if mins <= expected:
        return "✅"
    if mins <= expected * 2:
        return "⚠️"
    return "❌"


def group_runs(runs):
    grouped = {}
    for r in runs:
        key = r["job"]
        if key not in grouped:
            grouped[key] = {"ok": 0, "fail": 0, "added": 0}
        g = grouped[key]
        if r["status"] == "ok":
            g["ok"] += 1
        else:
            g["fail"] += 1
        g["added"] += r["added"]
    return grouped


def build_report():
    now = datetime.now(timezone.utc)
    lines = []
    lines.append("🌙 <b>ARGUS — вечерний техотчёт</b>")
    lines.append("📅 " + now.strftime("%d.%m.%Y %H:%M UTC"))
    lines.append("")

    runs = fetch_runs(24)
    grouped = group_runs(runs)

    lines.append("⚙️ <b>Workflows за 24ч</b>")
    if not grouped:
        lines.append("  ⚠️ Запусков не было")
    else:
        for job, g in sorted(grouped.items()):
            icon = "✅" if g["fail"] == 0 else "⚠️"
            line = "  " + icon + " " + job + ": "
            line += str(g["ok"]) + " ok"
            if g["fail"]:
                line += " / " + str(g["fail"]) + " fail"
            line += " / +" + str(g["added"])
            lines.append(line)
    lines.append("")

    lines.append("📋 <b>План / факт</b>")
    for job, expected in EXPECTED.items():
        got = grouped.get(job, {}).get("ok", 0)
        if got == 0:
            icon = "❌"
        elif got < expected * 0.8:
            icon = "⚠️"
        else:
            icon = "✅"
        line = "  " + icon + " " + job + ": "
        line += str(got) + "/" + str(expected)
        lines.append(line)
    lines.append("")

    fresh = fetch_freshness()
    lines.append("🕐 <b>Свежесть</b>")
    checks = [
        ("candles", 90, "свечи"),
        ("features_hourly", 120, "features"),
        ("events", 180, "события"),
    ]
    for tbl, exp, label in checks:
        mins = fresh.get(tbl)
        icon = fmt_icon(mins, exp)
        lines.append(
            "  " + icon + " " + label + ": "
            + fmt_age(mins)
        )
    lines.append("")

    stats = fetch_stats()
    lines.append("📊 <b>Всего в БД</b>")
    lines.append("  Candles: " + str(stats.get("candles", 0)))
    lines.append(
        "  Features: " + str(stats.get("features_hourly", 0))
    )
    lines.append(
        "  Patterns: " + str(stats.get("price_patterns", 0))
    )
    lines.append("  Events: " + str(stats.get("events", 0)))
    lines.append(
        "  Causal: " + str(stats.get("causal_links", 0))
    )
    lines.append(
        "  Anomalies: " + str(stats.get("anomaly_log", 0))
    )
    lines.append("")

    problems = []
    for job, expected in EXPECTED.items():
        got = grouped.get(job, {}).get("ok", 0)
        if got < expected * 0.8:
            problems.append(job + " " + str(got) + "/" + str(expected))
    for tbl, exp, label in checks:
        mins = fresh.get(tbl)
        if mins is None or mins == -1 or mins > exp * 2:
            problems.append(label + " устарели")

    if not problems:
        lines.append("✨ <i>Штатно</i>")
    else:
        lines.append("⚠️ <b>Внимание:</b>")
        for p in problems:
            lines.append("  • " + p)

    return "\n".join(lines)


def main():
    log("evening report v1")
    try:
        text = build_report()
        if len(text) > 4000:
            text = text[:3950] + "\n..."
        send_telegram(text)
        log("sent")
    except Exception as e:
        import traceback
        traceback.print_exc()
        send_telegram(
            "❌ Техотчёт упал\n<code>"
            + str(e)[:200] + "</code>"
        )
        sys.exit(1)
    finally:
        close_connection()


if __name__ == "__main__":
    main()