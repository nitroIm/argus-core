# ============================================================
# ARGUS-Trader — НЕДЕЛЬНЫЙ ОТЧЁТ v7
# ------------------------------------------------------------
# v7: fix events timestamp (was created_at -> 0 rows).
#     + DB2 candle counts (SOL/BNB).
#     Kaliningrad time.
# v6: + weekly portfolio, per-symbol, best/worst.
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
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

try:
    from report.charts import plot_candles
    from report.charts import plot_pattern
    from report.charts import plot_markov
    HAS_CHARTS = True
except Exception as e:
    HAS_CHARTS = False
    print("charts: " + str(e))

TZ = ZoneInfo("Europe/Kaliningrad")

DB2_SYMBOLS = {"SOLUSDT", "BNBUSDT"}

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def now_local():
    return datetime.now(timezone.utc).astimezone(TZ)


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


def fmt_price(p):
    if p is None:
        return "?"
    if p >= 1000:
        return "$" + format(int(p), ",")
    if p >= 1:
        return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")


def _signed_usd(v):
    sign = "+" if v >= 0 else "-"
    return sign + "$" + format(abs(v), ".2f")


# ============================================================
# TELEGRAM
# ============================================================
def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("no token")
        return False
    if len(text) > 4000:
        text = text[:3950] + "\n..."
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
            print("sent")
            return True
        print("tg " + str(r.status_code))
        return False
    except Exception as e:
        print("tg: " + str(e))
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
                url, data=data,
                files=files, timeout=30,
            )
        return r.status_code == 200
    except Exception as e:
        print("photo: " + str(e))
        return False


# ============================================================
# PORTFOLIO WEEK
# ============================================================
def load_trades():
    return load_json(STATE_DIR / "trades.json", [])


def load_portfolio():
    return load_json(STATE_DIR / "portfolio.json", {})


def trades_in_week():
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=7
    )
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


def fmt_week_portfolio():
    p = load_portfolio()
    lines = []

    if not p:
        lines.append("💼 <b>Портфель</b>")
        lines.append("  файл не найден")
        return lines

    lines.append("💼 <b>Портфель неделя</b>")

    balance = float(p.get("balance", 0))
    start = float(p.get("start_balance", 50))

    lines.append(
        "  Сейчас: $" + format(balance, ".2f")
        + " (старт $" + format(start, ".2f") + ")"
    )

    week_t = trades_in_week()
    if not week_t:
        lines.append("  Сделок за неделю: 0")
        return lines

    week_wins = [
        t for t in week_t if t.get("pnl_usd", 0) > 0
    ]
    week_losses = [
        t for t in week_t if t.get("pnl_usd", 0) <= 0
    ]
    week_pnl = sum(
        float(t.get("pnl_usd", 0)) for t in week_t
    )
    week_wr = (
        len(week_wins) / len(week_t) * 100
        if week_t else 0
    )

    line = "  Сделок: " + str(len(week_t))
    line += " (" + str(len(week_wins)) + "W/"
    line += str(len(week_losses)) + "L"
    line += " WR " + format(week_wr, ".1f") + "%)"
    lines.append(line)

    lines.append(
        "  PnL неделя: " + _signed_usd(week_pnl)
    )

    avg = week_pnl / len(week_t) if week_t else 0
    lines.append(
        "  Средний PnL: " + _signed_usd(avg)
    )

    sorted_t = sorted(
        week_t,
        key=lambda x: float(x.get("pnl_usd", 0)),
        reverse=True,
    )
    for label, t in [
        ("🥇 Лучшая", sorted_t[0]),
        ("🥉 Худшая", sorted_t[-1]),
    ]:
        sym = str(t.get("symbol", "?")).replace(
            "USDT", ""
        )
        pnl_t = float(t.get("pnl_usd", 0))
        reason = t.get("exit_reason", "?")
        lines.append(
            "  " + label + ": " + sym
            + " " + _signed_usd(pnl_t)
            + " (" + str(reason) + ")"
        )

    # Per symbol
    by_sym = {}
    for t in week_t:
        sym = str(t.get("symbol", "?")).replace(
            "USDT", ""
        )
        if sym not in by_sym:
            by_sym[sym] = {"n": 0, "wins": 0, "pnl": 0.0}
        by_sym[sym]["n"] += 1
        by_sym[sym]["pnl"] += float(
            t.get("pnl_usd", 0)
        )
        if t.get("pnl_usd", 0) > 0:
            by_sym[sym]["wins"] += 1

    if by_sym:
        lines.append("  По монетам:")
        for sym, d in sorted(
            by_sym.items(),
            key=lambda x: x[1]["pnl"],
            reverse=True,
        ):
            wr = d["wins"] / d["n"] * 100
            line = "    " + sym + ": "
            line += str(d["n"]) + " сд"
            line += " (" + format(wr, ".0f") + "% WR)"
            line += " " + _signed_usd(d["pnl"])
            lines.append(line)

    return lines


