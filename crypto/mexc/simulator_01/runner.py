# ============================================================
# ARGUS - SIMULATOR 01 v8.1 [PRODUCTION]
# ------------------------------------------------------------
# v8.1: после закрытия сразу ищем новый сигнал
# v8:   - check через 1h свечи Supabase
#       - time exit только в плюсе
#       - комиссии учтены
# v7:   - защита от кривых данных
# ============================================================

import os
import sys
import json
import time
import uuid
import logging
import requests
from datetime import datetime, timezone, timedelta
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
COOLDOWN_FILE = STATE_DIR / "cooldowns.json"

START_BALANCE = 50.0
POSITION_SIZE = 10.0
MAX_POSITIONS = 1
TAKER_FEE = 0.0005
SLIPPAGE = 0.0005

MIN_RR = 1.5
STOP_BELOW_SUP_PCT = 0.5
ATR_MULT = 1.5
TARGET_RR = 2.0
MAX_STOP_ATR = 2.5

COOLDOWN_HOURS = 2
ANALYSIS_MAX_AGE_H = 3
CANDLES_MAX_AGE_H = 2
RULES_MAX_AGE_H = 24

TIME_EXIT_HOURS = 24

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
        with open,
(path, "w", encoding="utf-8           ") as f:
            json.d "ump(
                data, f,
               total ensure_ascii=False, indent=2,
            )
_t    except Exception as e:
        logrades.error("save %s: %":s", path.name, e)


# ============================================================
# FRESHNESS
# ============================================================
def file_age_hours(path):
    if not path.exists():
        return None
    return (
        time.time() - path.stat().st_mtime
    ) / 3600


def load_analysis(name, max_age_h=ANALYSIS_MAX_AGE_H):
    path = DATA_DIR / name
    age = file_age_hours(path)
    if age is None:
        log.warning("%s: missing", name)
        return {}
    if age > max_age_h:
        log.warning(
            "%s: stale (%.1fh > %dh)",
            name, age, max_age_h,
        )
        return {}
    return load_json(path, {})


# ============================================================
# STATE
# ============================================================
def get_portfolio():
    p = load_json(PORTFOLIO_FILE, None)
    if p is None:
        p = {
            "start_balance": START_BALANCE,
            "balance": START_BALANCE,
            "realized_pnl": 0.0 0,
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


def save_positions(p):
    save_json(POSITIONS_FILE, p)


def get_trades():
    return load_json(TRADES_FILE, [])


def save_trades(t):
    save_json(TRADES_FILE, t)


def get_cooldowns():
    return load_json(COOLDOWN_FILE, {})


def set_cooldown(symbol):
    cd = get_cooldowns()
    cd[symbol] = datetime.now(
        timezone.utc
    ).isoformat()
    save_json(COOLDOWN_FILE, cd)


def in_cooldown(symbol):
    cd = get_cooldowns()
    ts = cd.get(symbol)
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts)
    except Exception:
        return False
    delta = (
        datetime.now(timezone.utc) - dt
    ).total_seconds() / 3600
    return delta < COOLDOWN_HOURS


# ============================================================
# MEXC
# ============================================================
def get_price(client, symbol):
    try:
        data = client.public_get(
            "/api/v3/ticker/price",
            {"symbol": symbol},
        )
        if data and "price" in data:
            return float(data["price"])
    except Exception as e:
        log.warning("price %s: %s", symbol, e)
    return None


def get_klines_1m(client, symbol, limit=180,
                  start_ms=None):
    params = {
        "symbol": symbol,
        "interval": "1m",
        "limit": limit,
    }
    if start_ms is not None:
        params["startTime"] = start_ms
    data = client.public_get(
        "/api/v3/klines", params,
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


# ============================================================
# DB CANDLES
# ============================================================
def get_candles_range(symbol, start_dt):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, "
                    "low, close, volume FROM candles "
                    "WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "AND timestamp >= %s "
                    "ORDER BY timestamp",
                    (symbol, start_dt),
                )
                rows = cur.fetchall()
                out = []
                for r in rows:
                    out.append({
                        "timestamp": r[0],
                        "open": float(r[1]) if r[1] else 0,
                        "high": float(r[2]) if r[2] else 0,
                        "low": float(r[3]) if r[3] else 0,
                        "close": float(r[4]) if r[4] else 0,
                        "volume": float(r[5]) if r[5] else 0,
                    })
                return out
    except Exception as e:
        log.warning("candles range %s: %s", symbol, e)
        return []


def get_candles(symbol, limit=200):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, "
                    "low, close, volume FROM candles "
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
                        "open": float(r[1]) if r[1] else 0,
                        "high": float(r[2]) if r[2] else 0,
                        "low": float(r[3]) if r[3] else 0,
                        "close": float(r[4]) if r[4] else 0,
                        "volume": float(r[5]) if r[5] else 0,
                    })
                return out
    except Exception as e:
        log.warning("candles %s: %s", symbol, e)
        return []


