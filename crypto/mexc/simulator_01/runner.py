# ============================================================
# ARGUS - SIMULATOR 01 v9.8
# ------------------------------------------------------------
# v9.8: get_cooldowns auto-cleans expired entries.
# v9.7: fallback to ATR stop when level is too far.
# v9.6: auto-locate db2.py anywhere in repo.
# v9.5: SOL/BNB candles from DB2, BTC/ETH from DB1.
# v9.4: fix slippage direction for SHORT.
# v9.3: fix SyntaxError на trade["exit_time"].
# v9.2: fix UnboundLocalError в check_signal.
# v9.1: MAX_POSITIONS=3, POSITION_SIZE=8, TIME_EXIT=12.
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
sys.path.insert(0, str(SCRIPT_DIR))

for _p in CRYPTO_ROOT.rglob("db2.py"):
    _d = str(_p.parent)
    if "__pycache__" in _d:
        continue
    if _d not in sys.path:
        sys.path.insert(0, _d)
    break

from client import MexcClient
from db import get_connection, close_connection
from db2 import get_connection as get_conn_db2
from db2 import close_connection as close_conn_db2

try:
    import explorer as explorer_mod
    EXPLORER_OK = True
except Exception:
    explorer_mod = None
    EXPLORER_OK = False

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
POSITION_SIZE = 8.0
MAX_POSITIONS = 3
TAKER_FEE = 0.0005
SLIPPAGE = 0.0005

MIN_RR = 1.5
STOP_BELOW_SUP_PCT = 0.5
STOP_ABOVE_RES_PCT = 0.5
ATR_MULT = 1.5
TARGET_RR = 2.0
MAX_STOP_ATR = 2.5

COOLDOWN_HOURS = 2
ANALYSIS_MAX_AGE_H = 3
CANDLES_MAX_AGE_H = 2
RULES_MAX_AGE_H = 24

TIME_EXIT_HOURS = 12

WATCH_INTERVAL_SEC = 600
WATCH_MAX_MIN = 55

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
DB2_SYMBOLS = {"SOLUSDT", "BNBUSDT"}

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()


def candles_conn(symbol):
    if symbol in DB2_SYMBOLS:
        return get_conn_db2()
    return get_connection()


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
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error("save %s: %s", path.name, e)


def file_age_hours(path):
    if not path.exists():
        return None
    return (time.time() - path.stat().st_mtime) / 3600


def load_analysis(name, max_age_h=ANALYSIS_MAX_AGE_H):
    path = DATA_DIR / name
    age = file_age_hours(path)
    if age is None:
        log.warning("%s: missing", name)
        return {}
    if age > max_age_h:
        log.warning("%s: stale (%.1fh > %dh)",
                    name, age, max_age_h)
        return {}
    return load_json(path, {})


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
            "created_at": datetime.now(timezone.utc).isoformat(),
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
    """Load cooldowns, drop expired ones."""
    cd = load_json(COOLDOWN_FILE, {})
    if not cd:
        return {}
    now = datetime.now(timezone.utc)
    clean = {}
    changed = False
    for sym, ts in cd.items():
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            changed = True
            continue
        age_h = (now - dt).total_seconds() / 3600
        if age_h < COOLDOWN_HOURS * 2:
            clean[sym] = ts
        else:
            changed = True
    if changed:
        save_json(COOLDOWN_FILE, clean)
    return clean


def set_cooldown(symbol):
    cd = get_cooldowns()
    cd[symbol] = datetime.now(timezone.utc).isoformat()
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
    delta = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return delta < COOLDOWN_HOURS


def get_price(client, symbol):
    try:
        data = client.public_get(
            "/api/v3/ticker/price", {"symbol": symbol},
        )
        if data and "price" in data:
            return float(data["price"])
    except Exception as e:
        log.warning("price %s: %s", symbol, e)
    return None


