# ============================================================
# ARGUS - SIMULATOR 01 v6 [PRODUCTION]
# ------------------------------------------------------------
# v6: если позиция открыта — внутренний цикл
#     проверки каждые 10 минут (55 мин максимум).
#     Cron остаётся раз в час.
# v5: проверка по 1-минутным свечам
# ============================================================

import os
import sys
import json
import time
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MEXC_DIR = SCRIPT_DIR.parent
CRYPTO_ROOT = MEXC_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
STATE_DIR = SCRIPT_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(MEXC_DIR))
sys.path.insert(0, str(CRYPTO_ROOT))

from client import MexcClient
from db import get_connection
from db import close_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sim01")

PORTFOLIO_FILE = STATE_DIR / "portfolio.json"
POSITIONS_FILE = STATE_DIR / "positions.json"
TRADES_FILE = STATE_DIR / "trades.json"

START_BALANCE = 50.0
POSITION_SIZE = 10.0
MAX_POSITIONS = 1
TAKER_FEE = 0.0005
SLIPPAGE = 0.0005

MIN_RR = 1.5
STOP_BELOW_SUP_PCT = 0.5
ATR_MULT = 1.5
TARGET_RR = 2.0

# Внутренний цикл мониторинга
WATCH_INTERVAL_SEC = 600
WATCH_MAX_MIN = 55

SYMBOLS = ["BTCUSDT", "ETHUSDT"]

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()


def notify(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        url = "https://api.telegram.org/bot"
        url += BOT_TOKEN + "/sendMessage"
        requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception:
        pass


def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                data, f,
                ensure_ascii=False, indent=2,
            )
    except Exception as e:
        log.error("save %s: %s", path.name, e)


def get_portfolio():
    p = load_json(PORTFOLIO_FILE, None)
    if p is None:
        p = {
            "start_balance": START_BALANCE,
            "balance": START_BALANCE,
            "realized_pnl": 0.0,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "created_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }
        save_json(PORTFOLIO_FILE, p)
    return p


def get_positions():
    return load_json(POSITIONS_FILE, [])


def save_positions(positions):
    save_json(POSITIONS_FILE, positions)


def get_trades():
    return load_json(TRADES_FILE, [])


def save_trades(trades):
    save_json(TRADES_FILE, trades)


def get_price(client, symbol):
    data = client.public_get(
        "/api/v3/ticker/price",
        {"symbol": symbol},
    )
    if data and "price" in data:
        return float(data["price"])
    return None


def get_klines_1m(client, symbol, limit=180):
    data = client.public_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": "1m",
            "limit": limit,
        },
    )
    if not data or not isinstance(data, list):
        return []
    out = []
    for k in data:
        try:
            out.append({
                "ts": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
            })
        except Exception:
            continue
    return out


def load_analysis(name):
    return load_json(DATA_DIR / name, {})


def get_candles(symbol, limit=200):
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
        log.warning("candles %s: %s", symbol, e)
        return []


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period
        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def compute_atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return None
    return round(sum(trs[-period:]) / period, 4)


def get_support(symbol, price):
    lv = load_analysis("levels_analysis.json")
    sym = lv.get("symbols", {}).get(symbol, {})
    supports = sym.get("supports", [])
    below = [
        s for s in supports if s["price"] < price
    ]
    if not below:
        return None
    return min(
        below, key=lambda x: price - x["price"],
    )


def get_resistances(symbol, price):
    lv = load_analysis("levels_analysis.json")
    sym = lv.get("symbols", {}).get(symbol, {})
    resistances = sym.get("resistances", [])
    above = [
        r for r in resistances if r["price"] > price
    ]
    above.sort(key=lambda x: x["price"])
    return above


def get_markov_p10(symbol):
    p = load_analysis("patterns_analysis.json")
    sym = p.get("symbols", {}).get(symbol, {})
    mk = sym.get("markov", {})
    return mk.get("p_1_given_0", 0)


def get_rules(symbol):
    c = load_analysis("correlations.json")
    sym = c.get("symbols", {}).get(symbol, {})
    return sym.get("rules", [])


