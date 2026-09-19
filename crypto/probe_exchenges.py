# -*- coding: utf-8 -*-
"""
Разведчик бирж: что доступно через публичные API.
Ничего не сохраняет, кроме отчёта. Никаких ключей.
"""
import json
import os
import time
from datetime import datetime, timezone, timedelta

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE_DIR, "data", "exchange_probe.json")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

TIMEOUT = 15
UA = {"User-Agent": "argus-probe/1.0"}

SYMBOL_BINANCE = "BTCUSDT"
SYMBOL_BYBIT   = "BTCUSDT"
SYMBOL_OKX     = "BTC-USDT-SWAP"
SYMBOL_COINBASE= "BTC-USD"
SYMBOL_KRAKEN  = "XBTUSD"
SYMBOL_BITGET  = "BTCUSDT"
SYMBOL_KUCOIN  = "XBTUSDTM"
SYMBOL_GATE    = "BTC_USDT"


def try_get(url, params=None, headers=None):
    """Безопасный GET, возвращает (ok, status, data_or_err, size_bytes)."""
    h = dict(UA)
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
        size = len(r.content)
        if r.status_code != 200:
            return False, r.status_code, r.text[:200], size
        try:
            return True, r.status_code, r.json(), size
        except Exception:
            return False, r.status_code, r.text[:200], size
    except Exception as e:
        return False, 0, str(e)[:200], 0


# ============================================================
# BINANCE (spot + futures)
# ============================================================
def probe_binance():
    res = {}
    spot = "https://api.binance.com"
    fut  = "https://fapi.binance.com"

    # OHLCV spot
    ok, st, data, size = try_get(f"{spot}/api/v3/klines",
        {"symbol": SYMBOL_BINANCE, "interval": "1h", "limit": 100})
    res["ohlcv_spot_1h"] = {"ok": ok, "status": st, "records": len(data) if ok else 0,
                            "bytes_100": size, "note": "100 candles"}

    # OHLCV futures
    ok, st, data, size = try_get(f"{fut}/fapi/v1/klines",
        {"symbol": SYMBOL_BINANCE, "interval": "1h", "limit": 100})
    res["ohlcv_futures_1h"] = {"ok": ok, "status": st, "records": len(data) if ok else 0,
                               "bytes_100": size}

    # Funding rate (история)
    ok, st, data, size = try_get(f"{fut}/fapi/v1/fundingRate",
        {"symbol": SYMBOL_BINANCE, "limit": 100})
    res["funding_rate_history"] = {"ok": ok, "status": st, "records": len(data) if ok else 0,
                                   "bytes_100": size, "note": "каждые 8ч"}

    # Open Interest (текущий)
    ok, st, data, size = try_get(f"{fut}/fapi/v1/openInterest",
        {"symbol": SYMBOL_BINANCE})
    res["open_interest_now"] = {"ok": ok, "status": st, "sample": data if ok else str(data)[:100]}

    # Open Interest (история)
    ok, st, data, size = try_get(f"{fut}/futures/data/openInterestHist",
        {"symbol": SYMBOL_BINANCE, "period": "1h", "limit": 100})
    res["open_interest_history"] = {"ok": ok, "status": st, "records": len(data) if ok else 0,
                                    "bytes_100": size}

    # Long/Short accounts
    ok, st, data, size = try_get(f"{fut}/futures/data/globalLongShortAccountRatio",
        {"symbol": SYMBOL_BINANCE, "period": "1h", "limit": 100})
    res["long_short_accounts"] = {"ok": ok, "status": st, "records": len(data) if ok else 0,
                                  "bytes_100": size}

    # Long/Short positions
    ok, st, data, size = try_get(f"{fut}/futures/data/topLongShortPositionRatio",
        {"symbol": SYMBOL_BINANCE, "period": "1h", "limit": 100})
    res["long_short_positions"] = {"ok": ok, "status": st, "records": len(data) if ok else 0,
                                   "bytes_100": size}

    # Taker buy/sell volume
    ok, st, data, size = try_get(f"{fut}/futures/data/takerlongshortRatio",
        {"symbol": SYMBOL_BINANCE, "period": "1h", "limit": 100})
    res["taker_buy_sell"] = {"ok": ok, "status": st, "records": len(data) if ok else 0,
                             "bytes_100": size}

    # Liquidations (публичного REST нет, только WS) - отмечаем как недоступное
    res["liquidations_rest"] = {"ok": False, "status": "-",
                                "note": "только через WebSocket forceOrder"}

    return res


