# ============================================================
# ARGUS - RISK MODULE v1 [PRODUCTION]
# ------------------------------------------------------------
# Считает торговые сетапы для spot-входа.
# Вход: $10 позиция. Стоп, цель, R:R.
# ------------------------------------------------------------
# Требования: нет (только stdlib)
# ============================================================

import logging

log = logging.getLogger("crypto.risk")

# --- Константы ---
POSITION_USD = 10.0
MIN_RR = 2.0
ATR_MULT = 1.5
SUPPORT_GAP_PCT = 1.0
STOP_BELOW_SUPPORT_PCT = 0.3
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70


def compute_atr(candles, period=14):
    """ATR — средняя волатильность."""
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
    atr = sum(trs[-period:]) / period
    return round(atr, 4)


def compute_rsi(closes, period=14):
    """RSI-14 без pandas."""
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
    rsi = 100 - 100 / (1 + rs)
    return round(rsi, 2)


def _nearest_support(supports, price):
    """Ближайшая поддержка ниже цены."""
    if not supports:
        return None
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


def _nearest_resistance(resistances, price):
    """Ближайшее сопротивление выше цены."""
    if not resistances:
        return None
    above = [
        r for r in resistances
        if r.get("price", 0) > price
    ]
    if not above:
        return None
    return min(
        above,
        key=lambda x: x["price"] - price,
    )


def build_setup(
    symbol, name, candles, levels,
    rsi_value, funding_data,
):
    """
    Строит торговый сетап.
    Возвращает dict или None.
    """
    if not candles:
        return None
    if rsi_value is None:
        return None

    price = candles[-1]["close"]
    if price <= 0:
        return None

    atr = compute_atr(candles, 14)
    if not atr or atr <= 0:
        return None

    sym_lvl = levels.get("symbols", {}).get(symbol, {})
    supports = sym_lvl.get("supports", [])
    resistances = sym_lvl.get("resistances", [])

    sup = _nearest_support(supports, price)
    res = _nearest_resistance(resistances, price)

    # --- Логика сигнала ---
    # Spot: только LONG
    # RSI перепродан + цена у поддержки → LONG
    signal = None

    if rsi_value < RSI_OVERSOLD:
        # RSI перепродан — ищем вход
        if sup:
            gap = (
                (price - sup["price"]) / price * 100
            )
            if gap <= SUPPORT_GAP_PCT:
                signal = "LONG"
    # RSI перекуп — не входим (нет SHORT на spot)
    elif rsi_value > RSI_OVERBOUGHT:
        signal = None

    if signal != "LONG":
        return {
            "symbol": symbol,
            "name": name,
            "action": "WAIT",
            "reason": "нет сигнала",
            "price": price,
            "rsi": rsi_value,
        }

    # --- Расчёт стоп-лосса ---
    stop_atr = price - atr * ATR_MULT

    if sup:
        stop_sup = (
            sup["price"]
            * (1 - STOP_BELOW_SUPPORT_PCT / 100)
        )
        stop = min(stop_atr, stop_sup)
    else:
        stop = stop_atr

    if stop >= price:
        return None

    risk_per_unit = price - stop

    # --- Цель ---
    target = price + risk_per_unit * MIN_RR

    # Если сопротивление ближе цели — берём его
    if res and res["price"] < target:
        target = res["price"]

    if target <= price:
        return None

    reward_per_unit = target - price
    rr = reward_per_unit / risk_per_unit

    if rr < 1.5:
        return {
            "symbol": symbol,
            "name": name,
            "action": "WAIT",
            "reason": "R:R < 1.5",
            "price": price,
            "rsi": rsi_value,
            "rr": round(rr, 2),
        }

    # --- Размер позиции ---
    size_coins = POSITION_USD / price
    potential_loss = (
        risk_per_unit / price * POSITION_USD
    )
    potential_profit = (
        reward_per_unit / price * POSITION_USD
    )

    # --- Trailing уровни ---
    be_price = price + risk_per_unit
    trail_price = price + risk_per_unit * 2

    return {
        "symbol": symbol,
        "name": name,
        "action": "LONG",
        "price": price,
        "stop": round(stop, 2),
        "target": round(target, 2),
        "rr": round(rr, 2),
        "atr": atr,
        "rsi": rsi_value,
        "size_usd": POSITION_USD,
        "size_coins": round(size_coins, 6),
        "potential_loss": round(potential_loss, 4),
        "potential_profit": round(potential_profit, 4),
        "risk_pct": round(
            risk_per_unit / price * 100, 2
        ),
        "reward_pct": round(
            reward_per_unit / price * 100, 2
        ),
        "be_price": round(be_price, 2),
        "trail_price": round(trail_price, 2),
        "support": sup["price"] if sup else None,
        "resistance": res["price"] if res else None,
    }


def fmt_price(p):
    if p >= 1000:
        return "$" + format(int(p), ",")
    if p >= 1:
        return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")


def fmt_setup(s):
    """Форматирует сетап в текст."""
    if not s:
        return ""

    name = s.get("name", "?")
    if s.get("action") == "WAIT":
        reason = s.get("reason", "?")
        rsi = s.get("rsi")
        rsi_str = (
            format(rsi, ".1f") if rsi else "?"
        )
        return (
            "  ⏸ " + name + ": " + reason
            + " (RSI " + rsi_str + ")"
        )

    lines = []
    lines.append(
        "  💡 " + name + " LONG"
    )
    lines.append(
        "    Вход: " + fmt_price(s["price"])
        + " | RSI "
        + format(s["rsi"], ".1f")
    )
    lines.append(
        "    Стоп: " + fmt_price(s["stop"])
        + " (" + format(s["risk_pct"], "-.2f")
        + "%)"
    )
    lines.append(
        "    Цель: " + fmt_price(s["target"])
        + " (" + format(s["reward_pct"], "+.2f")
        + "%)"
    )
    lines.append(
        "    R:R = 1:" + format(s["rr"], ".1f")
    )
    lines.append(
        "    Размер: $" + format(s["size_usd"], ".0f")
        + " = " + str(s["size_coins"])
        + " монет"
    )
    lines.append(
        "    При стопе: -$"
        + format(s["potential_loss"], ".2f")
        + " | при цели: +$"
        + format(s["potential_profit"], ".2f")
    )
    lines.append(
        "    Trailing: без убытка @ "
        + fmt_price(s["be_price"])
        + " | фикс @ "
        + fmt_price(s["trail_price"])
    )
    return "\n".join(lines)


def fmt_summary(s):
    """Короткая строка для отчёта."""
    if not s:
        return ""
    name = s.get("name", "?")
    if s.get("action") == "WAIT":
        return "  ⏸ " + name + ": ждём"
    return (
        "  💡 " + name + ": вход "
        + fmt_price(s["price"])
        + " / стоп " + fmt_price(s["stop"])
        + " / цель " + fmt_price(s["target"])
        + " (R:R 1:" + format(s["rr"], ".1f") + ")"
    )