# ============================================================
# ARGUS-Trader - LIQUIDATIONS COLLECTOR [PRODUCTION]
# ------------------------------------------------------------
# Сбор ликвидаций с OKX (публичный endpoint).
# Агрегирует за последний час: long/short ликвидации.
# v2: fix OKX API - убран state, попытка с uly
# ============================================================

import sys
import logging
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.liquidations")

OKX_URL = (
    "https://www.okx.com/api/v5/public/"
    "liquidation-orders"
)

SYMBOLS = {
    "BTCUSDT": "BTC-USDT-SWAP",
    "ETHUSDT": "ETH-USDT-SWAP",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
}


def _try_fetch(params):
    """Одна попытка запроса к OKX."""
    try:
        r = requests.get(
            OKX_URL,
            params=params,
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            return None, r.status_code
        data = r.json()
        if data.get("code") != "0":
            return None, data.get("code")
        return data.get("data", []), 200
    except Exception as e:
        log.warning("fetch: %s", e)
        return None, None


def fetch_liquidations(inst_id, limit=100):
    """Пробует разные варианты параметров OKX."""
    # Вариант 1: instType + instId
    variants = [
        {"instType": "SWAP", "instId": inst_id,
         "limit": limit},
        {"instType": "SWAP",
         "uly": inst_id.rsplit("-", 1)[0],
         "limit": limit},
        {"instType": "SWAP", "limit": limit},
    ]

    for params in variants:
        data, code = _try_fetch(params)
        if data is not None:
            log.info(
                "%s: got %d groups (params=%s)",
                inst_id, len(data),
                list(params.keys()),
            )
            return _parse_groups(data, inst_id)
    log.warning("%s: all variants failed", inst_id)
    return []


def _parse_groups(groups, inst_id):
    """Парсит ответ OKX и фильтрует по instId."""
    out = []
    for group in groups:
        g_inst = group.get("instId", "")
        # Фильтр если вернулись все SWAP
        if g_inst and g_inst != inst_id:
            continue
        details = group.get("details", [])
        for d in details:
            try:
                ts_ms = int(d.get("ts", 0))
                ts = datetime.fromtimestamp(
                    ts_ms / 1000, tz=timezone.utc,
                )
                sz = float(d.get("sz", 0))
                bk_px = float(d.get("bkPx", 0))
                side = d.get("side", "?")
                out.append({
                    "timestamp": ts,
                    "side": side,
                    "qty": sz,
                    "price": bk_px,
                    "quote_vol": sz * bk_px,
                })
            except Exception:
                continue
    return out


def aggregate_last_hour(items):
    if not items:
        return None

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=1)

    long_liq = 0.0
    short_liq = 0.0
    long_count = 0
    short_count = 0

    for it in items:
        if it["timestamp"] < cutoff:
            continue
        if it["side"] == "buy":
            long_liq += it["quote_vol"]
            long_count += 1
        elif it["side"] == "sell":
            short_liq += it["quote_vol"]
            short_count += 1

    return {
        "long_vol": round(long_liq, 2),
        "short_vol": round(short_liq, 2),
        "long_count": long_count,
        "short_count": short_count,
    }


def save_aggregated(symbol, ts, agg):
    if not agg:
        return 0

    sql = (
        "INSERT INTO liquidations "
        "(symbol, timestamp, side, qty, price, "
        "quote_vol, source) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (symbol, timestamp, side) "
        "DO UPDATE SET "
        "qty = EXCLUDED.qty, "
        "quote_vol = EXCLUDED.quote_vol"
    )

    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if agg["long_count"] > 0:
                    cur.execute(sql, (
                        symbol, ts, "long",
                        agg["long_count"], 0,
                        agg["long_vol"], "okx",
                    ))
                    if cur.rowcount and cur.rowcount > 0:
                        added += cur.rowcount
                if agg["short_count"] > 0:
                    cur.execute(sql, (
                        symbol, ts, "short",
                        agg["short_count"], 0,
                        agg["short_vol"], "okx",
                    ))
                    if cur.rowcount and cur.rowcount > 0:
                        added += cur.rowcount
    except Exception as e:
        log.error("save %s: %s", symbol, e)

    return added


def collect_liquidations():
    ts = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0,
    )

    total_added = 0
    for symbol, inst_id in SYMBOLS.items():
        items = fetch_liquidations(inst_id, limit=100)
        if not items:
            log.warning("%s: no data", symbol)
            continue

        agg = aggregate_last_hour(items)
        if not agg:
            log.warning("%s: no agg", symbol)
            continue

        added = save_aggregated(symbol, ts, agg)
        total_added += added

        log.info(
            "[liq/%s] long=%.0f (N=%d) "
            "short=%.0f (N=%d) added=%d",
            symbol,
            agg["long_vol"], agg["long_count"],
            agg["short_vol"], agg["short_count"],
            added,
        )

    return total_added


if __name__ == "__main__":
    log.info("=" * 50)
    log.info("LIQUIDATIONS test")
    log.info("=" * 50)
    n = collect_liquidations()
    log.info("done, added=%d", n)