def candles_are_fresh(candles):
    if not candles:
        return False
    last = candles[-1].get("timestamp")
    if last is None:
        return False
    try:
        if isinstance(last, datetime):
            age = (
                datetime.now(timezone.utc) - last
            ).total_seconds() / 3600
        else:
            return True
        if age > CANDLES_MAX_AGE_H:
            log.warning(
                "candles stale: %.1fh", age
            )
            return False
        return True
    except Exception:
        return True


# ============================================================
# INDICATORS
# ============================================================
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


# ============================================================
# ANALYSIS HELPERS
# ============================================================
def get_support(symbol, price):
    lv = load_analysis("levels_analysis.json")
    sym = lv.get("symbols", {}).get(symbol, {})
    supports = sym.get("supports", [])
    below = [
        s for s in supports
        if s.get("price", 0) < price
    ]
    if not below:
        return None
    return min(
        below,
        key=lambda x: price - x["price"],
    )


def get_resistances(symbol, price):
    lv = load_analysis("levels_analysis.json")
    sym = lv.get("symbols", {}).get(symbol, {})
    resistances = sym.get("resistances", [])
    above = [
        r for r in resistances
        if r.get("price", 0) > price
    ]
    above.sort(key=lambda x: x["price"])
    return above


def get_markov_p10(symbol):
    p = load_analysis("patterns_analysis.json")
    sym = p.get("symbols", {}).get(symbol, {})
    mk = sym.get("markov", {})
    return mk.get("p_1_given_0", 0)


def get_rules(symbol):
    c = load_analysis(
        "correlations.json",
        max_age_h=RULES_MAX_AGE_H,
    )
    sym = c.get("symbols", {}).get(symbol, {})
    return sym.get("rules", [])


def filter_bull_rules(rules):
    out = []
    for r in rules:
        if r.get("direction") != "up":
            continue
        if r.get("samples", 0) < 5:
            continue
        if r.get("confidence", 0) < 0.6:
            continue
        out.append(r)
    return out


# ============================================================
# LEVELS
# ============================================================
def build_levels(price, sup, resistances, atr):
    if sup:
        stop = sup["price"] * (
            1 - STOP_BELOW_SUP_PCT / 100
        )
        stop_dist = price - stop
        if stop_dist > atr * MAX_STOP_ATR:
            log.info(
                "  sup too far: %.2f > %.2f",
                stop_dist,
                atr * MAX_STOP_ATR,
            )
            return None, None, None
        if stop_dist <= 0:
            return None, None, None
    else:
        stop = price - atr * ATR_MULT

    if stop >= price:
        return None, None, None

    risk = price - stop
    if risk <= 0:
        return None, None, None

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


