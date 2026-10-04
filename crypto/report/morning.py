# ============================================================
# ARGUS - УТРЕННИЙ ОТЧЁТ v11
# ------------------------------------------------------------
# v11: Kaliningrad time (UTC+2). DB2 debug.
#      Safe DB2 ping before use.
# v10: auto-locate db2.py.
# v9: SOL/BNB read from DB2.
# ============================================================

import os
import sys
import json
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
TMP_DIR = Path("/tmp/argus_charts")
TMP_DIR.mkdir(parents=True, exist_ok=True)

TZ = ZoneInfo("Europe/Kaliningrad")

sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(
    0, str(CRYPTO_ROOT / "mexc" / "simulator_01")
)

# --- Auto-locate db2.py anywhere under crypto/ ---
for _p in CRYPTO_ROOT.rglob("db2.py"):
    _d = str(_p.parent)
    if "__pycache__" in _d:
        continue
    if _d not in sys.path:
        sys.path.insert(0, _d)
    print("db2.py located at: " + _d)
    break

from db import get_connection
from db import close_connection
from report.charts import plot_candles
from report.charts import plot_pattern
from report.charts import plot_rsi
from report.charts import plot_funding
from report.charts import plot_oi
from report.charts import compute_rsi
from report.risk import compute_atr

# --- DB2 ---
DB2_URL_PRESENT = bool(
    (os.getenv("ARGUS_DB_URL_2") or "").strip()
)
print("ARGUS_DB_URL_2 present: " + str(DB2_URL_PRESENT))

DB2_OK = False
get_conn_db2 = None
close_conn_db2 = None

if DB2_URL_PRESENT:
    try:
        from db2 import get_connection as get_conn_db2
        from db2 import close_connection as close_conn_db2
        # Test ping
        try:
            _test = get_conn_db2()
            with _test as _c:
                with _c.cursor() as _cur:
                    _cur.execute("SELECT 1")
                    _cur.fetchone()
            DB2_OK = True
            print("DB2 ping OK")
        except Exception as e:
            print("DB2 ping failed: " + str(e))
            DB2_OK = False
    except Exception as e:
        print("db2 import failed: " + str(e))
        DB2_OK = False
else:
    print("DB2 skipped: ARGUS_DB_URL_2 not set in env")

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()

MAX_POSITIONS = 3

SYMBOLS = [
    ("BTCUSDT", "BTC", "DB1"),
    ("ETHUSDT", "ETH", "DB1"),
    ("SOLUSDT", "SOL", "DB2"),
    ("BNBUSDT", "BNB", "DB2"),
]
DB2_SYMBOLS = {"SOLUSDT", "BNBUSDT"}

WATCHED_FILES = [
    ("levels_analysis.json",   120),
    ("patterns_analysis.json", 120),
    ("correlations.json",      360),
    ("events_analysis.json",   360),
    ("causal_analysis.json",   360),
    ("news_sentiment.json",    720),
]

SPOT_SOURCES = [
    {
        "name": "MEXC",
        "url": "https://api.mexc.com/api/v3/ticker/price?symbol={sym}",
        "parse": lambda j: float(j["price"]),
    },
    {
        "name": "Binance",
        "url": "https://api.binance.com/api/v3/ticker/price?symbol={sym}",
        "parse": lambda j: float(j["price"]),
    },
]

EXPLORER_OK = False
try:
    import explorer as explorer_mod
    EXPLORER_OK = True
except Exception as e:
    print("explorer import failed: " + str(e))
    explorer_mod = None


def now_local():
    """Current time in Kaliningrad."""
    return datetime.now(timezone.utc).astimezone(TZ)


def candles_conn(symbol):
    """Contextmanager: DB2 for SOL/BNB, DB1 otherwise."""
    if symbol in DB2_SYMBOLS and DB2_OK:
        return get_conn_db2()
    return get_connection()


# ============================================================
# FMT HELPERS
# ============================================================
def fmt_price(p):
    if p is None:
        return "?"
    if p >= 1000:
        return "$" + format(int(p), ",")
    if p >= 1:
        return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")


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


def escape_html(text):
    if not text:
        return ""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


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
# PORTFOLIO
# ============================================================
def load_portfolio():
    return load_json(STATE_DIR / "portfolio.json", {})


def load_positions():
    return load_json(STATE_DIR / "positions.json", [])


def load_trades():
    return load_json(STATE_DIR / "trades.json", [])


def trades_in_window(hours):
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=hours
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


