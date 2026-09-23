# ============================================================
# ARGUS-Trader — ВЕЧЕРНИЙ ТЕХОТЧЁТ v2
# ------------------------------------------------------------
# v2: схема collect_log подтверждена.
# v1: workflows + свежесть + всего в БД
# ============================================================

import os
import sys
import requests
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection, close_connection

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print("[" + ts + "] " + str(msg), flush=True)


def split_text(text, max_len=3800):
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


def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        log("no telegram")
        print(text)
        return False
    parts = split_text(text, 3800)
    ok_all = True
    for part in parts:
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
                log("send err: " + r.text[:200])
                ok_all = False
        except Exception as e:
            log("send: " + str(e))
            ok_all = False
    log("sent " + str(len(parts)) + " parts")
    return ok_all


def fetch_runs(hours=24):
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
        log("runs err: " + str(e))
    return out


def group_runs(runs):
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


def fetch_stats():
    stats = {}
    tables = [
        "candles",
        "funding_rates",
        "open_interest",
        "long_short_ratio",
        "taker_flow",
        "features_hourly",
        "price_patterns",
        "events",
        "causal_links",
        "anomaly_log",
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


def max_ts(cur, table, col):
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
    except Exception:
        pass
    return None


def fetch_freshness():
    checks = [
        ("candles", "timestamp", "свечи"),
        ("features_hourly", "timestamp", "features"),
        ("price_patterns", "timestamp", "patterns"),
        ("events", "timestamp", "события"),
        ("anomaly_log", "created_at", "аномалии"),
    ]
    now = datetime.now(timezone.utc)
    out = {}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for table, col, label in checks:
                    ts = max_ts(cur, table, col)
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
        log("fresh err: " + str(e))
    return out


def fmt_age(mins):
    if mins is None:
        return "нет данных"
    if mins < 60:
        return str(mins) + " мин"
    if mins < 1440:
        return str(mins // 60) + " ч"
    return str(mins // 1440) + " д"


def icon_age(mins, expected):
    if mins is None:
        return "❌"
    if mins <= expected:
        return "✅"
    if mins <= expected * 2:
        return "⚠️"
    return "❌"


def build_report():
    now = datetime.now(timezone.utc)
    lines = []
    lines.append("🌙 <b>ARGUS — техотчёт</b>")
    lines.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    lines.append("")

    # Workflows
    runs = fetch_runs(24)
    grouped = group_runs(runs)

    lines.append("⚙️ <b>Workflows за 24ч</b>")
    if not grouped:
        lines.append("  ⚠️ Запусков не было")
    else:
        for job in sorted(grouped.keys()):
            g = grouped[job]
            if g["fail"] == 0:
                icon = "✅"
            else:
                icon = "⚠️"
            line = "  " + icon + " " + job + ": "
            line += str(g["ok"]) + " ok"
            if g["fail"]:
                line += " / " + str(g["fail"]) + " fail"
            line += " / +" + str(g["added"])
            lines.append(line)
    lines.append("")

    # Свежесть
    fresh = fetch_freshness()
    expected = {
        "candles": 90,
        "features_hourly": 120,
        "price_patterns": 120,
        "events": 240,
        "anomaly_log": 120,
    }
    lines.append("🕐 <b>Свежесть</b>")
    for table, info in fresh.items():
        exp = expected.get(table, 120)
        icon = icon_age(info["age_min"], exp)
        label = info["label"]
        age = fmt_age(info["age_min"])
        lines.append("  " + icon + " " + label + ": " + age)
    lines.append("")

    # Всего в БД
    stats = fetch_stats()
    lines.append("📊 <b>Всего в БД</b>")
    order = [
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
    for key, label in order:
        n = stats.get(key, -1)
        if n < 0:
            lines.append("  " + label + ": ошибка")
        else:
            lines.append(
                "  " + label + ": " + format(n, ",")
            )
    lines.append("")

    # Проблемы
    problems = []
    for job, g in grouped.items():
        if g["fail"] > 0:
            problems.append(
                job + ": " + str(g["fail"]) + " fail"
            )
    for table, info in fresh.items():
        exp = expected.get(table, 120)
        age = info["age_min"]
        if age is None:
            problems.append(info["label"] + ": нет данных")
        elif age > exp * 2:
            problems.append(
                info["label"] + ": " + fmt_age(age)
            )

    if not problems:
        lines.append("✨ <i>Всё штатно</i>")
    else:
        lines.append("⚠️ <b>Внимание</b>")
        for p in problems:
            lines.append("  • " + p)

    return "\n".join(lines)


def main():
    log("=" * 50)
    log("evening report v2")
    log("=" * 50)
    try:
        text = build_report()
        send_message(text)
    except Exception as e:
        import traceback
        traceback.print_exc()
        send_message(
            "❌ Техотчёт упал\n<code>"
            + str(e)[:200] + "</code>"
        )
        sys.exit(1)
    finally:
        close_connection()


if __name__ == "__main__":
    main()