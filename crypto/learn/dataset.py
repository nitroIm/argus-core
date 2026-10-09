# ============================================================
# ARGUS-Trader - DATASET v16
# ------------------------------------------------------------
# v16: fetch_orderbook silent for DB2 (no log warning).
#      _fetch accepts silent flag.
# v15: read oi_change_pct/ls_ratio/taker_ratio from features_hourly.
#      Add _finalize_X (impute NaN + clip test to train).
# v13.1: expose ts_test/sym_test in prepare().
# ============================================================

import os
import sys
import logging
from pathlib import Path
from datetime import (
    datetime, timezone, timedelta,
)

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

for _p in CRYPTO_ROOT.rglob("db2.py"):
    _d = str(_p.parent)
    if "__pycache__" in _d:
        continue
    if _d not in sys.path:
        sys.path.insert(0, _d)
    break

from db import get_connection

DB2_OK = False
get_conn_db2 = None
if (os.getenv("ARGUS_DB_URL_2") or "").strip():
    try:
        from db2 import (
            get_connection as get_conn_db2,
        )
        _t = get_conn_db2()
        with _t as _c:
            with _c.cursor() as _cur:
                _cur.execute("SELECT 1")
                _cur.fetchone()
        DB2_OK = True
    except Exception as exc:
        print("DB2 fail: " + str(exc))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.dataset")

USE_EXTERNAL = (
    os.getenv("USE_EXTERNAL", "0").strip() == "1"
)

HORIZON = int(os.getenv("HORIZON", "12"))

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]

SYMBOLS = [
    s.strip().upper()
    for s in (
        os.getenv("SYMBOLS")
        or ",".join(DEFAULT_SYMBOLS)
    ).split(",")
    if s.strip()
]

REFERENCE = (
    os.getenv("REFERENCE") or "BTCUSDT"
).strip().upper()