def fmt_portfolio_block():
    p = load_portfolio()
    lines = []

    if not p:
        lines.append("💼 <b>Портфель</b>")
        lines.append("  portfolio.json не найден")
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
    line = "  Открыто: " + str(len(positions))
    line += "/" + str(MAX_POSITIONS)
    lines.append(line)

    for pos in positions:
        sym = str(pos.get("symbol", "?")).replace(
            "USDT", ""
        )
        d = pos.get("direction", "?")
        entry = float(pos.get("entry_price", 0))
        stop = float(pos.get("stop", 0))
        target = float(pos.get("target", 0))
        size = float(pos.get("size_usd", 0))
        line = "    " + sym + " " + d
        line += " $" + format(size, ".2f")
        line += " @ " + fmt_price(entry)
        lines.append(line)
        line = "      стоп " + fmt_price(stop)
        line += " | цель " + fmt_price(target)
        lines.append(line)

    recent = trades_in_window(24)
    lines.append("")
    lines.append("📊 <b>Сделки за 24ч</b>")

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
# SYSTEM HEALTH
# ============================================================
def _count_in(conn, table):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM " + table)
            return cur.fetchone()[0] or 0
    except Exception:
        return -1


def _max_ts_in(conn, table):
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(timestamp) FROM " + table
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


def fmt_system_block():
    lines = ["📦 <b>Система</b>"]

    candle_parts = []
    for sym, name, db in SYMBOLS:
        n = -1
        try:
            with candles_conn(sym) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM candles "
                        "WHERE symbol=%s AND "
                        "timeframe='1h'",
                        (sym,),
                    )
                    n = cur.fetchone()[0] or 0
        except Exception as e:
            print(
                "candles count " + sym
                + " (" + db + "): " + str(e)
            )
        if n < 0:
            candle_parts.append(name + " ?")
        else:
            candle_parts.append(name + " " + str(n))
    lines.append(
        "  Свечи: " + " | ".join(candle_parts)
    )

    try:
        with get_connection() as conn:
            n_feat = _count_in(conn, "features_hourly")
            ts_feat = _max_ts_in(
                conn, "features_hourly"
            )
            age = None
            if ts_feat:
                age = int(
                    (datetime.now(timezone.utc)
                     - ts_feat).total_seconds() / 60
                )
            line = "  Features: " + str(n_feat)
            line += " (age " + fmt_age(age) + ")"
            lines.append(line)

            n_ev = _count_in(conn, "events")
            ts_ev = _max_ts_in(conn, "events")
            age_ev = None
            if ts_ev:
                age_ev = int(
                    (datetime.now(timezone.utc)
                     - ts_ev).total_seconds() / 60
                )
            line = "  Events: " + str(n_ev)
            line += " (age " + fmt_age(age_ev) + ")"
            lines.append(line)

            n_ca = _count_in(conn, "causal_links")
            lines.append("  Causal: " + str(n_ca))

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM anomaly_log "
                        "WHERE created_at > "
                        "NOW() - INTERVAL '24 hours'"
                    )
                    n_an = cur.fetchone()[0] or 0
                lines.append(
                    "  Anomaly 24ч: " + str(n_an)
                )
            except Exception:
                pass
    except Exception as e:
        lines.append("  DB error: " + str(e)[:80])

    return lines


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
        line += " (prob_up=" + format(prob, ".3f") + ")"
        lines.append(line)

    return lines


def fmt_files_block():
    lines = ["📁 <b>Файлы</b>"]
    now = datetime.now(timezone.utc)

    for name, max_age_min in WATCHED_FILES:
        path = DATA_DIR / name
        if not path.exists():
            lines.append("  ❌ " + name + " нет")
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
# SPOT
# ============================================================
def fetch_spot(symbol):
    for src in SPOT_SOURCES:
        try:
            url = src["url"].format(sym=symbol)
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                return src["parse"](r.json())
        except Exception:
            continue
    return None


# ============================================================
# TELEGRAM SEND
# ============================================================
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
        print("no token")
        return False
    parts = split_text(text, 3800)
    ok_all = True
    for part in parts:
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
                print("send err " + str(r.status_code))
                ok_all = False
        except Exception as e:
            print("send: " + str(e))
            ok_all = False
    print("text sent: " + str(len(parts)) + " parts")
    return ok_all


def send_media_group(photos):
    if not BOT_TOKEN or not CHAT_ID:
        return False
    if not photos:
        return False

    photos = photos[:10]
    media = []
    files = {}

    for i, (path, caption) in enumerate(photos):
        if not Path(path).exists():
            continue
        attach = "file" + str(i)
        item = {
            "type": "photo",
            "media": "attach://" + attach,
        }
        if i == 0 and caption:
            item["caption"] = caption[:1000]
            item["parse_mode"] = "HTML"
        media.append(item)
        files[attach] = (
            Path(path).name,
            open(path, "rb"),
            "image/png",
        )

    if not media:
        return False

    try:
        url = "https://api.telegram.org/bot"
        url += BOT_TOKEN + "/sendMediaGroup"
        data = {
            "chat_id": CHAT_ID,
            "media": json.dumps(media),
        }
        r = requests.post(
            url, data=data, files=files, timeout=60,
        )
        for f in files.values():
            try:
                f[1].close()
            except Exception:
                pass
        if r.status_code == 200:
            print("album: " + str(len(media)))
            return True
        print("album err: " + r.text[:200])
        return False
    except Exception as e:
        print("album: " + str(e))
        return False