# ============================================================
# MODEL
# ============================================================
def fmt_model_block():
    lines = ["🧠 <b>Модель</b>"]
    meta = load_json(
        MODELS_DIR / "model_meta.json", {}
    )
    if not meta:
        lines.append("  нет мета")
        return lines

    acc = meta.get("accuracy")
    trained = meta.get("trained_at")
    n_feat = len(meta.get("features", []))

    line = "  Accuracy: "
    if acc is not None:
        line += format(acc, ".4f")
    else:
        line += "?"
    line += " | фич " + str(n_feat)
    lines.append(line)

    if trained:
        try:
            dt = datetime.fromisoformat(trained)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_d = (
                datetime.now(timezone.utc) - dt
            ).total_seconds() / 86400
            lines.append(
                "  Обучена: "
                + format(age_d, ".1f") + "д назад"
            )
        except Exception:
            pass

    top = meta.get("top_features", [])[:3]
    if top:
        parts = [
            t["name"] + " " + format(t["gain"], ".0f")
            for t in top
        ]
        lines.append(
            "  Топ фичи: " + ", ".join(parts)
        )
    return lines


# ============================================================
# DATA STATS (with DB2)
# ============================================================
def _candles_in(conn, symbol):
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM candles "
                "WHERE symbol=%s AND timeframe='1h'",
                (symbol,),
            )
            return cur.fetchone()[0] or 0
    except Exception:
        return -1


def fetch_stats():
    stats = {
        "candles": {},
        "features_hourly": 0,
        "price_patterns": 0,
        "events": 0,
        "events_week": 0,
        "events_by_type": {},
        "causal_links": 0,
        "anomaly_week": 0,
    }

    # DB1: BTC/ETH
    try:
        with get_connection() as conn:
            for sym in ["BTCUSDT", "ETHUSDT"]:
                stats["candles"][sym] = _candles_in(
                    conn, sym
                )

            for k, t in [
                ("features_hourly", "features_hourly"),
                ("price_patterns", "price_patterns"),
                ("events", "events"),
                ("causal_links", "causal_links"),
            ]:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT COUNT(*) FROM " + t
                        )
                        stats[k] = cur.fetchone()[0] or 0
                except Exception:
                    pass

            # Events week — by timestamp, not created_at
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM events "
                        "WHERE timestamp > "
                        "NOW() - INTERVAL '7 days'"
                    )
                    stats["events_week"] = (
                        cur.fetchone()[0] or 0
                    )
            except Exception:
                pass

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT event_type, COUNT(*) "
                        "FROM events "
                        "WHERE timestamp > "
                        "NOW() - INTERVAL '7 days' "
                        "GROUP BY event_type "
                        "ORDER BY 2 DESC LIMIT 5"
                    )
                    stats["events_by_type"] = {
                        r[0]: r[1] for r in cur.fetchall()
                    }
            except Exception:
                pass

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM anomaly_log "
                        "WHERE timestamp > "
                        "NOW() - INTERVAL '7 days'"
                    )
                    stats["anomaly_week"] = (
                        cur.fetchone()[0] or 0
                    )
            except Exception:
                pass
    except Exception as e:
        print("stats DB1: " + str(e))

    # DB2: SOL/BNB
    if DB2_OK:
        try:
            with get_conn_db2() as conn:
                for sym in ["SOLUSDT", "BNBUSDT"]:
                    stats["candles"][sym] = _candles_in(
                        conn, sym
                    )
        except Exception as e:
            print("stats DB2: " + str(e))
            for sym in ["SOLUSDT", "BNBUSDT"]:
                stats["candles"][sym] = -1

    return stats


