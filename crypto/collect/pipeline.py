# ============================================================
# ARGUS-Trader — PIPELINE (main collector) v11
# ------------------------------------------------------------
# v11: candles PK changed to (symbol, market_type,
#      timeframe, timestamp). All pipeline sources
#      are futures -> market_type='futures' literal.
# v10: + per-metric try/except — one failure does not
#      abort whole pipeline.
#      + source_used aggregated (not hardcoded "okx").
#      + failed_metrics list goes to log_collect.error.
#      + drop emoji from logs.
# v9: + onchain + macro. - liquidations.
# v8: + fear & greed. v5: one collect_log per run.
# ============================================================

import sys
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from config import (
    SYMBOLS, TIMEFRAMES,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    LIMITS_INCREMENTAL, LIMITS_BACKFILL,
    DATA_DIR,
)
from db import (
    get_connection, log_collect, log_rejected,
    log_anomaly, close_connection,
)
from collect.exchanges import CLIENTS
from collect.priority import get_priority
from collect.validator import validate
from collect.orderbook import collect_orderbook
from collect.feargreed import collect_feargreed
from collect.onchain import collect_onchain
from collect.macro import collect_macro

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.pipeline")

COINGECKO_CACHE_FILE = (
    DATA_DIR / "coingecko_cache.json"
)
COINGECKO_CACHE_TTL = 600


def notify(text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    if not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        url = (
            "https://api.telegram.org/bot"
            + TELEGRAM_BOT_TOKEN + "/sendMessage"
        )
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        log.warning("Telegram: %s", e)


SQL = {
    "ohlcv": """
        INSERT INTO candles
            (symbol, market_type, timeframe, timestamp,
             open, high, low, close, volume, source)
        VALUES (%(symbol)s, 'futures', %(timeframe)s,
                %(timestamp)s, %(open)s, %(high)s,
                %(low)s, %(close)s, %(volume)s,
                %(source)s)
        ON CONFLICT (symbol, market_type,
                     timeframe, timestamp)
        DO NOTHING
    """,
    "funding": """
        INSERT INTO funding_rates
            (symbol, timestamp, rate, source)
        VALUES (%(symbol)s, %(timestamp)s,
                %(rate)s, %(source)s)
        ON CONFLICT (symbol, timestamp) DO NOTHING
    """,
    "oi": """
        INSERT INTO open_interest
            (symbol, timestamp, oi, oi_value, source)
        VALUES (%(symbol)s, %(timestamp)s,
                %(oi)s, %(oi_value)s, %(source)s)
        ON CONFLICT (symbol, timestamp) DO NOTHING
    """,
    "ls_ratio": """
        INSERT INTO long_short_ratio
            (symbol, timestamp, ls_ratio,
             long_pct, short_pct, source)
        VALUES (%(symbol)s, %(timestamp)s, %(ls_ratio)s,
                %(long_pct)s, %(short_pct)s, %(source)s)
        ON CONFLICT (symbol, timestamp) DO NOTHING
    """,
    "taker": """
        INSERT INTO taker_flow
            (symbol, timestamp, buy_vol, sell_vol, source)
        VALUES (%(symbol)s, %(timestamp)s,
                %(buy_vol)s, %(sell_vol)s, %(source)s)
        ON CONFLICT (symbol, timestamp) DO NOTHING
    """,
    "context": """
        INSERT INTO market_context
            (timestamp, btc_mcap, eth_mcap, btc_dominance,
             total_mcap, total_volume_24h,
             btc_price_usd, eth_price_usd, source)
        VALUES (%(timestamp)s, %(btc_mcap)s, %(eth_mcap)s,
                %(btc_dominance)s, %(total_mcap)s,
                %(total_volume_24h)s, %(btc_price_usd)s,
                %(eth_price_usd)s, %(source)s)
        ON CONFLICT (timestamp) DO NOTHING
    """,
}


def insert_rows(metric: str, rows: list) -> int:
    if not rows:
        return 0
    sql = SQL.get(metric)
    if not sql:
        return 0
    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for r in rows:
                    try:
                        cur.execute(sql, r)
                        if cur.rowcount and \
                                cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as e:
                        log.warning(
                            "INSERT skip (%s): %s",
                            metric, e,
                        )
    except Exception as e:
        log.error(
            "insert %s failed: %s", metric, e
        )
    return added


def collect_metric(metric: str,
                   symbol: str = None,
                   timeframe: str = None,
                   limit: int = 3) -> dict:
    result = {
        "metric": metric,
        "symbol": symbol,
        "added": 0,
        "source": None,
        "fallback_count": 0,
        "status": "fail",
        "error": None,
    }
    try:
        chain = get_priority(metric)
    except Exception as e:
        result["error"] = (
            "priority err: " + str(e)[:60]
        )
        return result
    if not chain:
        result["error"] = (
            "no priority for " + metric
        )
        return result

    for exchange_name, method_name in chain:
        client = CLIENTS.get(exchange_name)
        if not client:
            continue
        method = getattr(
            client, method_name, None
        )
        if not method:
            continue
        try:
            if metric == "ohlcv":
                rows = method(
                    symbol, timeframe, limit=limit,
                )
            else:
                rows = method(symbol, limit=limit)
            if not rows:
                result["fallback_count"] += 1
                continue

            valid_rows = []
            rejected = 0
            rej_reasons = {}
            for r in rows:
                ok, reason = validate(metric, r)
                if ok:
                    valid_rows.append(r)
                else:
                    rejected += 1
                    key = (reason or "unknown")[:60]
                    rej_reasons[key] = (
                        rej_reasons.get(key, 0) + 1
                    )

            if rejected > 0:
                log_rejected(
                    job_name=(
                        "pipeline_" + metric
                    ),
                    reason=(
                        "batch rejected: "
                        + str(rej_reasons)
                    ),
                    metric=metric,
                    symbol=symbol,
                    raw_data={
                        "total": len(rows),
                        "rejected": rejected,
                        "reasons": rej_reasons,
                    },
                    source=exchange_name,
                )

            if not valid_rows:
                result["fallback_count"] += 1
                continue

            added = insert_rows(
                metric, valid_rows,
            )
            result["added"] = added
            result["source"] = exchange_name
            result["status"] = (
                "ok" if added > 0 else "no_new"
            )
            log.info(
                "[%s/%s] %s: got %d, valid %d, "
                "rejected %d, added %d",
                metric, symbol, exchange_name,
                len(rows), len(valid_rows),
                rejected, added,
            )
            return result
        except Exception as e:
            log.warning(
                "[%s/%s] %s: %s",
                metric, symbol,
                exchange_name, e,
            )
            result["fallback_count"] += 1
            continue

    result["error"] = "all sources failed"
    return result


def _load_cache() -> dict:
    if not COINGECKO_CACHE_FILE.exists():
        return {}
    try:
        with open(
            COINGECKO_CACHE_FILE,
            "r", encoding="utf-8",
        ) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data: dict):
    try:
        with open(
            COINGECKO_CACHE_FILE,
            "w", encoding="utf-8",
        ) as f:
            json.dump(
                data, f,
                default=str,
                ensure_ascii=False,
            )
    except Exception as e:
        log.warning("Cache save: %s", e)