# ============================================================
# DB READ HELPERS
# ============================================================
def fetch_candles(symbol, limit=200):
    try:
        with candles_conn(symbol) as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT timestamp, open, high, "
                    "low, close, volume FROM candles "
                    "WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "ORDER BY timestamp DESC LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
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
    except Exception as e:
        print("candles " + symbol + ": " + str(e))
        return []


def fetch_funding(symbol, limit=50):
    try:
        with candles_conn(symbol) as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT timestamp, rate "
                    "FROM funding_rates "
                    "WHERE symbol = %s "
                    "AND rate IS NOT NULL "
                    "ORDER BY timestamp DESC LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
                rows = list(reversed(cur.fetchall()))
                return [
                    {"timestamp": r[0], "rate": float(r[1])}
                    for r in rows
                ]
    except Exception:
        return []


def fetch_oi(symbol, limit=100):
    try:
        with candles_conn(symbol) as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT timestamp, oi "
                    "FROM open_interest "
                    "WHERE symbol = %s "
                    "AND oi IS NOT NULL "
                    "ORDER BY timestamp DESC LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
                rows = list(reversed(cur.fetchall()))
                return [
                    {"timestamp": r[0], "oi": float(r[1])}
                    for r in rows
                ]
    except Exception:
        return []


def fmt_regime(regime):
    if not regime:
        return "?"
    label = regime.get("label", "?")
    mapping = {
        "trend_up": "тренд↑",
        "trend_down": "тренд↓",
        "flat": "флэт",
        "chop": "пила",
        "volatile": "волатильно",
        "unknown": "нет данных",
    }
    return mapping.get(label, label)


# ============================================================
# REPORT
# ============================================================
def build_report_text():
    now = now_local()
    lines = []
    lines.append("☀️ <b>ARGUS — утро</b> v11")
    line = now.strftime("%d.%m.%Y %H:%M")
    line += " КЛГ"
    lines.append(line)
    lines.append("")

    lines.extend(fmt_portfolio_block())
    lines.append("")
    lines.append("─" * 20)
    lines.append("")

    lines.extend(fmt_system_block())
    lines.append("")

    lines.extend(fmt_model_block())
    lines.append("")

    lines.extend(fmt_files_block())
    lines.append("")
    lines.append("─" * 20)
    lines.append("")

    levels = load_json(DATA_DIR / "levels_analysis.json")
    patterns = load_json(
        DATA_DIR / "patterns_analysis.json"
    )

    for symbol, name, db in SYMBOLS:
        candles = fetch_candles(symbol, 200)
        if not candles:
            line = "⚠️ <b>" + name + "</b> [" + db
            line += "]: нет свечей"
            lines.append(line)
            lines.append("")
            continue

        price_close = candles[-1]["close"]
        spot = fetch_spot(symbol)

        change_24h = 0
        if len(candles) >= 25:
            prev = candles[-25]["close"]
            if prev:
                change_24h = (
                    (price_close - prev) / prev * 100
                )

        line = "💰 <b>" + name + "</b> [" + db + "]: "
        if spot:
            line += fmt_price(spot) + " (spot)"
        else:
            line += fmt_price(price_close)
        line += " | " + format(change_24h, "+.2f") + "% 24ч"
        lines.append(line)

        sym_p = patterns.get("symbols", {}).get(
            symbol, {}
        )
        regime = sym_p.get("regime", {})
        if regime:
            reg_label = fmt_regime(regime)
            allowed = regime.get("trade_allowed", True)
            mark = "✅" if allowed else "⛔"
            up = regime.get("up_ratio")
            line = "  " + mark + " " + reg_label
            if up is not None:
                line += " (up " + format(up, ".2f") + ")"
            lines.append(line)

        atr = compute_atr(candles, 14)
        closes = [c["close"] for c in candles]
        rsi = compute_rsi(closes, 14)

        line = "  "
        if atr:
            line += "ATR " + fmt_price(atr)
        if rsi and rsi[-1] is not None:
            r = rsi[-1]
            if r >= 70:
                st = "перекуп"
            elif r <= 30:
                st = "перепрод"
            else:
                st = "нейтр"
            line += " | RSI " + format(r, ".1f")
            line += " " + st
        lines.append(line)

        funding_data = fetch_funding(symbol, 50)
        if funding_data:
            cur_f = funding_data[-1]["rate"] * 100
            lines.append(
                "  funding " + format(cur_f, "+.4f") + "%"
            )

        if EXPLORER_OK:
            try:
                r = explorer_mod.analyze(symbol)
                direction = r.get("direction", "NONE")
                score = r.get("score", 0)
                if direction == "NONE":
                    line = "  🎯 NONE"
                    reg = r.get("regime", {})
                    if not reg.get("trade_allowed", True):
                        line += " (вето)"
                    line += " score " + format(score, ".3f")
                    lines.append(line)
                else:
                    em = "📈" if direction == "LONG" else "📉"
                    line = "  " + em + " " + direction
                    line += " score " + format(score, ".3f")
                    lines.append(line)
            except Exception as e:
                print("explorer: " + str(e))

        lines.append("")

    corr = load_json(DATA_DIR / "correlations.json", {})
    if corr and corr.get("symbols"):
        rules = []
        for sym, d in corr["symbols"].items():
            for r in d.get("rules", []):
                if r.get("samples", 0) < 10:
                    continue
                r2 = dict(r)
                r2["symbol"] = sym.replace("USDT", "")
                rules.append(r2)
        rules.sort(
            key=lambda x: x.get("edge", 0),
            reverse=True,
        )
        if rules:
            lines.append("🧠 <b>Правила</b>")
            for r in rules[:5]:
                arrow = (
                    "↑" if r["direction"] == "up" else "↓"
                )
                line = "  " + arrow + " ["
                line += escape_html(r["symbol"]) + "] "
                line += escape_html(r["rule"])
                line += " (" + format(
                    r["confidence"] * 100, ".0f"
                ) + "%"
                line += " N=" + str(r["samples"])
                line += " e=" + format(
                    r.get("edge", 0), ".2f"
                )
                line += ")"
                lines.append(line)
            lines.append("")

    lines.append("📊 Графики ниже")

    return "\n".join(lines)


