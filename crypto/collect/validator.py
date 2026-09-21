# ============================================================
# ARGUS-Trader — VALIDATOR
# ------------------------------------------------------------
# Валидация данных перед записью в БД.
# Правило: если данные подозрительные — в карантин (rejected_data).
# НЕ пишем в основную таблицу, чтобы не отравить историю.
# ------------------------------------------------------------
# v1: начальная версия
# ============================================================

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import VALIDATION

log = logging.getLogger("crypto.validator")


# ============================================================
# БАЗОВЫЕ ПРОВЕРКИ
# ============================================================
def is_valid_price(price, min_price: float = None) -> bool:
    """Цена должна быть > min_price и не None."""
    if price is None:
        return False
    min_p = min_price if min_price is not None else VALIDATION["min_price"]
    try:
        return float(price) > min_p
    except (ValueError, TypeError):
        return False


def is_valid_timestamp(ts, allow_future_minutes: int = None, max_age_hours: int = None) -> bool:
    """Timestamp не должен быть в будущем и не должен быть слишком старым."""
    if not isinstance(ts, datetime):
        return False

    # Убеждаемся, что tz-aware
    if ts.tzinfo is None:
        return False

    now = datetime.now(timezone.utc)
    max_future = allow_future_minutes if allow_future_minutes is not None else VALIDATION["max_future_minutes"]
    max_age = max_age_hours if max_age_hours is not None else VALIDATION["max_age_hours"]

    # Не в будущем (с допуском)
    if ts > now + timedelta(minutes=max_future):
        return False

    # Не старше N часов
    if ts < now - timedelta(hours=max_age):
        return False

    return True


def is_valid_change_pct(change_pct, max_change: float = None) -> bool:
    """Изменение цены за период не должно превышать max_change."""
    if change_pct is None:
        return True  # если не передали — не проверяем
    max_c = max_change if max_change is not None else VALIDATION["max_change_pct_1h"]
    try:
        return abs(float(change_pct)) <= max_c
    except (ValueError, TypeError):
        return False


# ============================================================
# ВАЛИДАЦИЯ СВЕЧЕЙ
# ============================================================
def validate_candle(candle: dict) -> tuple[bool, Optional[str]]:
    """
    Проверяет одну свечу OHLCV.
    Возвращает (ok, reason). reason=None если ok=True.
    """
    # Обязательные поля
    required = ["symbol", "timeframe", "timestamp", "open", "high", "low", "close"]
    for field in required:
        if field not in candle:
            return False, f"missing field: {field}"

    # Timestamp
    if not is_valid_timestamp(candle["timestamp"]):
        return False, "invalid timestamp (future or too old)"

    # Цены
    for field in ["open", "high", "low", "close"]:
        if not is_valid_price(candle[field]):
            return False, f"invalid {field}: {candle[field]}"

    # Логика OHLC: high >= max(open, close), low <= min(open, close)
    o, h, l, c = (candle["open"], candle["high"], candle["low"], candle["close"])
    if h < max(o, c):
        return False, f"high < max(open, close): h={h}, max={max(o, c)}"
    if l > min(o, c):
        return False, f"low > min(open, close): l={l}, min={min(o, c)}"

    # Volume >= 0
    vol = candle.get("volume")
    if vol is not None:
        try:
            if float(vol) < 0:
                return False, f"negative volume: {vol}"
        except (ValueError, TypeError):
            return False, f"invalid volume: {vol}"

    return True, None


# ============================================================
# ВАЛИДАЦИЯ FUNDING
# ============================================================
def validate_funding(row: dict) -> tuple[bool, Optional[str]]:
    """Funding rate: разумный диапазон ±1% за период (обычно ±0.1%)."""
    if "symbol" not in row or "timestamp" not in row:
        return False, "missing symbol/timestamp"
    if not is_valid_timestamp(row["timestamp"]):
        return False, "invalid timestamp"
    if row.get("rate") is None:
        return False, "missing rate"
    try:
        rate = float(row["rate"])
        # Защита от глюков API
        if abs(rate) > 0.01:  # 1% за 8ч — явный глюк
            return False, f"funding rate out of range: {rate}"
    except (ValueError, TypeError):
        return False, f"invalid rate: {row.get('rate')}"
    return True, None


# ============================================================
# ВАЛИДАЦИЯ OI
# ============================================================
def validate_oi(row: dict) -> tuple[bool, Optional[str]]:
    """Open Interest должен быть > 0."""
    if "symbol" not in row or "timestamp" not in row:
        return False, "missing symbol/timestamp"
    if not is_valid_timestamp(row["timestamp"]):
        return False, "invalid timestamp"
    oi = row.get("oi")
    if oi is None:
        return False, "missing oi"
    try:
        if float(oi) <= 0:
            return False, f"oi <= 0: {oi}"
    except (ValueError, TypeError):
        return False, f"invalid oi: {oi}"
    return True, None