def fmt_data_block(stats):
    lines = ["📦 <b>Данные</b>"]

    c = stats["candles"]
    parts = []
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
        n = c.get(sym, -1)
        name = sym.replace("USDT", "")
        if n < 0:
            parts.append(name + " ?")
        else:
            parts.append(name + " " + str(n))
    lines.append("  Свечи: " + " | ".join(parts))

    lines.append(
        "  Features: " + str(stats["features_hourly"])
    )

    line = "  Events: " + str(stats["events"])
    line += " (неделя " + str(stats["events_week"]) + ")"
    lines.append(line)

    if stats["events_by_type"]:
        parts = [
            t + " " + str(n)
            for t, n in stats["events_by_type"].items()
        ]
        lines.append("    " + ", ".join(parts))

    return lines


# ============================================================
# LEVELS + CORR
# ============================================================
def fmt_levels_block():
    lines = []
    levels = load_json(DATA_DIR / "levels_analysis.json", {})
    if not levels or not levels.get("symbols"):
        return lines

    lines.append("📍 <b>Уровни</b>")
    for sym, data in levels["symbols"].items():
        name = sym.replace("USDT", "")
        price = data.get("current_price", 0)
        sup = data.get("supports", [])
        res = data.get("resistances", [])
        lines.append(
            "  " + name + ": " + fmt_price(price)
        )
        if sup:
            s = sup[0]
            lines.append(
                "    🛡 " + fmt_price(s["price"])
                + " (-" + format(s["distance_pct"], ".2f")
                + "%)"
            )
        if res:
            r = res[0]
            lines.append(
                "    ⚔️ " + fmt_price(r["price"])
                + " (+" + format(r["distance_pct"], ".2f")
                + "%)"
            )
    return lines


def fmt_corr_block():
    lines = []
    corr = load_json(DATA_DIR / "correlations.json", {})
    if not corr or not corr.get("symbols"):
        return lines

    rules = []
    for sym, d in corr["symbols"].items():
        for r in d.get("rules", []):
            if r.get("samples", 0) < 10:
                continue
            r2 = dict(r)
            r2["symbol"] = sym.replace("USDT", "")
            rules.append(r2)

    if not rules:
        return lines

    rules.sort(
        key=lambda x: x.get("edge", 0),
        reverse=True,
    )
    lines.append("🧠 <b>Правила недели</b>")
    for r in rules[:5]:
        arrow = "↑" if r["direction"] == "up" else "↓"
        line = "  " + arrow + " ["
        line += r["symbol"] + "] " + r["rule"]
        line += " (" + format(
            r["confidence"] * 100, ".0f"
        ) + "%"
        line += " N=" + str(r["samples"])
        line += " e=" + format(r.get("edge", 0), ".2f")
        line += ")"
        lines.append(line)
    return lines


# ============================================================
# CHARTS
# ============================================================
def fetch_candles_for_chart(symbol, limit=100):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, low, "
                    "close, volume FROM candles "
                    "WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                rows = list(reversed(cur.fetchall()))
                out = []
                for r in rows:
                    out.append({
                        "timestamp": r[0],
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                    })
                return out
    except Exception:
        return []


def build_candle_caption(symbol, candles, sup, res):
    name = symbol.replace("USDT", "")
    lines = [f"📊 <b>{name} — 100h</b>"]
    if candles:
        first = candles[0]["close"]
        last = candles[-1]["close"]
        ch = (last - first) / first * 100
        lines.append(
            f"💰 {fmt_price(last)} ({ch:+.2f}%)"
        )
    if sup:
        s = sup[0]
        lines.append(
            "🛡 " + fmt_price(s["price"])
            + " (-" + format(s["distance_pct"], ".2f")
            + "%)"
        )
    if res:
        r = res[0]
        lines.append(
            "⚔️ " + fmt_price(r["price"])
            + " (+" + format(r["distance_pct"], ".2f")
            + "%)"
        )
    return "\n".join(lines)


