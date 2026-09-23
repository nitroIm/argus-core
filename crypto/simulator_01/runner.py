# ============================================================
# ARGUS - SIMULATOR 01 v1 [PRODUCTION]
# ------------------------------------------------------------
# Виртуальный LONG-трейдер.
# Сигнал: RSI < 30 + цена у поддержки.
# Баланс: $50. Позиция: $10.
# Всё в JSON. БЕЗ БД.
# ------------------------------------------------------------
# Требования:
#   pip install requests
# ============================================================

import os
import sys
import json
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
MEXC_DIR = SCRIPT_DIR.parent
CRYPTO_ROOT = MEXC_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
STATE_DIR = SCRIPT_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(MEXC_DIR))
sys.path.insert(0, str(CRYPTO_ROOT))

from client import MexcClient

# --- Логгер ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sim01")

# --- Файлы состояния ---
PORTFOLIO_FILE = STATE_DIR / "portfolio.json"
POSITIONS_FILE = STATE_DIR / "positions.json"
TRADES_FILE = STATE_DIR / "trades.json"

# --- Константы ---
START_BALANCE = 50.0
POSITION_SIZE = 10.0
MAX_POSITIONS = 1
TAKER_FEE = 0.0005
SLIPPAGE = 0.0005

SYMBOLS = ["BTCUSDT", "ETHUSDT"]

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


# ============================================================
# JSON HELPERS
# ============================================================
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


# ============================================================
# STATE
# ============================================================
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


# ============================================================
# MARKET DATA
# ============================================================
def get_price(client, symbol):
    """Spot цена с MEXC."""
    data = client.public_get(
        "/api/v3/ticker/price",
        {"symbol": symbol},
    )
    if data and "price" in data:
        return float(data["price"])
    return None


# ============================================================
# SIGNAL (из наших analysis JSON)
# ============================================================
def load_analysis(name):
    path = DATA_DIR / name
    return load_json(path, {})


def get_rsi_from_analysis(symbol):
    """
    RSI из patterns_analysis (последнее).
    Если нет — None.
    """
    p = load_analysis("patterns_analysis.json")
    sym = p.get("symbols", {}).get(symbol, {})
    # RSI считается в отчёте, не в patterns
    # Пока пропустим — вернём None
    return None


def get_levels(symbol):
    levels = load_analysis("levels_analysis.json")
    sym = levels.get("symbols", {}).get(symbol, {})
    return {
        "supports": sym.get("supports", []),
        "resistances": sym.get("resistances", []),
        "price": sym.get("current_price", 0),
    }


def check_signal(client, symbol):
    """
    Проверяет вход.
    Сейчас упрощённо:
      - цена у поддержки (< 1%)
      - есть сопротивление > 2%
    Возвращает dict или None.
    """
    price = get_price(client, symbol)
    if not price:
        return None

    lvl = get_levels(symbol)
    supports = lvl["supports"]
    resistances = lvl["resistances"]

    if not supports or not resistances:
        return None

    # Ближайшая поддержка ниже цены
    sup_below = [
        s for s in supports if s["price"] < price
    ]
    if not sup_below:
        return None
    sup = min(
        sup_below,
        key=lambda x: price - x["price"],
    )

    # Ближайшее сопротивление выше
    res_above = [
        r for r in resistances if r["price"] > price
    ]
    if not res_above:
        return None
    res = min(
        res_above,
        key=lambda x: x["price"] - price,
    )

    # Расстояние до поддержки
    gap_pct = (price - sup["price"]) / price * 100
    if gap_pct > 1.0:
        return None

    # Стоп — 0.5% ниже поддержки
    stop = sup["price"] * 0.995
    # Цель — сопротивление
    target = res["price"]

    risk = price - stop
    reward = target - price
    if risk <= 0 or reward <= 0:
        return None

    rr = reward / risk
    if rr < 1.5:
        return None

    return {
        "symbol": symbol,
        "price": price,
        "stop": stop,
        "target": target,
        "rr": round(rr, 2),
        "support": sup["price"],
        "resistance": res["price"],
    }


