# ============================================================
# ARGUS-Trader - LIQUIDATIONS COLLECTOR [PRODUCTION]
# ------------------------------------------------------------
# Сбор ликвидаций с OKX (публичный endpoint, без ключа).
# Агрегирует за последний час: long/short ликвидации.
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


def fetch_liquidations(inst_id, limit=100):
    """Тянет последние ликвидации с OKX."""
    params = {
        "instType": "SWAP",
        "instId": inst_id,
        "state": "filled",
        "limit": limit,
    }
    try:
        r = requests.get(
            OKX_URL,
            params=params,
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            log.warning(
                "%s: HTTP %d",
                inst_id, r.status_code,
            )
            return []
        data = r.json()
    except Exception as e:
        log.error("%s: %s", inst_id, e)
        return []

    if data.get("code") != "0":
        log.warning(
            "%s: code=%s msg=%s",
            inst_id,
            data.get("code"),
            data.get("msg"),
        )
        return []

    rows = data.get("data", [])
    out = []
    for group in rows:
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
    """Агрегирует ликвидации за последний час."""
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
        # side: "buy" = long liquidated, "sell" = short
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
        "total_vol": round(long_liq + short_liq, 2),
    }


def save_aggregated(symbol, ts, agg):
    """Сохраняет агрегат в БД."""
    if not agg:
        return 0

    sql = (
        "INSERT INTO liquidations "
        "(symbol, timestamp, side, qty, price, quote_vol, source) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (symbol, timestamp, side) "
        "DO UPDATE SET "
        "qty = EXCLUDED.qty, "
        "quote_vol = EXCLUDED.quote_vol, "
        "price = EXCLUDED.price"
    )

    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # LONG ликвидации
                if agg["long_count"] > 0:
                    cur.execute(sql, (
                        symbol, ts, "long",
                        agg["long_count"],
                        0,
                        agg["long_vol"],
                        "okx",
                    ))
                    if cur.rowcount and cur.rowcount > 0:
                        added += cur.rowcount

                # SHORT ликвидации
                if agg["short_count"] > 0:
                    cur.execute(sql, (
                        symbol, ts, "short",
                        agg["short_count"],
                        0,
                        agg["short_vol"],
                        "okx",
                    ))
                    if cur.rowcount and cur.rowcount > 0:
                        added += cur.rowcount
    except Exception as e:
        log.error("save %s: %s", symbol, e)

    return added


def collect_liquidations():
    """Главная функция сбора. Возвращает число строк."""
    ts = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0,
    )

    total_added = 0
    for symbol, inst_id in SYMBOLS.items():
        items = fetch_liquidations(inst_id, limit=100)
        if not items:
            log.warning("%s: no liquidations", symbol)
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