def build_pattern_caption(symbol, data):
    name = symbol.replace("USDT", "")
    lines = [f"🧩 <b>{name} — 50h</b>"]
    up = data.get("up_count", 0)
    down = data.get("down_count", 0)
    ratio = data.get("up_ratio", 0) * 100
    lines.append(
        f"⬆️ {up} | ⬇️ {down} ({ratio:.0f}% up)"
    )
    top = data.get("ngrams_top", [])[:1]
    if top:
        t = top[0]
        p_up = t["p_up"] * 100
        lines.append(
            f"🔥 `{t['ngram']}` → ↑ {p_up:.0f}% "
            f"(N={t['count']})"
        )
    return "\n".join(lines)


def build_markov_caption(symbol, mk):
    name = symbol.replace("USDT", "")
    lines = [f"🧠 <b>{name} — Markov</b>"]
    p11 = mk.get("p_1_given_1", 0)
    p10 = mk.get("p_1_given_0", 0)
    lines.append(f"P(1|1) = {p11:.2f} (после роста)")
    lines.append(f"P(1|0) = {p10:.2f} (после падения)")
    if p10 > 0.55:
        lines.append("📌 Mean reversion")
    elif p10 < 0.45:
        lines.append("📌 Momentum")
    else:
        lines.append("📌 Neutral")
    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================
def main():
    print("weekly v7")

    now = now_local()
    lines = []
    lines.append("📅 <b>ARGUS — неделя</b> v7")
    line = now.strftime("%d.%m.%Y %H:%M")
    line += " КЛГ"
    lines.append(line)
    week_ago = now - timedelta(days=7)
    lines.append(
        "<i>" + week_ago.strftime("%d.%m") + " — "
        + now.strftime("%d.%m") + "</i>"
    )
    lines.append("")

    lines.extend(fmt_week_portfolio())
    lines.append("")
    lines.append("─" * 20)
    lines.append("")

    lines.extend(fmt_model_block())
    lines.append("")

    stats = fetch_stats()
    lines.extend(fmt_data_block(stats))
    lines.append("")

    lines.extend(fmt_levels_block())
    lines.append("")

    lines.extend(fmt_corr_block())
    lines.append("")

    anom = stats["anomaly_week"]
    if anom > 0:
        lines.append("🚨 Аномалии: " + str(anom))
    else:
        lines.append("🚨 Аномалии: нет ✅")

    message = "\n".join(lines)
    send_message(message)

    if not HAS_CHARTS:
        print("no charts")
        close_connection()
        if DB2_OK and close_conn_db2:
            try:
                close_conn_db2()
            except Exception:
                pass
        return

    patterns = load_json(DATA_DIR / "patterns_analysis.json")
    levels = load_json(DATA_DIR / "levels_analysis.json")

    for symbol in ["BTCUSDT", "ETHUSDT"]:
        print("--- " + symbol)
        candles = fetch_candles_for_chart(symbol, limit=100)
        if candles:
            sym_lvl = levels.get("symbols", {}).get(symbol, {})
            path = plot_candles(
                symbol, candles,
                supports=sym_lvl.get("supports", []),
                resistances=sym_lvl.get("resistances", []),
            )
            if path:
                cap = build_candle_caption(
                    symbol, candles,
                    sym_lvl.get("supports", []),
                    sym_lvl.get("resistances", []),
                )
                send_photo(path, cap)

        sym_p = patterns.get("symbols", {}).get(symbol, {})
        binary = sym_p.get("binary_string", "")
        if binary:
            path = plot_pattern(symbol, binary)
            if path:
                cap = build_pattern_caption(symbol, sym_p)
                send_photo(path, cap)

        mk = sym_p.get("markov", {})
        if mk:
            path = plot_markov(symbol, mk)
            if path:
                cap = build_markov_caption(symbol, mk)
                send_photo(path, cap)

    close_connection()
    if DB2_OK and close_conn_db2:
        try:
            close_conn_db2()
        except Exception:
            pass
    print("done")


if __name__ == "__main__":
    main()