# ============================================================
# SIGNAL
# ============================================================
def check_signal(client, symbol):
    if in_cooldown(symbol):
        log.info("  %s: in cooldown", symbol)
        return None

    price = get_price(client, symbol)
    if not price:
        log.info("  %s: no price", symbol)
        return None

    candles = get_candles(symbol, 100)
    if len(candles) < 20:
        log.info(
            "  %s: few candles (%d)",
            symbol, len(candles),
        )
        return None

    if not candles_are_fresh(candles):
        log.info("  %s: candles stale", symbol)
        return None

    closes = [c["close"] for c in candles]
    rsi = compute_rsi(closes, 14)
    atr = compute_atr(candles, 14)

    if not rsi or not atr:
        log.info("  %s: no RSI/ATR", symbol)
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
        details.append(
            "gap=" + format(gap, ".2f") + "%"
        )
        if gap <= 1.5:
            votes.append("Level")
            reasons.append(
                "near " + format(
                    sup["price"], ".0f"
                )
            )
            details.append("Level+")

    rules = get_rules(symbol)
    bull_rules = filter_bull_rules(rules)
    details.append("rules=" + str(len(bull_rules)))
    if bull_rules:
        votes.append("Rules")
        reasons.append(
            str(len(bull_rules)) + " rules"
        )
        details.append("Rules+")

    log.info(
        "  %s: price=%.2f votes=%d [%s] | %s",
        symbol,
        price,
        len(votes),
        ", ".join(votes) if votes else "none",
        " ".join(details),
    )

    if len(votes) < 2:
        return None

    stop, target, rr = build_levels(
        price, sup, resistances, atr,
    )

    if not stop:
        log.info("  %s: no stop", symbol)
        return None

    if not (stop < price < target):
        log.warning(
            "  %s: sanity fail stop=%.4f "
            "price=%.4f target=%.4f",
            symbol, stop, price, target,
        )
        return None

    if rr < MIN_RR:
        log.info(
            "  %s: R:R=%.2f < %.1f, skip",
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


# ============================================================
# TRY OPEN HELPER
# ============================================================
def try_open_new(client):
    """Пробует открыть новую позицию. Возвращает True если открыл."""
    if len(get_positions()) >= MAX_POSITIONS:
        return False
    for symbol in SYMBOLS:
        signal = check_signal(client, symbol)
        if signal:
            pos = open_position(signal)
            if pos:
                return True
    return False


# ============================================================
# OPEN / CLOSE
# ============================================================
def open_position(signal):
    portfolio = get_portfolio()
    positions = get_positions()

    if portfolio["balance"] < POSITION_SIZE:
        log.warning("low balance")
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
        "id": str(uuid.uuid4()),
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

    lines = [
        "LONG OPENED",
        signal["symbol"].replace("USDT", ""),
        "Entry: $" + format(entry, ".4f"),
        "Stop: $" + format(signal["stop"], ".4f"),
        "Target: $" + format(
            signal["target"], ".4f"
        ),
        "R:R 1:" + str(signal["rr"]),
        "Reasons: " + ", ".join(
            signal["reasons"]
        ),
    ]
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

    if reason == "stop":
        set_cooldown(pos["symbol"])

    emoji = "[WIN]" if pnl > 0 else "[LOSS]"
    lines = [
        emoji + " CLOSED",
        pos["symbol"].replace("USDT", ""),
        "Reason: " + reason,
        "PnL: $" + format(pnl, "+.4f")
        + " (" + format(pnl_pct, "+.2f") + "%)",
        "Balance: $" + format(
            portfolio["balance"], ".2f"
        ),
    ]
    notify("\n".join(lines))
    log.info(
        "CLOSE %s PnL=%.4f (%s)",
        pos["symbol"], pnl, reason,
    )
    return True


# ============================================================
# POSITION CHECK
# ============================================================
def check_stop_target_1h(pos):
    try:
        entry_dt = datetime.fromisoformat(
            pos["entry_time"]
        )
    except Exception:
        return False, None, None

    candles = get_candles_range(
        pos["symbol"], entry_dt,
    )
    if not candles:
        log.warning(
            "  %s: no 1h candles from entry",
            pos["symbol"],
        )
        return False, None, None

    stop = pos["stop"]
    target = pos["target"]

    for c in candles:
        hit_stop = c["low"] <= stop
        hit_target = c["high"] >= target

        if hit_stop and hit_target:
            log.info(
                "  %s: both hit %s",
                pos["symbol"],
                c["timestamp"],
            )
            return check_stop_target_1m(
                pos, c["timestamp"],
            )

        if hit_stop:
            log.info(
                "  %s: STOP at 1h %s",
                pos["symbol"],
                c["timestamp"],
            )
            return True, stop, "stop"

        if hit_target:
            log.info(
                "  %s: TARGET at 1h %s",
                pos["symbol"],
                c["timestamp"],
            )
            return True, target, "target"

    return False, None, None


def check_stop_target_1m(pos, hour_ts):
    if isinstance(hour_ts, str):
        try:
            hour_ts = datetime.fromisoformat(hour_ts)
        except Exception:
            return False, None, None

    if hour_ts.tzinfo is None:
        hour_ts = hour_ts.replace(tzinfo=timezone.utc)

    start_ms = int(hour_ts.timestamp() * 1000)

    client = MexcClient()
    klines = get_klines_1m(
        client, pos["symbol"],
        limit=60, start_ms=start_ms,
    )
    if not klines:
        return False, None, None

    stop = pos["stop"]
    target = pos["target"]

    for k in klines:
        if k["low"] <= stop:
            return True, stop, "stop"
        if k["high"] >= target:
            return True, target, "target"

    return False, None, None


def check_time_exit(pos, current_price):
    try:
        entry_dt = datetime.fromisoformat(
            pos["entry_time"]
        )
    except Exception:
        return False

    age_h = (
        datetime.now(timezone.utc) - entry_dt
    ).total_seconds() / 3600

    if age_h < TIME_EXIT_HOURS:
        return False

    exit_real = current_price * (1 - SLIPPAGE)
    proceeds = pos["size_coins"] * exit_real
    exit_fee = proceeds * TAKER_FEE

    pnl = proceeds - pos["size_usd"]
    pnl -= pos["entry_fee"]
    pnl -= exit_fee

    if pnl > 0:
        log.info(
            "  %s: TIME EXIT (age=%.1fh, pnl=%.4f)",
            pos["symbol"], age_h, pnl,
        )
        return True

    return False


def check_one_position(client, pos):
    closed, exit_price, reason = check_stop_target_1h(
        pos,
    )
    if closed:
        return close_position(
            pos, exit_price, reason,
        )

    price = get_price(client, pos["symbol"])
    if price and check_time_exit(pos, price):
        return close_position(
            pos, price, "time_exit",
        )

    return False


# ============================================================
# WATCH
# ============================================================
def watch_position(client):
    log.info("")
    log.info("=" * 50)
    log.info(
        "WATCH start interval=%ds max=%dmin",
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
                "  watch: time limit (%.0fs)",
                elapsed,
            )
            break

        positions = get_positions()

        # Нет позиции - пробуем открыть
        if not positions:
            log.info("  watch: no positions, try open")
            opened = try_open_new(client)
            if not opened:
                log.info(
                    "  watch: no signal, exit"
                )
                break
            positions = get_positions()

        iteration += 1
        log.info(
            "  watch #%d (%.0fs)",
            iteration, elapsed,
        )

        closed = False
        for pos in list(positions):
            if check_one_position(client, pos):
                log.info("  position closed")
                closed = True
                break

        if closed:
            # Сразу пробуем открыть новую
            log.info(
                "  watch: position closed, "
                "search new signal"
            )
            opened = try_open_new(client)
            if not opened:
                log.info(
                    "  watch: no new signal, exit"
                )
                break
            # Продолжаем watch новой позиции
            continue

        remaining = max_seconds - (
            time.time() - started
        )
        if remaining < WATCH_INTERVAL_SEC:
            log.info(
                "  watch: %.0fs left, exit",
                remaining,
            )
            break

        log.info(
            "  watch: sleep %ds",
            WATCH_INTERVAL_SEC,
        )
        time.sleep(WATCH_INTERVAL_SEC)

    log.info("WATCH done")


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=" * 50)
    log.info("SIMULATOR 01 v8.1")
    log.info("=" * 50)

    client = MexcClient()

    # Проверяем существующие позиции
    positions = get_positions()
    if positions:
        for pos in list(positions):
            check_one_position(client, pos)

    # Ищем новую если нет
    positions = get_positions()
    if len(positions) < MAX_POSITIONS:
        try_open_new(client)

    # Watch
    positions = get_positions()
    if positions:
        watch_position(client)

    portfolio = get_portfolio()
    positions = get_positions()
    trades = get_trades()

    log.info("")
    log.info("=== SUMMARY ===")
    log.info(
        "Balance: $%.2f", portfolio["balance"]
    )
    log.info(
        "PnL: $%+.4f", portfolio["realized_pnl"]
    )
    log.info(
        "Trades: %d (%d W / %d L)",
        portfolio["total_trades"],
        portfolio["wins"],
        portfolio["losses"],
    )
    log.info("Open: %d", len(positions))
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