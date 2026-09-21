# ============================================================
# ARGUS-Trader — VALIDATOR
# ------------------------------------------------------------
# Валидация данных перед записью в БД.
# Правило: если данные подозрительные — в карантин (rejected_data).
# НЕ пишем в основную таблицу, чтобы не отравить историю.
# ------------------------------------------------------------
# v1: начальная версия
# v2: fix импортов (sys.path для запуска из любой папки)
# ============================================================

import sys
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# --- Путь к crypto/ (для импорта config) ---
SCRIPT_DIR = Path(__file__).resolve().parent          # crypto/collect/
CRYPTO_ROOT = SCRIPT_DIR.parent                        # crypto/
sys.path.insert(0, str(CRYPTO_ROOT))

from config import VALIDATION

log = logging.getLogger("crypto.validator")


# ============================================================
# БАЗОВЫЕ ПРОВЕРКИ
# ============================================================
def is_valid_price(price, min_price: float = None) -> bool:
    if price is None:
        return False
    min_p = min_price if min_price is not None else VALIDATION["min_price"]
    try:
        return float(price) > min_p
    except (ValueError, TypeError):
        return False


def is_valid_timestamp(ts, allow_future_minutes: int = None, max_age_hours: int = None) -> bool:
    if not isinstance(ts, datetime):
        return False
    if ts.tzinfo is None:
        return False

    now = datetime.now(timezone.utc)
    max_future = allow_future_minutes if allow_future_minutes is not None else VALIDATION["max_future_minutes"]
    max_age = max_age_hours if max_age_hours is not None else VALIDATION["max_age_hours"]

    if ts > now + timedelta(minutes=max_future):
        return False
    if ts < now - timedelta(hours=max_age):
        return False
    return True


def is_valid_change_pct(change_pct, max_change: float = None) -> bool:
    if change_pct is None:
        return True
    max_c = max_change if max_change is not None else VALIDATION["max_change_pct_1h"]
    try:
        return abs(float(change_pct)) <= max_c
    except (ValueError, TypeError):
        return False


# ============================================================
# ВАЛИДАЦИЯ СВЕЧЕЙ
# ============================================================
def validate_candle(candle: dict) -> tuple:
    required = ["symbol", "timeframe", "timestamp", "open", "high", "low", "close"]
    for field in required:
        if field not in candle:
            return False, f"missing field: {field}"

    if not is_valid_timestamp(candle["timestamp"]):
        return False, "invalid timestamp (future or too old)"

    for field in ["open", "high", "low", "close"]:
        if not is_valid_price(candle[field]):
            return False, f"invalid {field}: {candle[field]}"

    o, h, l, c = (candle["open"], candle["high"], candle["low"], candle["close"])
    if h < max(o, c):
        return False, f"high < max(open, close): h={h}, max={max(o, c)}"
    if l > min(o, c):
        return False, f"low > min(open, close): l={l}, min={min(o, c)}"

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
def validate_funding(row: dict) -> tuple:
    if "symbol" not in row or "timestamp" not in row:
        return False, "missing symbol/timestamp"
    if not is_valid_timestamp(row["timestamp"]):
        return False, "invalid timestamp"
    if row.get("rate") is None:
        return False, "missing rate"
    try:
        rate = float(row["rate"])
        if abs(rate) > 0.01:
            return False, f"funding rate out of range: {rate}"
    except (ValueError, TypeError):
        return False, f"invalid rate: {row.get('rate')}"
    return True, None


# ============================================================
# ВАЛИДАЦИЯ OI
# ============================================================
def validate_oi(row: dict) -> tuple:
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
def validate_ls(row: dict) -> tuple:
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
def validate_taker(row: dict) -> tuple:
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
def validate_context(row: dict) -> tuple:
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


def validate(metric: str, row: dict) -> tuple:
    validator = VALIDATORS.get(metric)
    if not validator:
        return True, None
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
    future = datetime.now(timezone.utc) + timedelta(hours=2)

    tests = [
        ("✅ Валидная свеча", "ohlcv", {
            "symbol": "BTCUSDT", "timeframe": "1h", "timestamp": past,
            "open": 60000, "high": 61000, "low": 59500, "close": 60500, "volume": 100,
        }),
        ("❌ Битый OHLC (high < close)", "ohlcv", {
            "symbol": "BTCUSDT", "timeframe": "1h", "timestamp": past,
            "open": 60000, "high": 59000, "low": 59500, "close": 60500, "volume": 100,
        }),
        ("❌ Timestamp в будущем", "ohlcv", {
            "symbol": "BTCUSDT", "timeframe": "1h", "timestamp": future,
            "open": 60000, "high": 61000, "low": 59500, "close": 60500, "volume": 100,
        }),
        ("✅ Валидный funding", "funding", {
            "symbol": "BTCUSDT", "timestamp": past, "rate": 0.0001,
        }),
        ("❌ Битый funding (>1%)", "funding", {
            "symbol": "BTCUSDT", "timestamp": past, "rate": 0.5,
        }),
    ]

    for label, metric, row in tests:
        ok, reason = validate(metric, row)
        status = "PASS" if ok == label.startswith("✅") else "FAIL"
        print(f"[{status}] {label}: ok={ok}, reason={reason or 'none'}")

    print("=" * 50)