DB2_SYMBOLS = {
    s.strip().upper()
    for s in (
        os.getenv("DB2_SYMBOLS")
        or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        try:
            return get_conn_db2()
        except Exception as exc:
            log.warning(
                "db2 conn %s: %s", symbol, exc
            )
    return get_connection()


INTERNAL_COLS = [
    "change_pct",
    "range_pct",
    "body_pct",
    "upper_wick_pct",
    "lower_wick_pct",
    "volume_ratio_24h",
    "volatility_24h",
    "volatility_7d",
    "change_4h",
    "change_24h",
    "change_1d",
    "trend_up",
    "hour_of_day",
    "funding_rate",
    "funding_trend",
    "ema9_dist_pct",
    "ema21_dist_pct",
    "ema50_dist_pct",
    "macd",
    "macd_signal",
    "bb_upper_dist",
    "bb_lower_dist",
    "bb_width_pct",
    "dist_high_24h_pct",
    "dist_low_24h_pct",
    "consecutive_up",
    "session",
    "oi_change_pct",
    "ls_ratio",
    "taker_ratio",
]

MACRO_COLS = ["us10y_level", "us10y_change_1d"]
ONCHAIN_COLS = ["hashrate_log", "hashrate_change_1d"]
OB_COLS = ["ob_bid_pct", "ob_ask_pct", "ob_spread_pct"]
EVENT_COLS = [
    "events_1h",
    "events_24h",
    "rsi_overbought_24h",
]
ANOMALY_COLS = ["anomaly_24h"]

ASIA_COLS = [
    "asia_nikkei_6h",
    "asia_shanghai_6h",
    "asia_shanghai_12h",
    "asia_hangseng_6h",
    "asia_usdcny_6h",
    "asia_usdcny_12h",
    "asia_dax_6h",
    "asia_stoxx50_6h",
    "asia_ftse_6h",
    "asia_eurusd_6h",
    "asia_vix_1h",
    "asia_nasdaq_1h",
    "asia_us10y_6h",
    "asia_usdjpy_6h",
    "asia_kospi_6h",
    "asia_taiex_6h",
    "asia_impact_score",
]

EXTERNAL_COLS = [
    "dxy_change_pct",
    "spx_change_pct",
    "gold_change_pct",
]

FEATURE_COLS = (
    INTERNAL_COLS
    + MACRO_COLS
    + ONCHAIN_COLS
    + OB_COLS
    + EVENT_COLS
    + ANOMALY_COLS
    + ASIA_COLS
    + (EXTERNAL_COLS if USE_EXTERNAL else [])
)

TARGET_RET = "next_return"
TARGET_COL = "next_change_pct"

ASIA_TOL_SEC = 7200

MAX_AGE = {
    "macro": 12,
    "onchain": 72,
    "orderbook": 4,
    "events": 168,
    "anomaly": 48,
}


def _fetch(conn, sql, params=(), silent=False):
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        out = []
        for r in rows:
            ts = r[0]
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(
                    tzinfo=timezone.utc
                )
            out.append((ts, r[1:]))
        return out
    except Exception as exc:
        if not silent:
            log.warning("fetch: %s", exc)
        return []


def _fetch_flat(conn, sql, params=()):
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        out = []
        for r in rows:
            ts = r[0]
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(
                    tzinfo=timezone.utc
                )
            out.append((ts, r[1]))
        return out
    except Exception as exc:
        log.warning("fetch: %s", exc)
        return []


def _sel(cols, table, where):
    return (
        "SELECT "
        + ", ".join(cols)
        + " FROM "
        + table
        + " WHERE "
        + where
    )


def fetch_features(symbol, limit=100000):
    cols = ["timestamp"] + INTERNAL_COLS
    sql = _sel(
        cols,
        "features_hourly",
        "symbol = %s ORDER BY timestamp LIMIT %s",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol, limit))
    except Exception as exc:
        log.warning("features %s: %s", symbol, exc)
        return []


def fetch_candles(symbol, limit=100000):
    cols = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
    ]
    sql = _sel(
        cols,
        "candles",
        "symbol = %s AND timeframe = '1h' "
        "AND market_type = 'futures' "
        "ORDER BY timestamp LIMIT %s",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol, limit))
    except Exception as exc:
        log.warning("candles %s: %s", symbol, exc)
        return []


def fetch_macro():
    if not DB2_OK:
        return []
    cols = ["timestamp", "close"]
    sql = _sel(
        cols,
        "global_market",
        "symbol = 'US10Y' AND close IS NOT NULL "
        "ORDER BY timestamp",
    )
    try:
        with get_conn_db2() as conn:
            return _fetch(conn, sql)
    except Exception as exc:
        log.warning("macro: %s", exc)
        return []


def fetch_onchain():
    cols = ["timestamp", "hashrate"]
    sql = _sel(
        cols,
        "onchain_metrics",
        "symbol = 'BTC' AND hashrate IS NOT NULL "
        "ORDER BY timestamp",
    )
    try:
        with get_connection() as conn:
            return _fetch(conn, sql)
    except Exception as exc:
        log.warning("onchain: %s", exc)
        return []