def build_levels(price, sup, resistances, atr):
    if sup:
        stop = sup["price"] * (
            1 - STOP_BELOW_SUP_PCT / 100
        )
        max_stop_dist = atr * 2.5
        if price - stop > max_stop_dist:
            stop = price - max_stop_dist
    else:
        stop = price - atr * ATR_MULT

    if stop >= price:
        return None, None, None

    risk = price - stop

    target = None
    for res in resistances:
        reward = res["price"] - price
        if reward / risk >= MIN_RR:
            target = res["price"]
            break

    if not target:
        target = price + risk * TARGET_RR

    reward = target - price
    if reward <= 0:
        return None, None, None

    rr = reward / risk
    return stop, target, rr


def check_signal(client, symbol):
    price = get_price(client, symbol)
    if not price:
        log.info("  %s: нет цены", symbol)
        return None

    candles = get_candles(symbol, 100)
    if len(candles) < 20:
        log.info(
            "  %s: мало свечей (%d)",
            symbol, len(candles),
        )
        return None

    closes = [c["close"] for c in candles]
    rsi = compute_rsi(closes, 14)
    atr = compute_atr(candles, 14)

    if not rsi or not atr:
        log.info("  %s: RSI/ATR нет", symbol)
        return None

    votes = []
    reasons = []
    details = []

    details.append("RSI=" + format(rsi, ".1f"))

    if rsi < 30:
        votes.append("RSI")
        reasons.append("RSI " + format(rsi, ".1f"))
        details.append("RSI+")

    p10 = get_markov_p10(symbol)
    details.append("P10=" + format(p10, ".2f"))
    if p10 > 0.55:
        votes.append("Markov")
        reasons.append(
            "P(1|0)=" + format(p10, ".2f")
        )
        details.append("Markov+")

    sup = get_support(symbol, price)
    resistances = get_resistances(symbol, price)

    if sup:
        gap = (price - sup["price"]) / price * 100
        details.append("gap=" + format(gap, ".2f") + "%")
        if gap <= 1.5:
            votes.append("Level")
            reasons.append(
                "у поддержки "
                + format(sup["price"], ".0f")
            )
            details.append("Level+")

    rules = get_rules(symbol)
    bull_rules = [
        r for r in rules
        if r.get("direction") == "up"
        and r.get("samples", 0) >= 5
        and r.get("confidence", 0) >= 0.6
    ]
    details.append("rules=" + str(len(bull_rules)))
    if bull_rules:
        votes.append("Rules")
        reasons.append(
            str(len(bull_rules)) + " правил"
        )
        details.append("Rules+")

    log.info(
        "  %s: price=%.2f votes=%d [%s] | %s",
        symbol,
        price,
        len(votes),
        ", ".join(votes) if votes else "нет",
        " ".join(details),
    )

    if len(votes) < 2:
        return None

    stop, target, rr = build_levels(
        price, sup, resistances, atr,
    )

    if not stop:
        log.info("  %s: стоп не построен", symbol)
        return None

    if rr < MIN_RR:
        log.info(
            "  %s: R:R=%.2f < %.1f, пропуск",
            symbol, rr, MIN_RR,
        )
        return None

    return {
        "symbol": symbol,
        "price": price,
        "stop": stop,
        "target": target,
        "rr": round(rr, 2),
        "atr": atr,
        "rsi": rsi,
        "votes": votes,
        "reasons": reasons,
        "p10": p10,
        "support": sup["price"] if sup else None,
        "resistance": target,
    }


