# ============================================================
# ARGUS-Trader — PIPELINE (главный сборщик)
# ------------------------------------------------------------
# Для каждой метрики проходит по fallback-цепочке бирж:
#   1. Пробует первую биржу из PRIORITY
#   2. Валидирует данные
#   3. Если OK — INSERT в БД (ON CONFLICT DO NOTHING)
#   4. Если упала — идёт к следующей
#   5. Если все упали — запись в collect_log (status=fail)
#
# Битые данные → rejected_data.
# Cross-check: OHLCV (OKX) vs CoinGecko — детект аномалий.
# ------------------------------------------------------------
# v1: начальная версия
# ============================================================

import sys
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# --- Путь к crypto/ ---
SCRIPT_DIR = Path(__file__).resolve().parent          # crypto/collect/
CRYPTO_ROOT = SCRIPT_DIR.parent                        # crypto/
sys.path.insert(0, str(CRYPTO_ROOT))

from config import SYMBOLS, TIMEFRAMES, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from db import (
    get_connection, log_collect, log_rejected, log_anomaly,
)
from collect.exchanges import CLIENTS
from collect.priority import get_priority
from collect.validator import validate

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.pipeline")


# ============================================================
# TELEGRAM
# ============================================================
def notify(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        log.warning(f"Telegram: {e}")


# ============================================================
# SQL ДЛЯ INSERT (ON CONFLICT DO NOTHING)
# ============================================================
SQL = {
    "ohlcv": """
        INSERT INTO candles
            (symbol, timeframe, timestamp, open, high, low, close, volume, source)
        VALUES (%(symbol)s, %(timeframe)s, %(timestamp)s, %(open)s,
                %(high)s, %(low)s, %(close)s, %(volume)s, %(source)s)
        ON CONFLICT (symbol, timeframe, timestamp) DO NOTHING
    """,
    "funding": """
        INSERT INTO funding_rates (symbol, timestamp, rate, source)
        VALUES (%(symbol)s, %(timestamp)s, %(rate)s, %(source)s)
        ON CONFLICT (symbol, timestamp) DO NOTHING
    """,
    "oi": """
        INSERT INTO open_interest (symbol, timestamp, oi, oi_value, source)
        VALUES (%(symbol)s, %(timestamp)s, %(oi)s, %(oi_value)s, %(source)s)
        ON CONFLICT (symbol, timestamp) DO NOTHING
    """,
    "ls_ratio": """
        INSERT INTO long_short_ratio
            (symbol, timestamp, ls_ratio, long_pct, short_pct, source)
        VALUES (%(symbol)s, %(timestamp)s, %(ls_ratio)s,
                %(long_pct)s, %(short_pct)s, %(source)s)
        ON CONFLICT (symbol, timestamp) DO NOTHING
    """,
    "taker": """
        INSERT INTO taker_flow
            (symbol, timestamp, buy_vol, sell_vol, source)
        VALUES (%(symbol)s, %(timestamp)s, %(buy_vol)s, %(sell_vol)s, %(source)s)
        ON CONFLICT (symbol, timestamp) DO NOTHING
    """,
    "context": """
        INSERT INTO market_context
            (timestamp, btc_mcap, eth_mcap, btc_dominance, total_mcap,
             total_volume_24h, btc_price_usd, eth_price_usd, source)
        VALUES (%(timestamp)s, %(btc_mcap)s, %(eth_mcap)s, %(btc_dominance)s,
                %(total_mcap)s, %(total_volume_24h)s, %(btc_price_usd)s,
                %(eth_price_usd)s, %(source)s)
        ON CONFLICT (timestamp) DO NOTHING
    """,
}


# ============================================================
# ВСТАВКА СТРОК В БД
# ============================================================
def insert_rows(metric: str, rows: list) -> int:
    """Массовая вставка. Возвращает число реально добавленных строк."""
    if not rows:
        return 0

    sql = SQL.get(metric)
    if not sql:
        log.warning(f"Нет SQL для метрики: {metric}")
        return 0

    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for r in rows:
                    try:
                        cur.execute(sql, r)
                        if cur.rowcount and cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as e:
                        log.warning(f"INSERT skip ({metric}): {e}")
    except Exception as e:
        log.error(f"Ошибка вставки {metric}: {e}")
    return added


# ============================================================
# СБОР ОДНОЙ МЕТРИКИ ПО FALLBACK-ЦЕПОЧКЕ
# ============================================================
def collect_metric(metric: str, symbol: str = None,
                   timeframe: str = None, limit: int = 100) -> dict:
    """
    Собирает одну метрику для одного символа.
    Возвращает: {added, source, fallback_count, status, error}
    """
    result = {
        "metric": metric, "symbol": symbol, "added": 0,
        "source": None, "fallback_count": 0,
        "status": "fail", "error": None,
    }

    chain = get_priority(metric)
    if not chain:
        result["error"] = f"no priority for {metric}"
        return result

    for idx, (exchange_name, method_name) in enumerate(chain):
        client = CLIENTS.get(exchange_name)
        if not client:
            continue

        method = getattr(client, method_name, None)
        if not method:
            continue

        try:
            # Вызов метода
            if metric == "ohlcv":
                rows = method(symbol, timeframe, limit=limit)
            else:
                rows = method(symbol, limit=limit)

            if not rows:
                log.warning(f"[{metric}/{symbol}] {exchange_name}: 0 строк")
                result["fallback_count"] += 1
                continue

            # Валидация + отсев битых
            valid_rows = []
            rejected = 0
            for r in rows:
                ok, reason = validate(metric, r)
                if ok:
                    valid_rows.append(r)
                else:
                    rejected += 1
                    log_rejected(
                        job_name=f"pipeline_{metric}",
                        reason=reason,
                        metric=metric,
                        symbol=symbol,
                        raw_data=r,
                        source=exchange_name,
                    )

            if not valid_rows:
                log.warning(f"[{metric}/{symbol}] {exchange_name}: все данные битые ({rejected})")
                result["fallback_count"] += 1
                continue

            # Вставка
            added = insert_rows(metric, valid_rows)
            result["added"] = added
            result["source"] = exchange_name
            result["status"] = "ok" if added > 0 else "no_new"

            log.info(
                f"[{metric}/{symbol}] {exchange_name}: "
                f"получено {len(rows)}, валидных {len(valid_rows)}, "
                f"битых {rejected}, добавлено {added}"
            )
            return result

        except Exception as e:
            log.warning(f"[{metric}/{symbol}] {exchange_name}: {e}")
            result["fallback_count"] += 1
            continue

    # Все биржи упали
    result["error"] = "all sources failed"
    return result


# ============================================================
# CROSS-CHECK (сверка цен между биржами)
# ============================================================
def cross_check_price(symbol: str) -> Optional[dict]:
    """
    Сравнивает последнюю цену с OKX и CoinGecko.
    Если разница > 0.5% → anomaly_log.
    """
    try:
        # OKX — последняя закрытая свеча
        okx = CLIENTS.get("okx")
        if not okx:
            return None
        okx_rows = okx.fetch_ohlcv(symbol, "1h", limit=1)
        if not okx_rows:
            return None
        okx_price = okx_rows[0]["close"]
        okx_ts = okx_rows[0]["timestamp"]

        # CoinGecko
        cg = CLIENTS.get("coingecko")
        if not cg:
            return None
        ctx = cg.fetch_context()
        if not ctx:
            return None
        coin_key = "btc_price_usd" if symbol.startswith("BTC") else "eth_price_usd"
        cg_price = ctx[0].get(coin_key)

        if not okx_price or not cg_price:
            return None

        diff_pct = abs(okx_price - cg_price) / okx_price * 100
        is_anomaly = diff_pct > 0.5

        # Запись в cross_check
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO cross_check
                            (symbol, timestamp, source_primary, source_secondary,
                             price_primary, price_secondary, diff_pct, is_anomaly)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (symbol, okx_ts, "okx", "coingecko",
                         okx_price, cg_price, round(diff_pct, 4), is_anomaly),
                    )
        except Exception as e:
            log.warning(f"cross_check insert: {e}")

        # Алерт при аномалии
        if is_anomaly:
            log_anomaly(
                symbol=symbol,
                timestamp=okx_ts,
                anomaly_type="price_divergence",
                severity="high" if diff_pct > 1.0 else "medium",
                details={
                    "okx_price": okx_price,
                    "coingecko_price": cg_price,
                    "diff_pct": round(diff_pct, 4),
                },
            )
            notify(
                f"⚠️ <b>Cross-check anomaly</b>\n"
                f"{symbol}: расхождение <b>{diff_pct:.2f}%</b>\n"
                f"OKX: ${okx_price:,.2f}\n"
                f"CoinGecko: ${cg_price:,.2f}"
            )

        return {
            "symbol": symbol, "okx": okx_price,
            "coingecko": cg_price, "diff_pct": round(diff_pct, 4),
            "is_anomaly": is_anomaly,
        }
    except Exception as e:
        log.warning(f"cross_check: {e}")
        return None


