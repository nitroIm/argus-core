# ============================================================
# ARGUS-Trader — ВЕЧЕРНИЙ ОТЧЁТ v3
# ------------------------------------------------------------
# v3: + portfolio (day PnL, trades today, positions).
#     + model status + files freshness.
#     Kaliningrad time.
#     DB2 routing for SOL/BNB.
# v2.2: base technical audit.
# ============================================================

import os
import sys
import json
import logging
import requests
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
STATE_DIR = (
    CRYPTO_ROOT / "mexc" / "simulator_01" / "state"
)
LEARN_DIR = CRYPTO_ROOT / "learn"
MODELS_DIR = LEARN_DIR / "models"

sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(
    0, str(CRYPTO_ROOT / "mexc" / "simulator_01")
)

# Auto-locate db2.py
for _p in CRYPTO_ROOT.rglob("db2.py"):
    _d = str(_p.parent)
    if "__pycache__" in _d:
        continue
    if _d not in sys.path:
        sys.path.insert(0, _d)
    break

from db import get_connection, close_connection

DB2_OK = False
get_conn_db2 = None
close_conn_db2 = None
if (os.getenv("ARGUS_DB_URL_2") or "").strip():
    try:
        from db2 import get_connection as get_conn_db2
        from db2 import close_connection as close_conn_db2
        _t = get_conn_db2()
        with _t as _c:
            with _c.cursor() as _cur:
                _cur.execute("SELECT 1")
                _cur.fetchone()
        DB2_OK = True
        print("DB2 OK")
    except Exception as e:
        print("DB2 fail: " + str(e))
else:
    print("ARGUS_DB_URL_2 not set")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("crypto.evening")

TZ = ZoneInfo("Europe/Kaliningrad")

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()

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

WATCHED_FILES = [
    ("levels_analysis.json",   120),
    ("patterns_analysis.json", 120),
    ("correlations.json",      360),
    ("events_analysis.json",   360),
    ("causal_analysis.json",   360),
    ("news_sentiment.json",    720),
]


def now_local():
    return datetime.now(timezone.utc).astimezone(TZ)


def _esc(s):
    if not s:
        return ""
    s = str(s)
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    return s


def _since(hours):
    return datetime.now(timezone.utc) - timedelta(
        hours=hours
    )