def main():
    print("Morning report v11 - start")
    print("DB2_OK = " + str(DB2_OK))

    text = build_report_text()
    print("text len: " + str(len(text)))
    send_message(text)

    levels = load_json(DATA_DIR / "levels_analysis.json")
    patterns = load_json(
        DATA_DIR / "patterns_analysis.json"
    )

    photos = []

    for symbol, name, prefix in [
        ("BTCUSDT", "BTC", "btc"),
        ("ETHUSDT", "ETH", "eth"),
    ]:
        candles = fetch_candles(symbol, 200)
        if not candles:
            continue

        sym_lvl = levels.get("symbols", {}).get(
            symbol, {}
        )
        sup = sym_lvl.get("supports", [])
        res = sym_lvl.get("resistances", [])

        path = TMP_DIR / (prefix + "_candles.png")
        plot_candles(
            symbol, candles,
            supports=sup, resistances=res,
            output_path=str(path), title=name,
        )
        photos.append((str(path), ""))

        path = TMP_DIR / (prefix + "_rsi.png")
        if plot_rsi(symbol, candles,
                    output_path=str(path)):
            photos.append((str(path), ""))

        funding_data = fetch_funding(symbol, 50)
        if funding_data:
            path = TMP_DIR / (prefix + "_funding.png")
            if plot_funding(
                symbol, funding_data,
                output_path=str(path),
            ):
                photos.append((str(path), ""))

        oi_data = fetch_oi(symbol, 100)
        if oi_data:
            path = TMP_DIR / (prefix + "_oi.png")
            if plot_oi(
                symbol, oi_data,
                output_path=str(path),
            ):
                photos.append((str(path), ""))

        sym_p = patterns.get("symbols", {}).get(
            symbol, {}
        )
        binary = sym_p.get("binary_string", "")
        if binary:
            path = TMP_DIR / (prefix + "_pattern.png")
            if plot_pattern(
                symbol, binary,
                output_path=str(path),
            ):
                photos.append((str(path), ""))

    if photos:
        first_cap = "📊 " + str(len(photos)) + " графиков"
        photos_cap = [(photos[0][0], first_cap)]
        photos_cap += photos[1:]
        ok = send_media_group(photos_cap)
        print("album: " + str(ok))

    if EXPLORER_OK and explorer_mod is not None:
        try:
            explorer_mod.close_all()
        except Exception:
            pass

    close_connection()
    if DB2_OK and close_conn_db2 is not None:
        try:
            close_conn_db2()
        except Exception:
            pass
    print("Morning report - done")


if __name__ == "__main__":
    main()