def get_klines_1m(client, symbol, limit=180, start_ms=None):
    params = {"symbol": symbol, "interval": "1m", "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    data = client.public_get("/api/v3/klines", params)
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


def get_candles_range(symbol, start_dt):
    try:
        with candles_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, low, "
                    "close, volume FROM candles "
                    "WHERE symbol = %s AND timeframe = '1h' "
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
        with candles_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, low, "
                    "close, volume FROM candles "
                    "WHERE symbol = %s AND timeframe = '1h' "
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
            age = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        else:
            return True
        if age > CANDLES_MAX_AGE_H:
            log.warning("candles stale: %.1fh", age)
            return False
        return True
    except Exception:
        return True


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
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
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
    below = [s for s in supports if s.get("price", 0) < price]
    if not below:
        return None
    return min(below, key=lambda x: price - x["price"])


def get_resistances(symbol, price):
    lv = load_analysis("levels_analysis.json")
    sym = lv.get("symbols", {}).get(symbol, {})
    resistances = sym.get("resistances", [])
    above = [r for r in resistances if r.get("price", 0) > price]
    above.sort(key=lambda x: x["price"])
    return above


def build_levels(price, sup, resistances, atr, direction):
    if direction == "LONG":
        stop = None
        if sup:
            cand = sup["price"] * (1 - STOP_BELOW_SUP_PCT / 100)
            if price - cand > atr * MAX_STOP_ATR:
                log.info(
                    "  sup too far: %.2f > %.2f, using ATR",
                    price - cand, atr * MAX_STOP_ATR,
                )
            elif price - cand > 0:
                stop = cand
        if stop is None:
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
        return stop, target, reward / risk

    elif direction == "SHORT":
        stop = None
        res_up = None
        for res in resistances:
            if res["price"] > price:
                res_up = res
                break
        if res_up:
            cand = res_up["price"] * (1 + STOP_ABOVE_RES_PCT / 100)
            if cand - price > atr * MAX_STOP_ATR:
                log.info(
                    "  res too far: %.2f > %.2f, using ATR",
                    cand - price, atr * MAX_STOP_ATR,
                )
            elif cand - price > 0:
                stop = cand
        if stop is None:
            stop = price + atr * ATR_MULT
        if stop <= price:
            return None, None, None
        risk = stop - price
        if risk <= 0:
            return None, None, None
        target = None
        sup_list = sup if isinstance(sup, list) else []
        sup_sorted = sorted(
            sup_list, key=lambda x: x["price"], reverse=True
        )
        for sup_it in sup_sorted:
            reward = price - sup_it["price"]
            if reward / risk >= MIN_RR:
                target = sup_it["price"]
                break
        if not target:
            target = price - risk * TARGET_RR
        reward = price - target
        if reward <= 0:
            return None, None, None
        return stop, target, reward / risk

    return None, None, None


def check_signal(client, symbol):
    if in_cooldown(symbol):
        log.info("  %s: in cooldown", symbol)
        return None

    for p in get_positions():
        if p["symbol"] == symbol:
            log.info("  %s: already in positions", symbol)
            return None

    price = get_price(client, symbol)
    if not price:
        log.info("  %s: no price", symbol)
        return None

    candles = get_candles(symbol, 100)
    if len(candles) < 20:
        log.info("  %s: few candles (%d)", symbol, len(candles))
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

    direction = "NONE"
    score = 0.0
    breakdown = {}
    active = []

    if EXPLORER_OK:
        try:
            r = explorer_mod.analyze(symbol)
            score = float(r.get("score", 0))
            direction = r.get("direction", "NONE")
            breakdown = r.get("breakdown", {})
            active = r.get("active", [])
            log.info("  %s: explorer score=%.4f dir=%s",
                     symbol, score, direction)
        except Exception as e:
            log.warning("  %s: explorer fail: %s", symbol, e)
            direction = "NONE"

    if direction == "NONE":
        log.info("  %s: explorer NONE, skip", symbol)
        return None

    votes = ["Explorer"]
    reasons = ["explorer " + direction + " " + format(score, ".3f")]

    sup_used = None

    if direction == "LONG":
        sup = get_support(symbol, price)
        resistances = get_resistances(symbol, price)
        stop, target, rr = build_levels(
            price, sup, resistances, atr, "LONG",
        )
        if isinstance(sup, dict) and sup:
            sup_used = sup.get("price")
    else:
        resistances = get_resistances(symbol, price)
        lv = load_analysis("levels_analysis.json")
        sym_lv = lv.get("symbols", {}).get(symbol, {})
        all_sup = sym_lv.get("supports", [])
        sup_list = [
            s for s in all_sup if s.get("price", 0) < price
        ]
        sup_list.sort(key=lambda x: x["price"], reverse=True)
        stop, target, rr = build_levels(
            price, sup_list, resistances, atr, "SHORT",
        )
        if sup_list:
            sup_used = sup_list[0].get("price")

    if not stop:
        log.info("  %s: no stop", symbol)
        return None

    if direction == "LONG":
        if not (stop < price < target):
            log.warning(
                "  %s: sanity fail LONG stop=%.4f price=%.4f target=%.4f",
                symbol, stop, price, target,
            )
            return None
    else:
        if not (target < price < stop):
            log.warning(
                "  %s: sanity fail SHORT stop=%.4f price=%.4f target=%.4f",
                symbol, stop, price, target,
            )
            return None

    if rr < MIN_RR:
        log.info("  %s: R:R=%.2f < %.1f, skip", symbol, rr, MIN_RR)
        return None

    return {
        "symbol": symbol,
        "direction": direction,
        "price": price,
        "stop": stop,
        "target": target,
        "rr": round(rr, 2),
        "atr": atr,
        "rsi": rsi,
        "votes": votes,
        "reasons": reasons,
        "score": round(score, 4),
        "breakdown": breakdown,
        "active": active[:5],
        "support": sup_used,
        "resistance": target,
    }


def try_open_new(client):
    if len(get_positions()) >= MAX_POSITIONS:
        return False
    for symbol in SYMBOLS:
        signal = check_signal(client, symbol)
        if signal:
            pos = open_position(signal)
            if pos:
                return True
    return False


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

    direction = signal["direction"]
    if direction == "LONG":
        entry = signal["price"] * (1 + SLIPPAGE)
    else:
        entry = signal["price"] * (1 - SLIPPAGE)

    fee = POSITION_SIZE * TAKER_FEE
    portfolio["balance"] -= POSITION_SIZE
    size_coins = POSITION_SIZE / entry

    pos = {
        "id": str(uuid.uuid4()),
        "symbol": signal["symbol"],
        "direction": direction,
        "entry_price": round(entry, 6),
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "size_usd": POSITION_SIZE,
        "size_coins": round(size_coins, 8),
        "stop": round(signal["stop"], 6),
        "target": round(signal["target"], 6),
        "entry_fee": round(fee, 6),
        "rr_planned": signal["rr"],
        "rsi_entry": signal["rsi"],
        "atr_entry": signal["atr"],
        "explorer_score": signal.get("score"),
        "explorer_breakdown": signal.get("breakdown", {}),
        "explorer_active": signal.get("active", []),
        "votes": signal["votes"],
        "reasons": signal["reasons"],
    }
    positions.append(pos)
    save_positions(positions)
    save_json(PORTFOLIO_FILE, portfolio)

    lines = [
        direction + " OPENED",
        signal["symbol"].replace("USDT", ""),
        "Entry: $" + format(entry, ".4f"),
        "Stop: $" + format(signal["stop"], ".4f"),
        "Target: $" + format(signal["target"], ".4f"),
        "R:R 1:" + str(signal["rr"]),
        "Score: " + format(signal.get("score", 0), ".3f"),
    ]
    notify("\n".join(lines))
    log.info("OPEN %s %s", direction, signal["symbol"])
    return pos


def close_position(pos, exit_price, reason):
    portfolio = get_portfolio()
    positions = get_positions()
    direction = pos.get("direction", "LONG")

    if direction == "LONG":
        exit_real = exit_price * (1 - SLIPPAGE)
    else:
        exit_real = exit_price * (1 + SLIPPAGE)

    proceeds = pos["size_coins"] * exit_real
    exit_fee = proceeds * TAKER_FEE
    entry_cost = pos["size_usd"]

    if direction == "LONG":
        pnl = proceeds - entry_cost
    else:
        entry_value = pos["size_coins"] * pos["entry_price"]
        pnl = entry_value - proceeds

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

    positions = [p for p in positions if p["id"] != pos["id"]]
    save_positions(positions)

    trades = get_trades()
    trade = dict(pos)
    trade["exit_price"] = round(exit_real, 6)
    trade["exit_time"] = datetime.now(timezone.utc).isoformat()
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
        emoji + " CLOSED " + direction,
        pos["symbol"].replace("USDT", ""),
        "Reason: " + reason,
        "PnL: $" + format(pnl, "+.4f")
        + " (" + format(pnl_pct, "+.2f") + "%)",
        "Balance: $" + format(portfolio["balance"], ".2f"),
    ]
    notify("\n".join(lines))
    log.info("CLOSE %s %s PnL=%.4f (%s)",
             direction, pos["symbol"], pnl, reason)
    return True