def fmt_age(mins):
    if mins is None:
        return "?"
    if mins < 60:
        return str(int(mins)) + "м"
    if mins < 1440:
        return str(int(mins // 60)) + "ч"
    return str(int(mins // 1440)) + "д"


def _signed_usd(v):
    sign = "+" if v >= 0 else "-"
    return sign + "$" + format(abs(v), ".2f")


def _signed_pct(v):
    sign = "+" if v >= 0 else ""
    return sign + format(v, ".2f") + "%"


def load_json(path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ============================================================
# PORTFOLIO (day view)
# ============================================================
def load_portfolio():
    return load_json(STATE_DIR / "portfolio.json", {})


def load_positions():
    return load_json(STATE_DIR / "positions.json", [])


def load_trades():
    return load_json(STATE_DIR / "trades.json", [])


def trades_in_window(hours):
    cutoff = _since(hours)
    out = []
    for t in load_trades():
        if not isinstance(t, dict):
            continue
        ts = t.get("exit_time")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if dt >= cutoff:
            out.append(t)
    return out


def fmt_portfolio_block():
    p = load_portfolio()
    lines = []

    if not p:
        lines.append("💼 <b>Портфель</b>")
        lines.append("  файл не найден")
        return lines

    lines.append("💼 <b>Портфель</b>")

    balance = float(p.get("balance", 0))
    start = float(p.get("start_balance", 50))
    pnl = float(p.get("realized_pnl", 0))
    total = int(p.get("total_trades", 0))
    wins = int(p.get("wins", 0))
    losses = int(p.get("losses", 0))

    pnl_pct = (pnl / start * 100) if start else 0
    wr = (wins / total * 100) if total else 0

    line = "  Баланс: $" + format(balance, ".2f")
    line += " | PnL " + _signed_usd(pnl)
    line += " (" + _signed_pct(pnl_pct) + ")"
    lines.append(line)

    line = "  Сделок: " + str(total)
    line += " (" + str(wins) + "W/"
    line += str(losses) + "L"
    line += " WR " + format(wr, ".1f") + "%)"
    lines.append(line)

    positions = load_positions()
    lines.append(
        "  Открыто: " + str(len(positions)) + "/3"
    )

    for pos in positions:
        sym = str(pos.get("symbol", "?")).replace(
            "USDT", ""
        )
        d = pos.get("direction", "?")
        entry = float(pos.get("entry_price", 0))
        lines.append(
            "    " + sym + " " + d
            + " @ " + format(entry, ".4f")
        )

    recent = trades_in_window(24)
    lines.append("")
    lines.append("📊 <b>Сделки за сутки</b>")

    if not recent:
        lines.append("  закрытий не было")
        return lines

    wins_24 = [
        t for t in recent if t.get("pnl_usd", 0) > 0
    ]
    losses_24 = [
        t for t in recent if t.get("pnl_usd", 0) <= 0
    ]
    pnl_24 = sum(
        float(t.get("pnl_usd", 0)) for t in recent
    )

    line = "  Закрыто: " + str(len(recent))
    line += " (" + str(len(wins_24)) + "W/"
    line += str(len(losses_24)) + "L)"
    line += " | PnL " + _signed_usd(pnl_24)
    lines.append(line)

    if len(recent) >= 2:
        sorted_t = sorted(
            recent,
            key=lambda x: float(x.get("pnl_usd", 0)),
            reverse=True,
        )
        for label, t in [
            ("Лучшая", sorted_t[0]),
            ("Худшая", sorted_t[-1]),
        ]:
            sym = str(t.get("symbol", "?")).replace(
                "USDT", ""
            )
            pnl_t = float(t.get("pnl_usd", 0))
            reason = t.get("exit_reason", "?")
            line = "  " + label + ": " + sym
            line += " " + _signed_usd(pnl_t)
            line += " (" + str(reason) + ")"
            lines.append(line)

    return lines


# ============================================================
# MODEL + FILES
# ============================================================
def fmt_model_block():
    lines = ["🧠 <b>Модель</b>"]
    meta = load_json(
        MODELS_DIR / "model_meta.json", {}
    )
    if not meta:
        lines.append("  model_meta.json нет")
        return lines

    acc = meta.get("accuracy")
    trained = meta.get("trained_at")

    line = "  Accuracy: "
    if acc is not None:
        line += format(acc, ".4f")
    else:
        line += "?"
    lines.append(line)

    if trained:
        try:
            dt = datetime.fromisoformat(trained)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_h = int(
                (datetime.now(timezone.utc)
                 - dt).total_seconds() / 3600
            )
            lines.append(
                "  Обучена: " + str(age_h) + "ч назад"
            )
        except Exception:
            pass

    signals = load_json(
        LEARN_DIR / "last_signals.json", {}
    )
    for s in signals.get("signals", []):
        sym = s.get("symbol", "?").replace("USDT", "")
        action = s.get("action", "?")
        prob = s.get("prob_up", 0.5)
        line = "  " + sym + ": " + action
        line += " (" + format(prob, ".3f") + ")"
        lines.append(line)
    return lines


def fmt_files_block():
    lines = ["📁 <b>Файлы</b>"]
    now = datetime.now(timezone.utc)
    for name, max_age_min in WATCHED_FILES:
        path = DATA_DIR / name
        if not path.exists():
            lines.append("  ❌ " + name)
            continue
        try:
            mtime = path.stat().st_mtime
            dt = datetime.fromtimestamp(
                mtime, tz=timezone.utc
            )
            age = int(
                (now - dt).total_seconds() / 60
            )
        except Exception:
            lines.append("  ? " + name)
            continue
        if age <= max_age_min:
            icon = "✅"
        elif age <= max_age_min * 2:
            icon = "⚠️"
        else:
            icon = "❌"
        lines.append(
            "  " + icon + " " + name
            + " (" + fmt_age(age) + ")"
        )
    return lines


# ============================================================
# TELEGRAM
# ============================================================
def split_text(text, max_len):
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
            current = (
                current + "\n" + line
                if current else line
            )
    if current:
        parts.append(current)
    return parts


def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("TG not configured")
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
                    "tg %d: %s",
                    r.status_code, r.text[:200],
                )
                ok_all = False
        except Exception as e:
            log.exception("send: %s", e)
            ok_all = False
    return ok_all


# ============================================================
# WORKFLOWS / FRESHNESS / EVENTS
# ============================================================
def fetch_runs(hours=LOOKBACK_HOURS):
    since = _since(hours)
    out = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT job_name, status, "
                    "started_at, records_added "
                    "FROM collect_log "
                    "WHERE started_at > %s "
                    "ORDER BY started_at DESC",
                    (since,),
                )
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


def group_runs(runs):
    grouped = {}
    for r in runs:
        key = r["job"]
        if key not in grouped:
            grouped[key] = {
                "ok": 0, "fail": 0, "added": 0,
                "last_fail": None, "last_ok": None,
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


def _max_ts(cur, table, col):
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
    now = datetime.now(timezone.utc)
    out = {}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for table, col, label in FRESHNESS_CHECKS:
                    ts = _max_ts(cur, table, col)
                    if ts:
                        mins = int(
                            (now - ts).total_seconds()
                            / 60
                        )
                        out[table] = {
                            "label": label, "age_min": mins,
                        }
                    else:
                        out[table] = {
                            "label": label, "age_min": None,
                        }
    except Exception as e:
        log.exception("freshness: %s", e)
    return out


def fetch_events_24h():
    since = _since(24)
    out = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, timestamp, "
                    "event_type, change_pct "
                    "FROM events "
                    "WHERE timestamp > %s "
                    "ORDER BY timestamp DESC LIMIT 20",
                    (since,),
                )
                for r in cur.fetchall():
                    cp = float(r[3]) if r[3] else 0
                    out.append({
                        "symbol": r[0] or "?",
                        "timestamp": r[1],
                        "type": r[2] or "?",
                        "change_pct": cp,
                    })
    except Exception as e:
        log.exception("events: %s", e)
    return out


def fetch_anomalies_24h():
    since = _since(24)
    out = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, timestamp, "
                    "anomaly_type, severity "
                    "FROM anomaly_log "
                    "WHERE created_at > %s "
                    "ORDER BY timestamp DESC LIMIT 20",
                    (since,),
                )
                for r in cur.fetchall():
                    out.append({
                        "symbol": r[0] or "?",
                        "timestamp": r[1],
                        "type": r[2] or "?",
                        "severity": r[3] or "?",
                    })
    except Exception as e:
        log.exception("anomalies: %s", e)
    return out


def fetch_stats():
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
                    except Exception:
                        stats[table] = -1
    except Exception:
        pass
    return stats


def fmt_age_or_dash(mins):
    return fmt_age(mins) if mins is not None else "нет"


def icon_age(mins, expected):
    if mins is None:
        return "❌"
    if mins <= expected:
        return "✅"
    if mins <= expected * 2:
        return "⚠️"
    return "❌"


def fmt_time_short(ts):
    if not ts:
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(TZ).strftime("%H:%M")


# ============================================================
# REPORT SECTIONS
# ============================================================
def _section_workflows(lines, grouped):
    lines.append("⚙️ <b>Workflows 24ч</b>")
    if not grouped:
        lines.append("  ⚠️ запусков не было")
        return
    total = sum(
        g["ok"] + g["fail"] for g in grouped.values()
    )
    lines.append("  всего: " + str(total))
    for job in sorted(grouped.keys()):
        g = grouped[job]
        icon = "✅" if g["fail"] == 0 else "⚠️"
        line = "  " + icon + " " + _esc(job) + ": "
        line += str(g["ok"]) + " ok"
        if g["fail"]:
            line += " / " + str(g["fail"]) + " fail"
        line += " / +" + str(g["added"])
        lines.append(line)


def _section_freshness(lines, fresh):
    lines.append("🕐 <b>Свежесть</b>")
    for table, info in fresh.items():
        exp = FRESHNESS_EXPECTED.get(table, 120)
        icon = icon_age(info["age_min"], exp)
        lines.append(
            "  " + icon + " " + info["label"]
            + ": " + fmt_age_or_dash(info["age_min"])
        )


def _section_events(lines, events):
    lines.append("📅 <b>События 24ч</b>")
    if not events:
        lines.append("  нет (рынок спокоен)")
        return
    by_type = {}
    for e in events:
        t = e["type"]
        by_type[t] = by_type.get(t, 0) + 1
    parts = []
    for t, n in sorted(
        by_type.items(), key=lambda x: -x[1]
    ):
        parts.append(_esc(t) + " " + str(n))
    line = "  всего " + str(len(events))
    line += ": " + ", ".join(parts)
    lines.append(line)
    for e in events[:3]:
        sym = e["symbol"].replace("USDT", "")
        t = fmt_time_short(e["timestamp"])
        cp = format(e["change_pct"], "+.2f")
        lines.append(
            "    " + t + " " + _esc(sym)
            + " " + _esc(e["type"])
            + " (" + cp + "%)"
        )


def _section_anomalies(lines, anomalies):
    lines.append("🚨 <b>Аномалии 24ч</b>")
    if not anomalies:
        lines.append("  нет")
        return
    by_type = {}
    for a in anomalies:
        t = a["type"]
        by_type[t] = by_type.get(t, 0) + 1
    parts = []
    for t, n in sorted(
        by_type.items(), key=lambda x: -x[1]
    ):
        parts.append(_esc(t) + " " + str(n))
    line = "  всего " + str(len(anomalies))
    line += ": " + ", ".join(parts)
    lines.append(line)


def _section_stats(lines, stats):
    lines.append("📊 <b>Всего в БД</b>")
    parts = []
    for key, label in DB_TABLES[:8]:
        n = stats.get(key, -1)
        if n < 0:
            continue
        parts.append(label + " " + format(n, ","))
    half = (len(parts) + 1) // 2
    for i in range(half):
        line = "  " + parts[i]
        if i + half < len(parts):
            line += " | " + parts[i + half]
        lines.append(line)


def _collect_problems(grouped, fresh):
    problems = []
    for job, g in grouped.items():
        if g["fail"] > 0:
            problems.append(
                "workflow " + _esc(job)
                + ": " + str(g["fail"]) + " fail"
            )
    if grouped:
        total_max = 0
        for g in grouped.values():
            t = g["ok"] + g["fail"]
            if t > total_max:
                total_max = t
        if total_max < MIN_COLLECT_RUNS:
            problems.append(
                "мало запусков ("
                + str(total_max) + " за 24ч)"
            )
    else:
        problems.append("нет данных о запусках")
    for table, info in fresh.items():
        exp = FRESHNESS_EXPECTED.get(table, 120)
        age = info["age_min"]
        if age is None:
            problems.append(info["label"] + ": нет")
        elif age > exp * 2:
            problems.append(
                info["label"] + ": " + fmt_age(age)
            )
    return problems


# ============================================================
# REPORT
# ============================================================
def build_report():
    now = now_local()
    lines = []
    lines.append("🌙 <b>ARGUS — вечер</b> v3")
    line = now.strftime("%d.%m.%Y %H:%M")
    line += " КЛГ"
    lines.append(line)
    lines.append("")

    lines.extend(fmt_portfolio_block())
    lines.append("")
    lines.append("─" * 20)
    lines.append("")

    lines.extend(fmt_model_block())
    lines.append("")

    lines.extend(fmt_files_block())
    lines.append("")
    lines.append("─" * 20)
    lines.append("")

    runs = fetch_runs(LOOKBACK_HOURS)
    grouped = group_runs(runs)
    _section_workflows(lines, grouped)
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

    problems = _collect_problems(grouped, fresh)
    if not problems:
        lines.append("✨ <i>Всё штатно</i>")
    else:
        lines.append("⚠️ <b>Внимание</b>")
        for p in problems:
            lines.append("  • " + p)

    return "\n".join(lines)


def main():
    log.info("evening report v3 start")
    log.info("DB2_OK = %s", DB2_OK)

    exit_code = 0
    try:
        text = build_report()
        log.info("len: %d", len(text))
        ok = send_message(text)
        if ok:
            log.info("sent")
    except Exception as e:
        log.exception("main: %s", e)
        exit_code = 1
    finally:
        try:
            close_connection()
        except Exception:
            pass
        if DB2_OK and close_conn_db2:
            try:
                close_conn_db2()
            except Exception:
                pass

    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()