def fetch_context_cached() -> list:
    cache = _load_cache()
    now = time.time()
    cached_at = cache.get("cached_at", 0)
    cached_data = cache.get("data")

    ttl = COINGECKO_CACHE_TTL
    if cached_data and (now - cached_at) < ttl:
        log.info(
            "[context] from cache (age=%ds)",
            int(now - cached_at),
        )
        return cached_data

    cg = CLIENTS.get("coingecko")
    if not cg:
        return cached_data or []

    try:
        ctx = cg.fetch_context()
        if ctx:
            _save_cache({
                "cached_at": now,
                "data": ctx,
            })
            log.info("[context] fresh fetch")
            return ctx
    except Exception as e:
        log.warning("[context] %s", e)

    return cached_data or []


def cross_check_price(symbol: str) -> Optional[dict]:
    try:
        okx = CLIENTS.get("okx")
        if not okx:
            return None
        okx_rows = okx.fetch_ohlcv(
            symbol, "1h", limit=1,
        )
        if not okx_rows:
            return None
        okx_price = okx_rows[0]["close"]
        okx_ts = okx_rows[0]["timestamp"]

        ctx = fetch_context_cached()
        if not ctx:
            return None
        coin_key = (
            "btc_price_usd"
            if symbol.startswith("BTC")
            else "eth_price_usd"
        )
        cg_price = ctx[0].get(coin_key)
        if not okx_price or not cg_price:
            return None

        diff_pct = (
            abs(okx_price - cg_price)
            / okx_price * 100
        )
        is_anomaly = diff_pct > 0.5

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO cross_check
                            (symbol, timestamp,
                             source_primary,
                             source_secondary,
                             price_primary,
                             price_secondary,
                             diff_pct, is_anomaly)
                        VALUES (%s, %s, %s, %s,
                                %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            symbol, okx_ts,
                            "okx", "coingecko",
                            okx_price, cg_price,
                            round(diff_pct, 4),
                            is_anomaly,
                        ),
                    )
        except Exception as e:
            log.warning(
                "cross_check insert: %s", e,
            )

        if is_anomaly:
            log_anomaly(
                symbol=symbol,
                timestamp=okx_ts,
                anomaly_type="price_divergence",
                severity=(
                    "high" if diff_pct > 1.0
                    else "medium"
                ),
                details={
                    "okx_price": okx_price,
                    "coingecko_price": cg_price,
                    "diff_pct": round(diff_pct, 4),
                },
            )
        return {
            "symbol": symbol,
            "okx": okx_price,
            "coingecko": cg_price,
            "diff_pct": round(diff_pct, 4),
            "is_anomaly": is_anomaly,
        }
    except Exception as e:
        log.warning("cross_check: %s", e)
        return None


