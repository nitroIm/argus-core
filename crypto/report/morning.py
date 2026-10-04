# ============================================================
# ARGUS - УТРЕННИЙ ОТЧЁТ v7
# ------------------------------------------------------------
# v7: + PORTFOLIO block (balance, PnL, trades, positions).
#     + last 24h closed trades summary.
# v6: use explorer.analyze() instead of risk.build_setup.
#     Show regime + direction + edge.
# v5: + торговые сетапы с рисками (R:R, стоп, цель)
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
STATE_DIR = (
    CRYPTO_ROOT / "mexc" / "simulator_01" / "state"
)
TMP_DIR = Path("/tmp/argus_charts")
TMP_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(
    0, str(CRYPTO_ROOT / "mexc" / "simulator_01")
)

from db import get_connection
from db import close_connection
from report.charts import plot_candles
from report.charts import plot_pattern
from report.charts import plot_rsi
from report.charts import plot_funding
from report.charts import plot_oi
from report.charts import compute_rsi
from report.risk import compute_atr

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
    {
        "name": "Bybit",
        "url": "https://api.bybit.com/v5/market/tickers?category=spot&symbol={sym}",
        "parse": lambda j: float(j["result"]["list"][0]["lastPrice"]),
    },
    {
        "name": "OKX",
        "url": "https://www.okx.com/api/v5/market/ticker?instId={sym_dash}",
        "parse": lambda j: float(j["data"][0]["last"]),
        "sym_fmt": lambda s: s.replace("USDT", "-USDT"),
    },
]

EXPLORER_OK = False
try:
    import explorer as explorer_mod
    EXPLORER_OK = True
except Exception as e:
    print("explorer import failed: " + str(e))
    explorer_mod = None


# ============================================================
# PORTFOLIO / TRADES
# ============================================================
def load_state_json(name, default=None):
    if default is None:
        default = {}
    path = STATE_DIR / name
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_portfolio():
    return load_state_json("portfolio.json", {})


def load_positions():
    return load_state_json("positions.json", [])


def load_trades():
    return load_state_json("trades.json", [])


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


def _signed_usd(v):
    sign = "+" if v >= 0 else "-"
    return sign + "$" + format(abs(v), ".2f")


def _signed_pct(v):
    sign = "+" if v >= 0 else ""
    return sign + format(v, ".2f") + "%"


def fmt_portfolio_block():
    """Portfolio + last 24h trading summary."""
    p = load_portfolio()
    lines = []

    if not p:
        lines.append("💼 <b>Портфель</b>")
        lines.append("  файл portfolio.json не найден")
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
    line += " (старт $" + format(start, ".2f") + ")"
    lines.append(line)

    line = "  PnL всего: " + _signed_usd(pnl)
    line += " (" + _signed_pct(pnl_pct) + ")"
    lines.append(line)

    line = "  Сделок: " + str(total)
    line += " (" + str(wins) + "W/"
    line += str(losses) + "L"
    line += " | WR " + format(wr, ".1f") + "%)"
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

    # --- Last 24h closed trades ---
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
    lines.append(line)

    line = "  PnL за день: " + _signed_usd(pnl_24)
    lines.append(line)

    if len(recent) >= 2:
        sorted_t = sorted(
            recent,
            key=lambda x: float(x.get("pnl_usd", 0)),
            reverse=True,
        )
        best = sorted_t[0]
        worst = sorted_t[-1]
        for label, t in [
            ("Лучшая", best),
            ("Худшая", worst),
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
# SPOT
# ============================================================
def fetch_spot(symbol):
    for src in SPOT_SOURCES:
        try:
            url = src["url"]
            if "sym_fmt" in src:
                url = url.format(
                    sym_dash=src["sym_fmt"](symbol)
                )
            else:
                url = url.format(sym=symbol)
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                return src["parse"](r.json())
        except Exception:
            continue
    return None


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


def fmt_price(p):
    if p is None:
        return "?"
    if p >= 1000:
        return "$" + format(int(p), ",")
    if p >= 1:
        return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")


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


def fetch_candles(symbol, limit=200):
    try:
        with get_connection() as conn:
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
        print("candles: " + str(e))
        return []


def fetch_funding(symbol, limit=50):
    try:
        with get_connection() as conn:
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
                    {
                        "timestamp": r[0],
                        "rate": float(r[1]),
                    }
                    for r in rows
                ]
    except Exception:
        return []


def fetch_oi(symbol, limit=100):
    try:
        with get_connection() as conn:
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
                    {
                        "timestamp": r[0],
                        "oi": float(r[1]),
                    }
                    for r in rows
                ]
    except Exception:
        return []


def fmt_regime(regime):
    if not regime:
        return "?"
    label = regime.get("label", "?")
    mapping = {
        "trend_up": "тренд вверх",
        "trend_down": "тренд вниз",
        "flat": "флэт",
        "chop": "пила",
        "volatile": "волатильно",
        "unknown": "нет данных",
    }
    return mapping.get(label, label)