# ============================================================
# MAIN: ПОЛНЫЙ ЦИКЛ СБОРА
# ============================================================
def run_full_cycle():
    started_at = datetime.now(timezone.utc)
    log.info("=" * 60)
    log.info(f"🚀 PIPELINE START — {started_at.isoformat()}")
    log.info("=" * 60)

    summary = {"ok": 0, "no_new": 0, "fail": 0, "total_added": 0}

    # --- OHLCV ---
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            r = collect_metric("ohlcv", symbol=symbol, timeframe=tf, limit=100)
            log_collect(
                job_name="pipeline_ohlcv", status=r["status"],
                metric="ohlcv", symbol=symbol,
                records_added=r["added"], source_used=r["source"],
                fallback_count=r["fallback_count"], error=r["error"],
                started_at=started_at,
            )
            summary[r["status"]] = summary.get(r["status"], 0) + 1
            summary["total_added"] += r["added"]

    # --- Funding ---
    for symbol in SYMBOLS:
        r = collect_metric("funding", symbol=symbol, limit=100)
        log_collect(
            job_name="pipeline_funding", status=r["status"],
            metric="funding", symbol=symbol,
            records_added=r["added"], source_used=r["source"],
            fallback_count=r["fallback_count"], error=r["error"],
            started_at=started_at,
        )
        summary[r["status"]] = summary.get(r["status"], 0) + 1
        summary["total_added"] += r["added"]

    # --- Open Interest ---
    for symbol in SYMBOLS:
        r = collect_metric("oi", symbol=symbol, limit=100)
        log_collect(
            job_name="pipeline_oi", status=r["status"],
            metric="oi", symbol=symbol,
            records_added=r["added"], source_used=r["source"],
            fallback_count=r["fallback_count"], error=r["error"],
            started_at=started_at,
        )
        summary[r["status"]] = summary.get(r["status"], 0) + 1
        summary["total_added"] += r["added"]

    # --- Long/Short ---
    for symbol in SYMBOLS:
        r = collect_metric("ls_ratio", symbol=symbol, limit=100)
        log_collect(
            job_name="pipeline_ls_ratio", status=r["status"],
            metric="ls_ratio", symbol=symbol,
            records_added=r["added"], source_used=r["source"],
            fallback_count=r["fallback_count"], error=r["error"],
            started_at=started_at,
        )
        summary[r["status"]] = summary.get(r["status"], 0) + 1
        summary["total_added"] += r["added"]

    # --- Taker ---
    for symbol in SYMBOLS:
        r = collect_metric("taker", symbol=symbol, limit=100)
        log_collect(
            job_name="pipeline_taker", status=r["status"],
            metric="taker", symbol=symbol,
            records_added=r["added"], source_used=r["source"],
            fallback_count=r["fallback_count"], error=r["error"],
            started_at=started_at,
        )
        summary[r["status"]] = summary.get(r["status"], 0) + 1
        summary["total_added"] += r["added"]

    # --- Market Context (CoinGecko) ---
    try:
        cg = CLIENTS.get("coingecko")
        ctx = cg.fetch_context() if cg else []
        if ctx:
            added = insert_rows("context", ctx)
            log.info(f"[context] добавлено {added} строк")
            summary["total_added"] += added
    except Exception as e:
        log.error(f"Context: {e}")

    # --- Cross-check ---
    for symbol in SYMBOLS:
        cc = cross_check_price(symbol)
        if cc:
            status = "⚠️ ANOMALY" if cc["is_anomaly"] else "ok"
            log.info(
                f"[cross-check/{symbol}] {status}: "
                f"OKX=${cc['okx']:,.2f} vs CG=${cc['coingecko']:,.2f} "
                f"(diff {cc['diff_pct']:.3f}%)"
            )

    # --- Итог ---
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    log.info("=" * 60)
    log.info(f"✅ PIPELINE DONE за {elapsed:.1f}с")
    log.info(f"   OK: {summary['ok']}, NO_NEW: {summary['no_new']}, FAIL: {summary['fail']}")
    log.info(f"   Всего добавлено строк: {summary['total_added']}")
    log.info("=" * 60)


# ============================================================
# ТЕСТ / ЗАПУСК
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Только 1 метрика, dry-run лог")
    args = parser.parse_args()

    if args.test:
        # Быстрый тест — только OHLCV BTC 1h
        print("🧪 TEST MODE — одна метрика")
        r = collect_metric("ohlcv", symbol="BTCUSDT", timeframe="1h", limit=3)
        print(f"Result: {r}")
    else:
        run_full_cycle()