def fetch_orderbook(symbol):
    cols = [
        "timestamp",
        "bid_pct",
        "ask_pct",
        "spread_pct",
    ]
    sql = _sel(
        cols,
        "orderbook_snapshots",
        "symbol = %s AND bid_pct IS NOT NULL "
        "ORDER BY timestamp",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(
                conn, sql, (symbol,), silent=True,
            )
    except Exception:
        return []


def fetch_events(symbol):
    cols = ["timestamp", "event_type"]
    sql = _sel(
        cols,
        "events",
        "symbol = %s ORDER BY timestamp",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch_flat(conn, sql, (symbol,))
    except Exception as exc:
        log.warning("events %s: %s", symbol, exc)
        return []


def fetch_anomaly(symbol):
    cols = ["timestamp", "anomaly_type"]
    sql = _sel(
        cols,
        "anomaly_log",
        "symbol = %s ORDER BY timestamp",
    )
    try:
        with symbol_conn(symbol) as conn:
            return _fetch_flat(conn, sql, (symbol,))
    except Exception as exc:
        log.warning("anom %s: %s", symbol, exc)
        return []


def fetch_asia_market():
    if not DB2_OK:
        return {}
    try:
        with get_conn_db2() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, timestamp, "
                    "change_pct FROM global_market "
                    "WHERE change_pct IS NOT NULL "
                    "ORDER BY symbol, timestamp"
                )
                out = {}
                for sym, ts, ch in cur.fetchall():
                    if ts is None:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(
                            tzinfo=timezone.utc
                        )
                    out.setdefault(
                        sym, []
                    ).append((ts, float(ch)))
                return out
    except Exception as exc:
        log.warning("global_market: %s", exc)
        return {}


def asof(series, ts, max_age_h):
    if not series:
        return None
    best = None
    for s_ts, s_val in series:
        if s_ts <= ts:
            best = (s_ts, s_val)
        else:
            break
    if best is None:
        return None
    if ts - best[0] > timedelta(
        hours=max_age_h
    ):
        return None
    return best[1]


def asof_shift(series, ts, shift_h, max_age_h):
    return asof(
        series,
        ts - timedelta(hours=shift_h),
        max_age_h,
    )


def asia_at(asia_list, ts, lag_hours):
    if not asia_list:
        return None
    target = ts - timedelta(hours=lag_hours)
    best_val = None
    best_diff = None
    for a_ts, a_val in asia_list:
        diff = abs(
            (a_ts - target).total_seconds()
        )
        if diff <= ASIA_TOL_SEC:
            if best_diff is None or diff < best_diff:
                best_val = a_val
                best_diff = diff
    return best_val


def asia_impact_score(asia, ts):
    parts = []
    sh = asia_at(asia.get("SHANGHAI", []), ts, 6)
    if sh is not None:
        parts.append(-sh)
    for src in ["DAX", "NASDAQ", "NIKKEI"]:
        v = asia_at(asia.get(src, []), ts, 6)
        if v is not None:
            parts.append(v)
    v = asia_at(asia.get("VIX", []), ts, 1)
    if v is not None:
        parts.append(-v)
    if not parts:
        return None
    return round(sum(parts), 4)


def _safe(v):
    if v is None:
        return np.nan
    try:
        return float(v)
    except Exception:
        return np.nan


def _macro_feats(series, ts):
    now = asof(series, ts, MAX_AGE["macro"])
    p = asof_shift(
        series, ts, 24, MAX_AGE["macro"]
    )
    lvl = np.nan
    ch = np.nan
    if now is not None:
        lvl = now[0]
    if now is not None and p is not None:
        if p[0] != 0:
            ch = (
                (now[0] - p[0])
                / abs(p[0])
                * 100
            )
    return [_safe(lvl), _safe(ch)]


def _onchain_feats(series, ts):
    now = asof(
        series, ts, MAX_AGE["onchain"]
    )
    p = asof_shift(
        series, ts, 24, MAX_AGE["onchain"]
    )
    lh = np.nan
    ch = np.nan
    if now is not None and now[0] > 0:
        lh = float(np.log(now[0]))
    if now is not None and p is not None:
        if p[0] > 0:
            ch = (now[0] - p[0]) / p[0] * 100
    return [_safe(lh), _safe(ch)]


def _ob_feats(series, ts):
    now = asof(series, ts, MAX_AGE["orderbook"])
    if now is None:
        return [np.nan, np.nan, np.nan]
    return [_safe(now[0]), _safe(now[1]),
            _safe(now[2])]


def _count_events(series, ts, hours):
    if not series:
        return 0
    cutoff = ts - timedelta(hours=hours)
    n = 0
    for e_ts, _ in series:
        if e_ts > cutoff and e_ts <= ts:
            n += 1
    return n


def _count_type(series, ts, hours, etype):
    if not series:
        return 0
    cutoff = ts - timedelta(hours=hours)
    n = 0
    for e_ts, e_data in series:
        if e_ts <= cutoff or e_ts > ts:
            continue
        e_val = e_data
        if isinstance(e_val, tuple):
            e_val = e_val[0]
        if e_val == etype:
            n += 1
    return n


def _event_feats(series, ts):
    return [
        _count_events(series, ts, 1),
        _count_events(series, ts, 24),
        _count_type(
            series, ts, 24, "rsi_overbought"
        ),
    ]


def _anom_feats(series, ts):
    return [_count_events(series, ts, 24)]


def _asia_feats(asia, ts):
    return [
        asia_at(asia.get("NIKKEI", []), ts, 6),
        asia_at(asia.get("SHANGHAI", []), ts, 6),
        asia_at(asia.get("SHANGHAI", []), ts, 12),
        asia_at(asia.get("HANGSENG", []), ts, 6),
        asia_at(asia.get("USDCNY", []), ts, 6),
        asia_at(asia.get("USDCNY", []), ts, 12),
        asia_at(asia.get("DAX", []), ts, 6),
        asia_at(asia.get("SX5E", []), ts, 6),
        asia_at(asia.get("FTSE", []), ts, 6),
        asia_at(asia.get("EURUSD", []), ts, 6),
        asia_at(asia.get("VIX", []), ts, 1),
        asia_at(asia.get("NASDAQ", []), ts, 1),
        asia_at(asia.get("US10Y", []), ts, 6),
        asia_at(asia.get("USDJPY", []), ts, 6),
        asia_at(asia.get("KOSPI", []), ts, 6),
        asia_at(asia.get("TAIEX", []), ts, 6),
        asia_impact_score(asia, ts),
    ]


def _ext_feats(dxy, spx, gold, ts):
    v1 = asof(dxy, ts, 3)
    v2 = asof(spx, ts, 3)
    v3 = asof(gold, ts, 3)
    return [
        _safe(v1[0]) if v1 else np.nan,
        _safe(v2[0]) if v2 else np.nan,
        _safe(v3[0]) if v3 else np.nan,
    ]


def build_targets(candles, horizon):
    out = {}
    n = len(candles)
    for i in range(n):
        ts = candles[i][0]
        if i + horizon >= n:
            out[ts] = None
            continue
        c0 = candles[i][1][3]
        c1 = candles[i + horizon][1][3]
        if not c0 or not c1 or c0 <= 0:
            out[ts] = None
            continue
        out[ts] = (c1 - c0) / c0 * 100
    return out


def _row_for(symbol, d, asia, ts,
             ext_dxy, ext_spx, ext_gold):
    row = []
    for i in range(len(INTERNAL_COLS)):
        v = d["fa"][i].get(ts)
        row.append(v if v is not None else np.nan)
    row.extend(_macro_feats(d["macro"], ts))
    row.extend(_onchain_feats(d["onchain"], ts))
    row.extend(_ob_feats(d["ob"], ts))
    row.extend(_event_feats(d["events"], ts))
    row.extend(_anom_feats(d["anom"], ts))
    row.extend(_asia_feats(asia, ts))
    if USE_EXTERNAL:
        row.extend(_ext_feats(
            ext_dxy, ext_spx, ext_gold, ts
        ))
    return row


def build_xy_all(
    symbols_data, targets_map, asia,
    ext_dxy, ext_spx, ext_gold,
):
    X, y_dir, y_ret = [], [], []
    ts_list, sym_list = [], []

    for symbol in SYMBOLS:
        d = symbols_data.get(symbol)
        if not d:
            continue
        targets = targets_map.get(symbol) or {}

        for ts in sorted(d["feats"]):
            ret = targets.get(ts)
            if ret is None:
                continue
            row = _row_for(
                symbol, d, asia, ts,
                ext_dxy, ext_spx, ext_gold,
            )
            X.append(row)
            y_dir.append(1 if ret > 0 else 0)
            y_ret.append(float(ret))
            ts_list.append(ts)
            sym_list.append(symbol)

    if not X:
        return (
            np.array([], dtype=np.float32),
            np.array([], dtype=np.int32),
            np.array([], dtype=np.float32),
            [], [],
        )

    order = sorted(
        range(len(ts_list)),
        key=lambda i: ts_list[i],
    )
    X = [X[i] for i in order]
    y_dir = [y_dir[i] for i in order]
    y_ret = [y_ret[i] for i in order]
    ts_list = [ts_list[i] for i in order]
    sym_list = [sym_list[i] for i in order]

    return (
        np.array(X, dtype=np.float32),
        np.array(y_dir, dtype=np.int32),
        np.array(y_ret, dtype=np.float32),
        ts_list,
        sym_list,
    )


def per_symbol_split(
    X, y, y_ret, ts_list, sym_list,
    test_frac=0.2,
):
    X = np.asarray(X)
    y = np.asarray(y)
    y_ret = np.asarray(y_ret)

    train_idx, test_idx = [], []

    for sym in sorted(set(sym_list)):
        idxs = [
            i for i, s in enumerate(sym_list)
            if s == sym
        ]
        idxs_sorted = sorted(
            idxs, key=lambda i: ts_list[i]
        )
        n = len(idxs_sorted)
        if n < 20:
            train_idx.extend(idxs_sorted)
            continue
        split = int(n * (1 - test_frac))

        purge = min(HORIZON, split)
        train_keep = split - purge
        if train_keep < 20:
            train_keep = split

        train_idx.extend(
            idxs_sorted[:train_keep]
        )
        test_idx.extend(idxs_sorted[split:])
        log.info(
            "  %s: train=%d (purged %d) test=%d",
            sym, train_keep, purge,
            n - split,
        )

    train_idx.sort(key=lambda i: ts_list[i])
    test_idx.sort(key=lambda i: ts_list[i])

    return (
        X[train_idx], y[train_idx],
        y_ret[train_idx],
        X[test_idx], y[test_idx],
        y_ret[test_idx],
        [ts_list[i] for i in train_idx],
        [ts_list[i] for i in test_idx],
        [sym_list[i] for i in train_idx],
        [sym_list[i] for i in test_idx],
    )


def _finalize_X(X_train, X_test):
    X_train = np.asarray(
        X_train, dtype=np.float64
    ).copy()
    X_test = np.asarray(
        X_test, dtype=np.float64
    ).copy()

    X_train[~np.isfinite(X_train)] = np.nan
    X_test[~np.isfinite(X_test)] = np.nan

    med = np.nanmedian(X_train, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)

    m = np.isnan(X_train)
    if m.any():
        X_train[m] = np.take(
            med, np.where(m)[1]
        )
    m = np.isnan(X_test)
    if m.any():
        X_test[m] = np.take(
            med, np.where(m)[1]
        )

    lo = np.nanquantile(
        X_train, 0.001, axis=0
    )
    hi = np.nanquantile(
        X_train, 0.999, axis=0
    )
    X_test = np.clip(X_test, lo, hi)

    return (
        X_train.astype(np.float32),
        X_test.astype(np.float32),
    )


def _build_feat_arrays(rows):
    feats = {}
    fa = [dict() for _ in INTERNAL_COLS]
    for ts, vals in rows:
        feats[ts] = True
        for i, v in enumerate(vals):
            if v is None:
                continue
            try:
                fa[i][ts] = float(v)
            except Exception:
                pass
    return feats, fa


def _load_symbol(symbol):
    rows = fetch_features(symbol)
    if not rows:
        return None

    feats, fa = _build_feat_arrays(rows)
    if not feats:
        return None

    candles = fetch_candles(symbol)
    if not candles:
        return None

    return {
        "feats": feats,
        "fa": fa,
        "macro": fetch_macro(),
        "onchain": fetch_onchain(),
        "ob": fetch_orderbook(symbol),
        "events": fetch_events(symbol),
        "anom": fetch_anomaly(symbol),
        "candles": candles,
    }


def prepare(test_frac=0.2):
    log.info("=" * 60)
    log.info("DATASET v16")
    log.info("SYMBOLS=%s", SYMBOLS)
    log.info("HORIZON=%dh", HORIZON)
    log.info("DB2_OK=%s", DB2_OK)
    log.info(
        "features expected: %d",
        len(FEATURE_COLS),
    )
    log.info("=" * 60)

    asia = fetch_asia_market()
    log.info(
        "global_market: %d symbols", len(asia)
    )

    ext_dxy = []
    ext_spx = []
    ext_gold = []

    symbols_data = {}
    targets_map = {}

    for symbol in SYMBOLS:
        d = _load_symbol(symbol)
        if d is None:
            log.warning(
                "%s: no data (skip)", symbol
            )
            continue
        symbols_data[symbol] = d
        targets_map[symbol] = build_targets(
            d["candles"], HORIZON
        )
        log.info(
            "%s: feat=%d candles=%d "
            "ob=%d ev=%d an=%d",
            symbol, len(d["feats"]),
            len(d["candles"]),
            len(d["ob"]),
            len(d["events"]), len(d["anom"]),
        )

    if REFERENCE not in symbols_data:
        log.error(
            "REFERENCE %s missing", REFERENCE
        )
        return None

    X, y_dir, y_ret, ts, sym = build_xy_all(
        symbols_data, targets_map, asia,
        ext_dxy, ext_spx, ext_gold,
    )

    log.info("samples: %d", len(X))

    if len(X) < 100:
        log.error("too few samples")
        return None

    log.info("per-symbol time split:")
    (
        X_train, y_train, r_train,
        X_test, y_test, r_test,
        ts_train, ts_test,
        sym_train, sym_test,
    ) = per_symbol_split(
        X, y_dir, y_ret, ts, sym, test_frac,
    )

    X_train, X_test = _finalize_X(
        X_train, X_test
    )

    nan_tr = int(np.isnan(X_train).sum())
    nan_te = int(np.isnan(X_test).sum())
    log.info(
        "finalize: nan_train=%d nan_test=%d",
        nan_tr, nan_te,
    )

    balance = {
        "up_train": int(y_train.sum()),
        "down_train": int(
            len(y_train) - y_train.sum()
        ),
        "ret_mean": float(r_train.mean()),
        "ret_std": float(r_train.std()),
    }

    log.info(
        "split: train=%d test=%d features=%d",
        len(X_train), len(X_test),
        len(FEATURE_COLS),
    )
    log.info(
        "ret_train: mean=%.4f std=%.4f",
        balance["ret_mean"],
        balance["ret_std"],
    )

    return {
        "X_train": X_train,
        "y_train": y_train,
        "X_test": X_test,
        "y_test": y_test,
        "r_train": r_train,
        "r_test": r_test,
        "n_total": len(X),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "balance": balance,
        "feature_cols": FEATURE_COLS,
        "horizon": HORIZON,
        "symbols": sorted(set(sym)),
        "reference": REFERENCE,
        "ts_test": ts_test,
        "sym_test": sym_test,
    }


def prepare_one(symbol):
    d = _load_symbol(symbol)
    if d is None:
        return None, None

    feats = d["feats"]
    if not feats:
        return None, None

    latest_ts = max(feats.keys())
    asia = fetch_asia_market()

    row = _row_for(
        symbol, d, asia, latest_ts,
        [], [], [],
    )
    return latest_ts, row


def main():
    data = prepare()
    if data is None:
        return
    log.info(
        "total=%d train=%d test=%d features=%d",
        data["n_total"],
        data["n_train"], data["n_test"],
        len(data["feature_cols"]),
    )


if __name__ == "__main__":
    main()