def check_stop_target_1h(pos):
    try:
        entry_dt = datetime.fromisoformat(pos["entry_time"])
    except Exception:
        return False, None, None
    candles = get_candles_range(pos["symbol"], entry_dt)
    if not candles:
        return False, None, None
    stop = pos["stop"]
    target = pos["target"]
    direction = pos.get("direction", "LONG")

    for c in candles:
        if direction == "LONG":
            hit_stop = c["low"] <= stop
            hit_target = c["high"] >= target
        else:
            hit_stop = c["high"] >= stop
            hit_target = c["low"] <= target

        if hit_stop and hit_target:
            return check_stop_target_1m(pos, c["timestamp"])
        if hit_stop:
            return True, stop, "stop"
        if hit_target:
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
        client, pos["symbol"], limit=60, start_ms=start_ms,
    )
    if not klines:
        return False, None, None
    stop = pos["stop"]
    target = pos["target"]
    direction = pos.get("direction", "LONG")

    for k in klines:
        if direction == "LONG":
            if k["low"] <= stop:
                return True, stop, "stop"
            if k["high"] >= target:
                return True, target, "target"
        else:
            if k["high"] >= stop:
                return True, stop, "stop"
            if k["low"] <= target:
                return True, target, "target"
    return False, None, None


def check_time_exit(pos, current_price):
    try:
        entry_dt = datetime.fromisoformat(pos["entry_time"])
    except Exception:
        return False
    age_h = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
    if age_h < TIME_EXIT_HOURS:
        return False

    direction = pos.get("direction", "LONG")
    if direction == "LONG":
        exit_real = current_price * (1 - SLIPPAGE)
    else:
        exit_real = current_price * (1 + SLIPPAGE)

    proceeds = pos["size_coins"] * exit_real
    exit_fee = proceeds * TAKER_FEE

    if direction == "LONG":
        pnl = proceeds - pos["size_usd"]
    else:
        entry_value = pos["size_coins"] * pos["entry_price"]
        pnl = entry_value - proceeds

    pnl -= pos["entry_fee"]
    pnl -= exit_fee
    if pnl > 0:
        log.info("  %s: TIME EXIT (age=%.1fh, pnl=%.4f)",
                 pos["symbol"], age_h, pnl)
        return True
    return False