def open_position(signal):
    portfolio = get_portfolio()
    positions = get_positions()

    if portfolio["balance"] < POSITION_SIZE:
        log.warning("мало баланса")
        return None

    for p in positions:
        if p["symbol"] == signal["symbol"]:
            return None

    if len(positions) >= MAX_POSITIONS:
        return None

    entry = signal["price"] * (1 + SLIPPAGE)
    fee = POSITION_SIZE * TAKER_FEE

    portfolio["balance"] -= POSITION_SIZE

    size_coins = POSITION_SIZE / entry

    pos = {
        "id": len(get_trades()) + len(positions) + 1,
        "symbol": signal["symbol"],
        "direction": "LONG",
        "entry_price": round(entry, 6),
        "entry_time": datetime.now(
            timezone.utc
        ).isoformat(),
        "size_usd": POSITION_SIZE,
        "size_coins": round(size_coins, 8),
        "stop": round(signal["stop"], 6),
        "target": round(signal["target"], 6),
        "entry_fee": round(fee, 6),
        "rr_planned": signal["rr"],
        "rsi_entry": signal["rsi"],
        "atr_entry": signal["atr"],
        "markov_p10": signal["p10"],
        "votes": signal["votes"],
        "reasons": signal["reasons"],
    }

    positions.append(pos)
    save_positions(positions)
    save_json(PORTFOLIO_FILE, portfolio)

    lines = []
    lines.append("🟢 ОТКРЫТА LONG")
    lines.append(
        signal["symbol"].replace("USDT", "")
    )
    lines.append(
        "Вход: $" + format(entry, ".4f")
    )
    lines.append(
        "Стоп: $" + format(signal["stop"], ".4f")
    )
    lines.append(
        "Цель: $" + format(signal["target"], ".4f")
    )
    lines.append("R:R 1:" + str(signal["rr"]))
    lines.append(
        "Причины: " + ", ".join(signal["reasons"])
    )
    notify("\n".join(lines))
    log.info("OPEN " + signal["symbol"])

    return pos


def close_position(pos, exit_price, reason):
    portfolio = get_portfolio()
    positions = get_positions()

    exit_real = exit_price * (1 - SLIPPAGE)
    proceeds = pos["size_coins"] * exit_real
    exit_fee = proceeds * TAKER_FEE

    entry_cost = pos["size_usd"]
    pnl = proceeds - entry_cost
    pnl -= pos["entry_fee"]
    pnl -= exit_fee
    pnl_pct = pnl / entry_cost * 100

    portfolio["balance"] += entry_cost + pnl
    portfolio["realized_pnl"] += pnl
    portfolio["total_trades"] += 1
    if pnl > 0:
        portfolio["wins"] += 1
    else:
        portfolio["losses"] += 1

    positions = [
        p for p in positions
        if p["id"] != pos["id"]
    ]
    save_positions(positions)

    trades = get_trades()
    trade = dict(pos)
    trade["exit_price"] = round(exit_real, 6)
    trade["exit_time"] = datetime.now(
        timezone.utc
    ).isoformat()
    trade["exit_reason"] = reason
    trade["pnl_usd"] = round(pnl, 4)
    trade["pnl_pct"] = round(pnl_pct, 3)
    trade["exit_fee"] = round(exit_fee, 6)
    trades.append(trade)
    save_trades(trades)
    save_json(PORTFOLIO_FILE, portfolio)

    emoji = "✅" if pnl > 0 else "❌"
    lines = []
    lines.append(emoji + " ЗАКРЫТА")
    lines.append(
        pos["symbol"].replace("USDT", "")
    )
    lines.append("Причина: " + reason)
    lines.append(
        "PnL: $" + format(pnl, "+.4f")
        + " (" + format(pnl_pct, "+.2f") + "%)"
    )
    lines.append(
        "Баланс: $" + format(
            portfolio["balance"], ".2f"
        )
    )
    notify("\n".join(lines))
    log.info(
        "CLOSE %s PnL=%.4f (%s)",
        pos["symbol"], pnl, reason,
    )
    return True