def build_report_text():
    now = datetime.now(timezone.utc)
    lines = []
    lines.append("☀️ ARGUS — утренний отчёт v7")
    lines.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    lines.append("")

    # --- PORTFOLIO BLOCK (first!) ---
    lines.extend(fmt_portfolio_block())
    lines.append("")
    lines.append("─" * 20)
    lines.append("")

    levels = load_json(
        DATA_DIR / "levels_analysis.json"
    )
    patterns = load_json(
        DATA_DIR / "patterns_analysis.json"
    )
    corr = load_json(DATA_DIR / "correlations.json")

    pairs = [
        ("BTCUSDT", "BTC"),
        ("ETHUSDT", "ETH"),
    ]

    trade_lines = []

    for symbol, name in pairs:
        candles = fetch_candles(symbol, 200)
        if not candles:
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

        line = "💰 <b>" + name + "</b>: "
        if spot:
            line += fmt_price(spot) + " (spot)"
        else:
            line += fmt_price(price_close)
        line += " | свеча: "
        line += format(change_24h, "+.2f") + "% 24ч"
        lines.append(line)

        sym_p = patterns.get("symbols", {}).get(symbol, {})
        regime = sym_p.get("regime", {})
        if regime:
            reg_label = fmt_regime(regime)
            allowed = regime.get("trade_allowed", True)
            mark = "✅" if allowed else "⛔"
            line = "  " + mark + " Режим: " + reg_label
            up_ratio = regime.get("up_ratio")
            if up_ratio is not None:
                line += " (up=" + format(up_ratio, ".2f") + ")"
            lines.append(line)

        atr = compute_atr(candles, 14)
        if atr:
            lines.append("  ATR(14): " + fmt_price(atr))

        closes = [c["close"] for c in candles]
        rsi = compute_rsi(closes, 14)
        if rsi and rsi[-1] is not None:
            r = rsi[-1]
            if r >= 70:
                state = "перекуплен"
            elif r <= 30:
                state = "перепродан"
            else:
                state = "нейтрально"
            lines.append(
                "  RSI(14): " + format(r, ".1f")
                + " - " + state
            )

        funding_data = fetch_funding(symbol, 50)
        if funding_data:
            cur_f = funding_data[-1]["rate"] * 100
            if cur_f > 0.01:
                state = "перегрев лонгов"
            elif cur_f < -0.01:
                state = "перегрев шортов"
            else:
                state = "сбалансирован"
            lines.append(
                "  Funding: " + format(cur_f, "+.4f")
                + "% - " + state
            )

        oi_data = fetch_oi(symbol, 100)
        if oi_data and len(oi_data) >= 2:
            first = oi_data[0]["oi"]
            cur = oi_data[-1]["oi"]
            if first:
                oi_ch = (cur - first) / first * 100
                if oi_ch > 1:
                    state = "тренд усиливается"
                elif oi_ch < -1:
                    state = "тренд слабеет"
                else:
                    state = "флэт"
                lines.append(
                    "  OI: " + format(oi_ch, "+.2f")
                    + "% - " + state
                )

        if EXPLORER_OK:
            try:
                r = explorer_mod.analyze(symbol)
                direction = r.get("direction", "NONE")
                score = r.get("score", 0)

                if direction == "NONE":
                    line = "  🎯 Сигнал: NONE"
                    reg = r.get("regime", {})
                    if not reg.get("trade_allowed", True):
                        line += " (вето: "
                        line += fmt_regime(reg) + ")"
                    line += " | score="
                    line += format(score, ".3f")
                    lines.append(line)
                else:
                    emoji = (
                        "📈" if direction == "LONG"
                        else "📉"
                    )
                    line = "  " + emoji + " Сигнал: "
                    line += direction
                    line += " | score="
                    line += format(score, ".3f")
                    lines.append(line)
                    trade_lines.append(
                        "[" + name + "] " + direction
                        + " score=" + format(score, ".3f")
                    )
            except Exception as e:
                print("explorer fail: " + str(e))

        lines.append("")

    if trade_lines:
        lines.append("💼 <b>Торговые сигналы</b>")
        for t in trade_lines:
            lines.append("  " + escape_html(t))
        lines.append("")
    else:
        lines.append("💼 Сигналов нет — не торгуем")
        lines.append("")

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
            lines.append(
                "🧠 Закономерности (edge > 0.15):"
            )
            for r in rules[:5]:
                arrow = (
                    "↑" if r["direction"] == "up" else "↓"
                )
                sym_safe = escape_html(r["symbol"])
                rule_safe = escape_html(r["rule"])
                line = "  " + arrow + " ["
                line += sym_safe + "] "
                line += rule_safe
                line += " (" + format(
                    r["confidence"] * 100, ".0f"
                ) + "%"
                line += ", N=" + str(r["samples"])
                edge = r.get("edge", 0)
                line += ", edge=" + format(edge, ".2f")
                line += ")"
                lines.append(line)
            lines.append("")

    lines.append("📊 Графики ниже одним альбомом")

    return "\n".join(lines)


def main():
    print("Morning report v7 - start")

    text = build_report_text()
    print("text len: " + str(len(text)))
    send_message(text)

    levels = load_json(
        DATA_DIR / "levels_analysis.json"
    )
    patterns = load_json(
        DATA_DIR / "patterns_analysis.json"
    )

    pairs = [
        ("BTCUSDT", "BTC", "btc"),
        ("ETHUSDT", "ETH", "eth"),
    ]

    photos = []

    for symbol, name, prefix in pairs:
        print("--- " + symbol)

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
            output_path=str(path),
            title=name,
        )
        photos.append((str(path), ""))

        path = TMP_DIR / (prefix + "_rsi.png")
        if plot_rsi(
            symbol, candles, output_path=str(path)
        ):
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
        first_cap = "📊 " + str(len(photos))
        first_cap += " графиков"
        photos_cap = [(photos[0][0], first_cap)]
        photos_cap += photos[1:]
        ok = send_media_group(photos_cap)
        print("album: " + str(ok))
    else:
        print("no charts")

    if EXPLORER_OK and explorer_mod is not None:
        try:
            explorer_mod.close_all()
        except Exception:
            pass

    close_connection()
    print("Morning report - done")


if __name__ == "__main__":
    main()