# ============================================================
# BYBIT
# ============================================================
def probe_bybit():
    res = {}
    base = "https://api.bybit.com"

    ok, st, data, size = try_get(f"{base}/v5/market/kline",
        {"category": "linear", "symbol": SYMBOL_BYBIT, "interval": "60", "limit": 100})
    res["ohlcv_linear_1h"] = {"ok": ok, "status": st,
        "records": len(data.get("result", {}).get("list", [])) if ok else 0,
        "bytes_100": size}

    ok, st, data, size = try_get(f"{base}/v5/market/funding/history",
        {"category": "linear", "symbol": SYMBOL_BYBIT, "limit": 100})
    res["funding_rate_history"] = {"ok": ok, "status": st,
        "records": len(data.get("result", {}).get("list", [])) if ok else 0,
        "bytes_100": size}

    ok, st, data, size = try_get(f"{base}/v5/market/open-interest",
        {"category": "linear", "symbol": SYMBOL_BYBIT, "intervalTime": "1h", "limit": 100})
    res["open_interest"] = {"ok": ok, "status": st,
        "records": len(data.get("result", {}).get("list", [])) if ok else 0,
        "bytes_100": size}

    ok, st, data, size = try_get(f"{base}/v5/market/account-ratio",
        {"category": "linear", "symbol": SYMBOL_BYBIT, "period": "1h", "limit": 100})
    res["long_short_ratio"] = {"ok": ok, "status": st,
        "records": len(data.get("result", {}).get("list", [])) if ok else 0,
        "bytes_100": size}

    return res


# ============================================================
# OKX
# ============================================================
def probe_okx():
    res = {}
    base = "https://www.okx.com"

    ok, st, data, size = try_get(f"{base}/api/v5/market/candles",
        {"instId": SYMBOL_OKX, "bar": "1H", "limit": 100})
    res["ohlcv_swap_1h"] = {"ok": ok, "status": st,
        "records": len(data.get("data", [])) if ok else 0, "bytes_100": size}

    ok, st, data, size = try_get(f"{base}/api/v5/public/funding-rate-history",
        {"instId": SYMBOL_OKX, "limit": 100})
    res["funding_rate_history"] = {"ok": ok, "status": st,
        "records": len(data.get("data", [])) if ok else 0, "bytes_100": size}

    ok, st, data, size = try_get(f"{base}/api/v5/rubik/stat/contracts/open-interest-volume",
        {"ccy": "BTC", "period": "1H"})
    res["open_interest"] = {"ok": ok, "status": st,
        "records": len(data.get("data", [])) if ok else 0, "bytes_100": size}

    ok, st, data, size = try_get(f"{base}/api/v5/rubik/stat/contracts/long-short-account-ratio",
        {"ccy": "BTC", "period": "1H"})
    res["long_short_ratio"] = {"ok": ok, "status": st,
        "records": len(data.get("data", [])) if ok else 0, "bytes_100": size}

    ok, st, data, size = try_get(f"{base}/api/v5/rubik/stat/taker-volume",
        {"ccy": "BTC", "instType": "CONTRACTS"})
    res["taker_volume"] = {"ok": ok, "status": st,
        "records": len(data.get("data", [])) if ok else 0, "bytes_100": size}

    return res


# ============================================================
# COINBASE (spot only)
# ============================================================
def probe_coinbase():
    res = {}
    base = "https://api.exchange.coinbase.com"

    ok, st, data, size = try_get(f"{base}/products/{SYMBOL_COINBASE}/candles",
        {"granularity": 3600})
    res["ohlcv_spot_1h"] = {"ok": ok, "status": st,
        "records": len(data) if ok and isinstance(data, list) else 0, "bytes_300": size}

    # Coinbase — только спот, деривативов нет
    res["derivatives"] = {"ok": False, "note": "только спот"}

    return res