# ============================================================
# ВАЛИДАЦИЯ LS RATIO
# ============================================================
def validate_ls(row: dict) -> tuple[bool, Optional[str]]:
    """LS ratio должен быть > 0."""
    if "symbol" not in row or "timestamp" not in row:
        return False, "missing symbol/timestamp"
    if not is_valid_timestamp(row["timestamp"]):
        return False, "invalid timestamp"
    ratio = row.get("ls_ratio")
    if ratio is None:
        return False, "missing ls_ratio"
    try:
        if float(ratio) <= 0:
            return False, f"ls_ratio <= 0: {ratio}"
    except (ValueError, TypeError):
        return False, f"invalid ls_ratio: {ratio}"
    return True, None


# ============================================================
# ВАЛИДАЦИЯ TAKER
# ============================================================
def validate_taker(row: dict) -> tuple[bool, Optional[str]]:
    """Taker buy/sell: объёмы >= 0."""
    if "symbol" not in row or "timestamp" not in row:
        return False, "missing symbol/timestamp"
    if not is_valid_timestamp(row["timestamp"]):
        return False, "invalid timestamp"
    for field in ["buy_vol", "sell_vol"]:
        v = row.get(field)
        if v is None:
            return False, f"missing {field}"
        try:
            if float(v) < 0:
                return False, f"negative {field}: {v}"
        except (ValueError, TypeError):
            return False, f"invalid {field}: {v}"
    return True, None


# ============================================================
# ВАЛИДАЦИЯ MARKET CONTEXT
# ============================================================
def validate_context(row: dict) -> tuple[bool, Optional[str]]:
    """Контекст рынка: цены и доминация в разумных пределах."""
    if not is_valid_timestamp(row.get("timestamp"), max_age_hours=6):
        return False, "invalid timestamp"

    for field in ["btc_price_usd", "eth_price_usd"]:
        if not is_valid_price(row.get(field)):
            return False, f"invalid {field}: {row.get(field)}"

    dom = row.get("btc_dominance")
    if dom is not None:
        try:
            d = float(dom)
            if not (0 < d < 100):
                return False, f"btc_dominance out of range: {d}"
        except (ValueError, TypeError):
            return False, f"invalid btc_dominance: {dom}"

    return True, None


# ============================================================
# УНИВЕРСАЛЬНЫЙ ДИСПЕТЧЕР
# ============================================================
VALIDATORS = {
    "ohlcv": validate_candle,
    "funding": validate_funding,
    "oi": validate_oi,
    "ls_ratio": validate_ls,
    "taker": validate_taker,
    "context": validate_context,
}


def validate(metric: str, row: dict) -> tuple[bool, Optional[str]]:
    """Выбирает валидатор по метрике."""
    validator = VALIDATORS.get(metric)
    if not validator:
        return True, None  # неизвестная метрика — пропускаем
    return validator(row)


# ============================================================
# ТЕСТ
# ============================================================
if __name__ == "__main__":
    from datetime import datetime, timezone

    print("🔍 ARGUS-Trader — тест валидатора")
    print("=" * 50)

    now = datetime.now(timezone.utc)
    past = datetime.now(timezone.utc) - timedelta(hours=1)

    # Тест 1: валидная свеча
    ok, reason = validate("ohlcv", {
        "symbol": "BTCUSDT", "timeframe": "1h", "timestamp": past,
        "open": 60000, "high": 61000, "low": 59500, "close": 60500, "volume": 100,
    })
    print(f"✅ Валидная свеча: {ok} ({reason or 'ok'})")

    # Тест 2: битый OHLC
    ok, reason = validate("ohlcv", {
        "symbol": "BTCUSDT", "timeframe": "1h", "timestamp": past,
        "open": 60000, "high": 59000, "low": 59500, "close": 60500, "volume": 100,
    })
    print(f"❌ Битый OHLC: {ok} ({reason})")

    # Тест 3: timestamp в будущем
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    ok, reason = validate("ohlcv", {
        "symbol": "BTCUSDT", "timeframe": "1h", "timestamp": future,
        "open": 60000, "high": 61000, "low": 59500, "close": 60500, "volume": 100,
    })
    print(f"❌ Timestamp в будущем: {ok} ({reason})")

    # Тест 4: валидный funding
    ok, reason = validate("funding", {
        "symbol": "BTCUSDT", "timestamp": past, "rate": 0.0001,
    })
    print(f"✅ Валидный funding: {ok} ({reason or 'ok'})")

    # Тест 5: битый funding
    ok, reason = validate("funding", {
        "symbol": "BTCUSDT", "timestamp": past, "rate": 0.5,
    })
    print(f"❌ Битый funding: {ok} ({reason})")

    print("=" * 50)