def _run_wrapper(name, fn, *args, **kwargs):
    """Run function, return (added, error)."""
    try:
        n = fn(*args, **kwargs)
        return int(n or 0), None
    except Exception as e:
        log.error("%s: %s", name, e)
        return 0, str(e)[:80]


def run_cycle(mode: str = "incremental"):
    limits = (
        LIMITS_BACKFILL
        if mode == "backfill"
        else LIMITS_INCREMENTAL
    )
    started_at = datetime.now(timezone.utc)
    log.info("=" * 60)
    log.info(
        "PIPELINE START [%s] %s",
        mode, started_at.isoformat(),
    )
    log.info("Limits: %s", limits)
    log.info("=" * 60)

    summary = {
        "ok": 0, "no_new": 0, "fail": 0,
        "total_added": 0,
    }
    results = []
    sources_used = set()

    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            r = collect_metric(
                "ohlcv", symbol=symbol,
                timeframe=tf,
                limit=limits["ohlcv"],
            )
            summary[r["status"]] = (
                summary.get(r["status"], 0) + 1
            )
            summary["total_added"] += r["added"]
            if r["source"]:
                sources_used.add(r["source"])
            results.append(r)

    for metric in [
        "funding", "oi", "ls_ratio", "taker",
    ]:
        for symbol in SYMBOLS:
            r = collect_metric(
                metric, symbol=symbol,
                limit=limits[metric],
            )
            summary[r["status"]] = (
                summary.get(r["status"], 0) + 1
            )
            summary["total_added"] += r["added"]
            if r["source"]:
                sources_used.add(r["source"])
            results.append(r)

    try:
        ctx = fetch_context_cached()
        if ctx:
            added = insert_rows("context", ctx)
            log.info(
                "[context] added %d rows", added,
            )
            summary["total_added"] += added
            sources_used.add("coingecko")
    except Exception as e:
        log.error("Context: %s", e)

    n, err = _run_wrapper(
        "FearGreed", collect_feargreed,
    )
    summary["total_added"] += n
    if err is None and n > 0:
        sources_used.add("alternative.me")

    n, err = _run_wrapper(
        "Orderbook", collect_orderbook,
    )
    summary["total_added"] += n
    if err is None and n > 0:
        sources_used.add("mexc")

    n, err = _run_wrapper(
        "Onchain", collect_onchain,
    )
    summary["total_added"] += n
    if err is None and n > 0:
        sources_used.add("mempool.space")

    n, err = _run_wrapper(
        "Macro", collect_macro,
    )
    summary["total_added"] += n
    if err is None and n > 0:
        sources_used.add("yahoo")

    for symbol in SYMBOLS:
        cc = cross_check_price(symbol)
        if cc:
            tag = (
                "ANOMALY" if cc["is_anomaly"]
                else "ok"
            )
            log.info(
                "[cross-check/%s] %s: "
                "OKX=$%.2f vs CG=$%.2f "
                "(diff %.3f%%)",
                symbol, tag,
                cc["okx"], cc["coingecko"],
                cc["diff_pct"],
            )

    elapsed = (
        datetime.now(timezone.utc) - started_at
    ).total_seconds()

    overall_status = (
        "ok"
        if summary["fail"] == 0
        else (
            "partial"
            if summary["ok"] > 0
            else "fail"
        )
    )
    error_summary = None
    if summary["fail"] > 0:
        failed = [
            r["metric"] + "/" + str(r["symbol"])
            for r in results
            if r["status"] == "fail"
        ]
        error_summary = (
            "failed: " + ", ".join(failed[:10])
        )

    src_str = (
        ",".join(sorted(sources_used))
        if sources_used else None
    )

    log_collect(
        job_name="pipeline_" + mode,
        status=overall_status,
        metric=None,
        symbol=None,
        records_added=summary["total_added"],
        source_used=src_str,
        fallback_count=0,
        error=error_summary,
        started_at=started_at,
    )

    log.info("=" * 60)
    log.info(
        "PIPELINE DONE [%s] in %.1fs",
        mode, elapsed,
    )
    log.info(
        "  OK: %d, NO_NEW: %d, FAIL: %d",
        summary["ok"],
        summary["no_new"],
        summary["fail"],
    )
    log.info(
        "  total_added: %d",
        summary["total_added"],
    )
    log.info("=" * 60)

    close_connection()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["incremental", "backfill"],
        default="incremental",
    )
    parser.add_argument(
        "--test", action="store_true",
    )
    args = parser.parse_args()

    if args.test:
        print("TEST MODE")
        r = collect_metric(
            "ohlcv", symbol="BTCUSDT",
            timeframe="1h", limit=3,
        )
        print("Result: %s" % r)
        close_connection()
    else:
        run_cycle(mode=args.mode)