# ============================================================
# KRAKEN
# ============================================================
def probe_kraken():
    res = {}
    base = "https://api.kraken.com"

    ok, st, data, size = try_get(f"{base}/0/public/OHLC",
        {"pair": SYMBOL_KRAKEN, "interval": 60})
    n = 0
    if ok and isinstance(data, dict):
        for k, v in data.get("result", {}).items():
            if isinstance(v, list):
                n = len(v)
    res["ohlcv_spot_1h"] = {"ok": ok, "status": st, "records": n, "bytes": size}

    # Kraken Futures — отдельный API
    ok2, st2, data2, size2 = try_get(
        "https://futures.kraken.com/derivatives/api/v3/tickers")
    res["futures_tickers"] = {"ok": ok2, "status": st2, "bytes": size2}

    ok3, st3, data3, size3 = try_get(
        "https://futures.kraken.com/derivatives/api/v3/historicalfundingrates",
        {"symbol": "PF_XBTUSD"})
    res["funding_history"] = {"ok": ok3, "status": st3,
        "records": len(data3.get("rates", [])) if ok3 and isinstance(data3, dict) else 0,
        "bytes": size3}

    return res


# ============================================================
# BITGET
# ============================================================
def probe_bitget():
    res = {}
    base = "https://api.bitget.com"

    ok, st, data, size = try_get(f"{base}/api/v2/mix/market/candles",
        {"symbol": SYMBOL_BITGET, "granularity": "1H", "limit": 100,
         "productType": "USDT-FUTURES"})
    res["ohlcv_futures_1h"] = {"ok": ok, "status": st,
        "records": len(data.get("data", [])) if ok else 0, "bytes_100": size}

    ok, st, data, size = try_get(f"{base}/api/v2/mix/market/history-fund-rate",
        {"symbol": SYMBOL_BITGET, "productType": "USDT-FUTURES", "pageSize": 100})
    res["funding_rate_history"] = {"ok": ok, "status": st,
        "records": len(data.get("data", [])) if ok else 0, "bytes_100": size}

    ok, st, data, size = try_get(f"{base}/api/v2/mix/market/open-interest",
        {"symbol": SYMBOL_BITGET, "productType": "USDT-FUTURES"})
    res["open_interest_now"] = {"ok": ok, "status": st}

    ok, st, data, size = try_get(f"{base}/api/v2/mix/market/account-long-short",
        {"symbol": SYMBOL_BITGET, "productType": "USDT-FUTURES", "period": "1H"})
    res["long_short"] = {"ok": ok, "status": st,
        "records": len(data.get("data", [])) if ok else 0, "bytes_100": size}

    return res


# ============================================================
# KUCOIN (только для фьючерсов)
# ============================================================
def probe_kucoin():
    res = {}
    base = "https://api-futures.kucoin.com"

    ok, st, data, size = try_get(f"{base}/api/v1/kline/query",
        {"symbol": SYMBOL_KUCOIN, "granularity": 60})
    res["ohlcv_futures_1h"] = {"ok": ok, "status": st,
        "records": len(data.get("data", [])) if ok and isinstance(data, dict) else 0,
        "bytes": size}

    ok, st, data, size = try_get(f"{base}/api/v1/contract/funding-rates",
        {"symbol": SYMBOL_KUCOIN, "from": int((time.time() - 86400*7) * 1000),
         "to": int(time.time() * 1000)})
    res["funding_history"] = {"ok": ok, "status": st,
        "records": len(data.get("data", [])) if ok and isinstance(data, dict) else 0,
        "bytes": size}

    ok, st, data, size = try_get(f"{base}/api/v1/contracts/active")
    res["contracts_list"] = {"ok": ok, "status": st, "bytes": size}

    return res


# ============================================================
# GATE.IO
# ============================================================
def probe_gate():
    res = {}
    base = "https://api.gateio.ws/api/v4"

    ok, st, data, size = try_get(f"{base}/futures/usdt/candlesticks",
        {"contract": SYMBOL_GATE, "interval": "1h", "limit": 100})
    res["ohlcv_futures_1h"] = {"ok": ok, "status": st,
        "records": len(data) if ok and isinstance(data, list) else 0,
        "bytes_100": size}

    ok, st, data, size = try_get(f"{base}/futures/usdt/funding_rate",
        {"contract": SYMBOL_GATE, "limit": 100})
    res["funding_history"] = {"ok": ok, "status": st,
        "records": len(data) if ok and isinstance(data, list) else 0,
        "bytes_100": size}

    ok, st, data, size = try_get(f"{base}/futures/usdt/contract_stats",
        {"contract": SYMBOL_GATE, "interval": "1h", "limit": 100})
    res["contract_stats"] = {"ok": ok, "status": st,
        "records": len(data) if ok and isinstance(data, list) else 0,
        "bytes_100": size, "note": "OI + LS + taker в одном"}

    return res