# ============================================================
# EXECUTION (VIRTUAL)
# ============================================================
def open_position(signal):
    """Открывает виртуальную позицию."""
    portfolio = get_portfolio()
    positions = get_positions()

    # Проверка баланса
    if portfolio["balance"] < POSITION_SIZE:
        log.warning("мало баланса")
        return None

    # Уже есть позиция по этому символу?
    for p in positions:
        if p["symbol"] == signal["symbol"]:
            return None

    # Лимит позиций
    if len(positions) >= MAX_POSITIONS:
        return None

    # Вход с slippage (покупаем дороже)
    entry = signal["price"] * (1 + SLIPPAGE)
    fee = POSITION_SIZE * TAKER_FEE

    # Списание с баланса
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
        "support": signal["support"],
        "resistance": signal["resistance"],
    }

    positions.append(pos)
    save_positions(positions)
    save_json(PORTFOLIO_FILE, portfolio)

    msg = "🟢 ОТКРЫТА LONG\n"
    msg += signal["symbol"].replace(
        "USDT", ""
    ) + "\n"
    msg += "Вход: $" + format(entry, ".4f") + "\n"
    msg += "Стоп: $" + format(
        signal["stop"], ".4f"
    ) + "\n"
    msg += "Цель: $" + format(
        signal["target"], ".4f"
    ) + "\n"
    msg += "R:R 1:" + str(signal["rr"]) + "\n"
    msg += "Размер: $" + str(POSITION_SIZE)
    notify(msg)
    log.info("OPEN " + signal["symbol"])

    return pos


def close_position(pos, exit_price, reason):
    """Закрывает позицию."""
    portfolio = get_portfolio()
    positions = get_positions()

    # Выход с slippage (продаём дешевле)
    exit_real = exit_price * (1 - SLIPPAGE)
    proceeds = pos["size_coins"] * exit_real
    exit_fee = proceeds * TAKER_FEE

    entry_cost = pos["size_usd"]
    pnl = proceeds - entry_cost
    pnl -= pos["entry_fee"]
    pnl -= exit_fee
    pnl_pct = pnl / entry_cost * 100

    # Возврат на баланс
    portfolio["balance"] += entry_cost + pnl
    portfolio["realized_pnl"] += pnl
    portfolio["total_trades"] += 1
    if pnl > 0:
        portfolio["wins"] += 1
    else:
        portfolio["losses"] += 1

    # Убираем из открытых
    positions = [
        p for p in positions
        if p["id"] != pos["id"]
    ]
    save_positions(positions)

    # В историю
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
    msg = emoji + " ЗАКРЫТА\n"
    msg += pos["symbol"].replace(
        "USDT", ""
    ) + "\n"
    msg += "Причина: " + reason + "\n"
    msg += "PnL: $" + format(pnl, "+.4f")
    msg += " (" + format(pnl_pct, "+.2f") + "%)\n"
    msg += "Баланс: $" + format(
        portfolio["balance"], ".2f"
    )
    notify(msg)
    log.info(
        "CLOSE %s PnL=%.4f (%s)",
        pos["symbol"], pnl, reason,
    )


def check_positions(client):
    """Проверяет стоп/цель открытых позиций."""
    positions = get_positions()
    if not positions:
        return

    for pos in list(positions):
        symbol = pos["symbol"]
        price = get_price(client, symbol)
        if not price:
            continue

        stop = pos["stop"]
        target = pos["target"]

        if price <= stop:
            close_position(pos, stop, "stop")
        elif price >= target:
            close_position(pos, target, "target")


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=" * 50)
    log.info("SIMULATOR 01 v1")
    log.info("=" * 50)

    client = MexcClient()

    # 1. Проверяем открытые
    check_positions(client)

    # 2. Ищем новые сигналы
    positions = get_positions()
    if len(positions) < MAX_POSITIONS:
        for symbol in SYMBOLS:
            signal = check_signal(client, symbol)
            if signal:
                open_position(signal)
                break

    # 3. Итоги
    portfolio = get_portfolio()
    positions = get_positions()
    trades = get_trades()

    log.info("")
    log.info("=== ИТОГИ ===")
    log.info(
        "Баланс: $%.2f",
        portfolio["balance"],
    )
    log.info(
        "PnL: $%+.4f",
        portfolio["realized_pnl"],
    )
    log.info(
        "Сделок: %d (%d W / %d L)",
        portfolio["total_trades"],
        portfolio["wins"],
        portfolio["losses"],
    )
    log.info(
        "Открыто: %d", len(positions),
    )
    if trades:
        wins = [
            t for t in trades if t["pnl_usd"] > 0
        ]
        wr = len(wins) / len(trades) * 100
        avg_pnl = sum(
            t["pnl_usd"] for t in trades
        ) / len(trades)
        log.info(
            "Win rate: %.1f%%", wr,
        )
        log.info(
            "Avg PnL: $%+.4f", avg_pnl,
        )


if __name__ == "__main__":
    main()