def check_one_position(client, pos):
    closed, exit_price, reason = check_stop_target_1h(pos)
    if closed:
        return close_position(pos, exit_price, reason)
    price = get_price(client, pos["symbol"])
    if price and check_time_exit(pos, price):
        return close_position(pos, price, "time_exit")
    return False


def watch_position(client):
    log.info("")
    log.info("=" * 50)
    log.info("WATCH start interval=%ds max=%dmin",
             WATCH_INTERVAL_SEC, WATCH_MAX_MIN)
    log.info("=" * 50)
    started = time.time()
    max_seconds = WATCH_MAX_MIN * 60

    while True:
        elapsed = time.time() - started
        if elapsed > max_seconds:
            break

        positions = get_positions()
        for pos in list(positions):
            if check_one_position(client, pos):
                break

        if len(get_positions()) < MAX_POSITIONS:
            opened = try_open_new(client)
            if opened:
                log.info("  opened new position")

        remaining = max_seconds - (time.time() - started)
        if remaining < WATCH_INTERVAL_SEC:
            break
        time.sleep(WATCH_INTERVAL_SEC)

    log.info("WATCH done")


def main():
    log.info("=" * 50)
    log.info("SIMULATOR 01 v9.8")
    log.info("MAX_POSITIONS=%d, SIZE=$%.2f, TIME_EXIT=%dh",
             MAX_POSITIONS, POSITION_SIZE, TIME_EXIT_HOURS)
    log.info("DB2 symbols: %s", ", ".join(sorted(DB2_SYMBOLS)))
    log.info("=" * 50)
    client = MexcClient()

    positions = get_positions()
    if positions:
        for pos in list(positions):
            check_one_position(client, pos)

    positions = get_positions()
    if len(positions) < MAX_POSITIONS:
        try_open_new(client)

    positions = get_positions()
    if positions:
        watch_position(client)

    portfolio = get_portfolio()
    positions = get_positions()
    trades = get_trades()

    log.info("")
    log.info("=== SUMMARY ===")
    log.info("Balance: $%.2f", portfolio["balance"])
    log.info("PnL: $%+.4f", portfolio["realized_pnl"])
    log.info("Trades: %d (%d W / %d L)",
             portfolio["total_trades"],
             portfolio["wins"], portfolio["losses"])
    log.info("Open: %d", len(positions))
    if trades:
        wins = [t for t in trades if t["pnl_usd"] > 0]
        wr = len(wins) / len(trades) * 100
        avg = sum(t["pnl_usd"] for t in trades) / len(trades)
        log.info("Win rate: %.1f%%", wr)
        log.info("Avg PnL: $%+.4f", avg)

    if EXPLORER_OK and explorer_mod is not None:
        try:
            explorer_mod.close_all()
        except Exception:
            pass
    close_connection()
    try:
        close_conn_db2()
    except Exception:
        pass


if __name__ == "__main__":
    main()