# ============================================================
# MAIN
# ============================================================
EXCHANGES = {
    "binance": probe_binance,
    "bybit":   probe_bybit,
    "okx":     probe_okx,
    "coinbase":probe_coinbase,
    "kraken":  probe_kraken,
    "bitget":  probe_bitget,
    "kucoin":  probe_kucoin,
    "gate":    probe_gate,
}


def estimate_monthly(probe_result):
    """Прикидка объёма за 30 дней при часовом интервале."""
    hours = 30 * 24  # 720
    estimate = {}
    for metric, info in probe_result.items():
        if not info.get("ok"):
            continue
        # базовая оценка: размер_100 / 100 * 720
        size_key = None
        for k in ("bytes_100", "bytes_300", "bytes"):
            if k in info and info[k]:
                size_key = k
                break
        if not size_key:
            continue
        n = 100 if size_key == "bytes_100" else (300 if size_key == "bytes_300" else 1)
        per_record = info[size_key] / n
        monthly_bytes = per_record * hours
        estimate[metric] = {
            "monthly_records": hours,
            "monthly_kb": round(monthly_bytes / 1024, 1),
            "monthly_mb": round(monthly_bytes / 1024 / 1024, 2),
        }
    return estimate


def main():
    report = {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "exchanges": {},
    }

    for name, probe in EXCHANGES.items():
        print(f"\n{'='*60}")
        print(f"🔍 {name.upper()}")
        print('='*60)
        try:
            res = probe()
            est = estimate_monthly(res)
            report["exchanges"][name] = {"metrics": res, "monthly_estimate": est}

            ok_metrics = [k for k, v in res.items() if v.get("ok")]
            fail_metrics = [k for k, v in res.items() if not v.get("ok")]
            print(f"  ✅ доступно: {len(ok_metrics)}")
            for m in ok_metrics:
                est_m = est.get(m, {})
                mb = est_m.get("monthly_mb", "?")
                print(f"     • {m:30} ~{mb} МБ/мес")
            if fail_metrics:
                print(f"  ❌ недоступно: {len(fail_metrics)}")
                for m in fail_metrics:
                    note = res[m].get("note", "no access")
                    print(f"     • {m:30} ({note})")
        except Exception as e:
            print(f"  ⚠️ Ошибка: {e}")
            report["exchanges"][name] = {"error": str(e)}
        time.sleep(0.5)  # вежливость

    # Общая сводка
    print(f"\n{'='*60}")
    print("📊 ИТОГ")
    print('='*60)
    total_metrics = 0
    total_mb = 0.0
    for ex, data in report["exchanges"].items():
        if "monthly_estimate" not in data:
            continue
        for m, est in data["monthly_estimate"].items():
            total_metrics += 1
            total_mb += est.get("monthly_mb", 0)

    report["summary"] = {
        "total_metrics_available": total_metrics,
        "total_mb_per_month_all": round(total_mb, 2),
    }
    print(f"Доступных метрик всего: {total_metrics}")
    print(f"Объём всех метрик за месяц (если качать всё): ~{total_mb:.1f} МБ")

    # Уникальные метрики
    print(f"\nУникальные типы метрик (доступные хотя бы на одной бирже):")
    seen = {}
    for ex, data in report["exchanges"].items():
        if "metrics" not in data:
            continue
        for m, info in data["metrics"].items():
            if info.get("ok"):
                seen.setdefault(m, []).append(ex)
    for m, exs in sorted(seen.items()):
        print(f"  • {m:30} — {', '.join(exs)}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n📁 Полный отчёт: {OUT}")
    print("👉 Пришли этот файл или вывод терминала — разберём.")


if __name__ == "__main__":
    main()