def check_one_position(client, pos):
    """Проверка одной позиции по 1m свечам.
    Возвращает True если закрыта."""
    try:
        entry_dt = datetime.fromisoformat(
            pos["entry_time"]
        )
    except Exception:
        return False

    entry_ms = int(entry_dt.timestamp() * 1000)

    klines = get_klines_1m(
        client, pos["symbol"], limit=180,
    )
    if not klines:
        log.warning(
            "  %s: 1m свечи недоступны",
            pos["symbol"],
        )
        return False

    stop = pos["stop"]
    target = pos["target"]

    for k in klines:
        if k["ts"] < entry_ms:
            continue

        # Приоритет стопу (худший случай)
        if k["low"] <= stop:
            ts_utc = datetime.fromtimestamp(
                k["ts"] / 1000, tz=timezone.utc,
            )
            log.info(
                "  %s: STOP по свече %s",
                pos["symbol"],
                ts_utc.strftime("%H:%M"),
            )
            return close_position(pos, stop, "stop")

        if k["high"] >= target:
            ts_utc = datetime.fromtimestamp(
                k["ts"] / 1000, tz=timezone.utc,
            )
            log.info(
                "  %s: TARGET по свече %s",
                pos["symbol"],
                ts_utc.strftime("%H:%M"),
            )
            return close_position(
                pos, target, "target",
            )

    return False


def watch_position(client):
    """
    Внутренний цикл мониторинга.
    Проверяет позицию каждые 10 мин,
    максимум 55 минут.
    """
    log.info("")
    log.info("=" * 50)
    log.info(
        "WATCH LOOP start (interval=%ds, max=%dmin)",
        WATCH_INTERVAL_SEC,
        WATCH_MAX_MIN,
    )
    log.info("=" * 50)

    started = time.time()
    max_seconds = WATCH_MAX_MIN * 60
    iteration = 0

    while True:
        elapsed = time.time() - started
        if elapsed > max_seconds:
            log.info(
                "  watch: время вышло (%.0fс)",
                elapsed,
            )
            break

        positions = get_positions()
        if not positions:
            log.info("  watch: позиций нет, выход")
            break

        iteration += 1
        log.info(
            "  watch #%d (elapsed %.0fс)",
            iteration, elapsed,
        )

        for pos in list(positions):
            closed = check_one_position(
                client, pos,
            )
            if closed:
                log.info(
                    "  watch: позиция закрыта"
                )
                break
        else:
            # Ни одна не закрыта — ждём
            remaining = max_seconds - (
                time.time() - started
            )
            if remaining < WATCH_INTERVAL_SEC:
                log.info(
                    "  watch: осталось %.0fс — выход",
                    откры remaining,
                )
                break
            log.info(
                "  watch: sleep %ds",
                WATCH_INTERVAL_SEC,
            )
            time.sleep(WATCH_INTERVAL_SEC)
            continue
        # Если закрыли — выходим
        break

    log.info("WATCH LOOP done")


def main():
    log.info("=" * 50)
    log.info("SIMULATOR 01 v6")
    log.info("=" * 50)

    client = MexcClient()

    # 1. Проверяем существующие позиции (могли быть с прошлого часа)
    positions = get_positions()
    if positions:
        for pos in list(positions):
            check_one_position(client, pos)

    # 2. Ищем сигнал (если позиций нет)
    positions = get_positions()
    if len(positions) < MAX_POSITIONS:
        for symbol in SYMBOLS:
            signal = check_signal(client, symbol)
            if signal:
                open_position(signal)
                break

    # 3. Если естьтая — мониторим каждые 10 мин
    positions = get_positions()
    if positions:
        watch_position(client)

    # 4. Итоги
    portfolio = get_portfolio()
    positions = get_positions()
    trades = get_trades()

    log.info("")
    log.info("=== ИТОГИ ===")
    log.info(
        "Баланс: $%.2f", portfolio["balance"]
    )
    log.info(
        "PnL: $%+.4f", portfolio["realized_pnl"]
    )
    log.info(
        "Сделок: %d (%d W / %d L)",
        portfolio["total_trades"],
        portfolio["wins"],
        portfolio["losses"],
    )
    log.info("Открыто: %d", len(positions))
    if trades:
        wins = [
            t for t in trades if t["pnl_usd"] > 0
        ]
        wr = len(wins) / len(trades) * 100
        avg = sum(
            t["pnl_usd"] for t in trades
        ) / len(trades)
        log.info("Win rate: %.1f%%", wr)
        log.info("Avg PnL: $%+.4f", avg)

    close_connection()


if __name__ == "__main__":
    main()