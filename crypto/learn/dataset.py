# ============================================================
# ARGUS-Trader - DATASET v24
# ------------------------------------------------------------
# v24: FIX Decimal->float in _compute_volatility.
#      HMM regime detection (3 states).
#      Regime labels saved for per-regime training.
#      30 base features (v17) + regime feature.
# ============================================================

import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
from hmmlearn.hmm import GaussianHMM

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
        from db2 import get_connection as get_conn_db2
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

HORIZON = int(os.getenv("HORIZON", "12"))
N_REGIMES = int(os.getenv("N_REGIMES", "3"))
MAX_LOOKBACK = int(os.getenv("MAX_LOOKBACK", "168"))
PURGE_SAFETY = int(os.getenv("PURGE_SAFETY", "24"))
PURGE_HOURS = HORIZON + MAX_LOOKBACK + PURGE_SAFETY

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
SYMBOLS = [
    s.strip().upper()
    for s in (os.getenv("SYMBOLS") or ",".join(DEFAULT_SYMBOLS)).split(",")
    if s.strip()
]
REFERENCE = (os.getenv("REFERENCE") or "BTCUSDT").strip().upper()
DB2_SYMBOLS = {
    s.strip().upper()
    for s in (os.getenv("DB2_SYMBOLS") or "SOLUSDT,BNBUSDT").split(",")
    if s.strip()
}


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        try:
            return get_conn_db2()
        except Exception as exc:
            log.warning("db2 conn %s: %s", symbol, exc)
    return get_connection()


INTERNAL_COLS = [
    "change_pct", "range_pct", "body_pct",
    "upper_wick_pct", "lower_wick_pct",
    "volume_ratio_24h", "volatility_24h",
    "volatility_7d", "change_4h", "change_24h",
    "change_1d", "trend_up", "hour_of_day",
    "funding_rate", "funding_trend",
    "ema9_dist_pct", "ema21_dist_pct",
    "ema50_dist_pct", "macd", "macd_signal",
    "bb_upper_dist", "bb_lower_dist",
    "bb_width_pct", "dist_high_24h_pct",
    "dist_low_24h_pct", "consecutive_up",
    "session", "oi_change_pct",
    "ls_ratio", "taker_ratio",
]

FEATURE_COLS = list(INTERNAL_COLS)
TARGET_RET = "next_return"
TARGET_COL = "next_change_pct"


def _fetch(conn, sql, params=()):
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
                ts = ts.replace(tzinfo=timezone.utc)
            out.append((ts, r[1:]))
        return out
    except Exception as exc:
        log.warning("fetch: %s", exc)
        return []


def _sel(cols, table, where):
    return "SELECT " + ", ".join(cols) + " FROM " + table + " WHERE " + where


def fetch_features(symbol, limit=100000):
    cols = ["timestamp"] + INTERNAL_COLS
    sql = _sel(cols, "features_hourly",
               "symbol = %s ORDER BY timestamp LIMIT %s")
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol, limit))
    except Exception as exc:
        log.warning("features %s: %s", symbol, exc)
        return []


def fetch_candles(symbol, limit=100000):
    cols = ["timestamp", "open", "high", "low", "close"]
    sql = _sel(cols, "candles",
               "symbol = %s AND timeframe = '1h' "
               "AND market_type = 'futures' "
               "ORDER BY timestamp LIMIT %s")
    try:
        with symbol_conn(symbol) as conn:
            return _fetch(conn, sql, (symbol, limit))
    except Exception as exc:
        log.warning("candles %s: %s", symbol, exc)
        return []


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


def _compute_volatility(closes, window):
    """Rolling std of log returns."""
    # FIX: convert Decimal to float BEFORE np.log.
    closes = [float(c) for c in closes]
    n = len(closes)
    vols = np.full(n, np.nan)
    if n < window + 1:
        return vols
    log_ret = np.zeros(n)
    for i in range(1, n):
        if closes[i - 1] > 0 and closes[i] > 0:
            log_ret[i] = np.log(closes[i] / closes[i - 1])
    for i in range(window, n):
        seg = log_ret[i - window + 1:i + 1]
        vols[i] = float(np.std(seg)) * 100.0
    return vols


def detect_regimes(closes, n_regimes=3):
    """HMM regime detection."""
    # FIX: convert Decimal to float.
    closes = [float(c) for c in closes]
    n = len(closes)
    if n < 100:
        return np.zeros(n, dtype=np.int32)

    returns = np.zeros(n)
    for i in range(1, n):
        if closes[i - 1] > 0:
            returns[i] = (closes[i] - closes[i - 1]) / closes[i - 1]

    X = returns[1:].reshape(-1, 1)
    try:
        model = GaussianHMM(
            n_components=n_regimes,
            covariance_type="diag",
            n_iter=100,
            random_state=42,
        )
        model.fit(X)
        states = model.predict(X)

        vol_per_state = {}
        for s in range(n_regimes):
            mask = states == s
            if mask.sum() > 0:
                vol_per_state[s] = np.std(X[mask])
            else:
                vol_per_state[s] = 0
        sorted_states = sorted(vol_per_state.keys(), key=lambda s: vol_per_state[s])
        remap = {old: new for new, old in enumerate(sorted_states)}

        regimes = np.zeros(n, dtype=np.int32)
        for i, s in enumerate(states):
            regimes[i + 1] = remap.get(s, 0)
        return regimes
    except Exception as exc:
        log.warning("HMM failed: %s", exc)
        return np.zeros(n, dtype=np.int32)


def _row_for(d, ts):
    row = []
    for i in range(len(INTERNAL_COLS)):
        v = d["fa"][i].get(ts)
        row.append(v if v is not None else np.nan)
    return row


def build_xy_all(symbols_data, targets_map):
    X, y_dir, y_ret = [], [], []
    ts_list, sym_list = [], []
    regime_list = []

    for symbol in SYMBOLS:
        d = symbols_data.get(symbol)
        if not d:
            continue
        targets = targets_map.get(symbol) or {}
        regimes = d.get("regimes")

        for ts in sorted(d["feats"]):
            ret = targets.get(ts)
            if ret is None:
                continue
            row = _row_for(d, ts)
            X.append(row)
            y_dir.append(1 if ret > 0 else 0)
            y_ret.append(float(ret))
            ts_list.append(ts)
            sym_list.append(symbol)
            if regimes is not None:
                idx = d["ts_index"].get(ts, 0)
                regime_list.append(int(regimes[idx]) if idx < len(regimes) else 0)
            else:
                regime_list.append(0)

    if not X:
        return (
            np.array([], dtype=np.float32),
            np.array([], dtype=np.int32),
            np.array([], dtype=np.float32),
            [], [], [],
        )

    order = sorted(range(len(ts_list)), key=lambda i: ts_list[i])
    X = [X[i] for i in order]
    y_dir = [y_dir[i] for i in order]
    y_ret = [y_ret[i] for i in order]
    ts_list = [ts_list[i] for i in order]
    sym_list = [sym_list[i] for i in order]
    regime_list = [regime_list[i] for i in order]

    return (
        np.array(X, dtype=np.float32),
        np.array(y_dir, dtype=np.int32),
        np.array(y_ret, dtype=np.float32),
        ts_list, sym_list,
        np.array(regime_list, dtype=np.int32),
    )


def per_symbol_split(X, y, y_ret, ts_list, sym_list, regimes, test_frac=0.2):
    X = np.asarray(X)
    y = np.asarray(y)
    y_ret = np.asarray(y_ret)

    train_idx, test_idx = [], []

    for sym in sorted(set(sym_list)):
        idxs = [i for i, s in enumerate(sym_list) if s == sym]
        idxs_sorted = sorted(idxs, key=lambda i: ts_list[i])
        n = len(idxs_sorted)
        if n < 20:
            train_idx.extend(idxs_sorted)
            continue
        split = int(n * (1 - test_frac))
        purge = min(PURGE_HOURS, split)
        train_keep = split - purge
        if train_keep < 20:
            train_keep = split
        train_idx.extend(idxs_sorted[:train_keep])
        test_idx.extend(idxs_sorted[split:])
        log.info("  %s: train=%d (purged %dh) test=%d",
                 sym, train_keep, purge, n - split)

    train_idx.sort(key=lambda i: ts_list[i])
    test_idx.sort(key=lambda i: ts_list[i])

    return (
        X[train_idx], y[train_idx], y_ret[train_idx],
        X[test_idx], y[test_idx], y_ret[test_idx],
        [ts_list[i] for i in train_idx],
        [ts_list[i] for i in test_idx],
        [sym_list[i] for i in train_idx],
        [sym_list[i] for i in test_idx],
        [regimes[i] for i in train_idx],
        [regimes[i] for i in test_idx],
    )


def _finalize_X(X_train, X_test):
    X_train = np.asarray(X_train, dtype=np.float64).copy()
    X_test = np.asarray(X_test, dtype=np.float64).copy()
    X_train[~np.isfinite(X_train)] = np.nan
    X_test[~np.isfinite(X_test)] = np.nan
    med = np.nanmedian(X_train, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    m = np.isnan(X_train)
    if m.any():
        X_train[m] = np.take(med, np.where(m)[1])
    m = np.isnan(X_test)
    if m.any():
        X_test[m] = np.take(med, np.where(m)[1])
    lo = np.nanquantile(X_train, 0.001, axis=0)
    hi = np.nanquantile(X_train, 0.999, axis=0)
    X_test = np.clip(X_test, lo, hi)
    return X_train.astype(np.float32), X_test.astype(np.float32)


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

    closes = [float(c[1][3]) for c in candles]
    regimes = detect_regimes(closes, N_REGIMES)

    ts_index = {}
    for i, (ts, _) in enumerate(candles):
        ts_index[ts] = i

    return {
        "feats": feats,
        "fa": fa,
        "candles": candles,
        "regimes": regimes,
        "ts_index": ts_index,
    }


def prepare(test_frac=0.2):
    log.info("=" * 60)
    log.info("DATASET v24 (HMM regimes + Decimal fix)")
    log.info("SYMBOLS=%s", SYMBOLS)
    log.info("HORIZON=%dh N_REGIMES=%d", HORIZON, N_REGIMES)
    log.info("PURGE_HOURS=%dh", PURGE_HOURS)
    log.info("features: %d", len(FEATURE_COLS))
    log.info("=" * 60)

    symbols_data = {}
    targets_map = {}

    for symbol in SYMBOLS:
        d = _load_symbol(symbol)
        if d is None:
            log.warning("%s: no data", symbol)
            continue
        symbols_data[symbol] = d
        targets_map[symbol] = build_targets(d["candles"], HORIZON)
        reg_dist = np.bincount(d["regimes"], minlength=N_REGIMES)
        log.info("%s: feat=%d candles=%d regimes=%s",
                 symbol, len(d["feats"]), len(d["candles"]),
                 reg_dist.tolist())

    if REFERENCE not in symbols_data:
        log.error("REFERENCE %s missing", REFERENCE)
        return None

    X, y_dir, y_ret, ts, sym, regimes = build_xy_all(symbols_data, targets_map)
    log.info("samples: %d", len(X))

    if len(X) < 100:
        log.error("too few samples")
        return None

    log.info("per-symbol time split:")
    (X_train, y_train, r_train,
     X_test, y_test, r_test,
     ts_train, ts_test,
     sym_train, sym_test,
     reg_train, reg_test) = per_symbol_split(
        X, y_dir, y_ret, ts, sym, regimes, test_frac,
    )

    X_train, X_test = _finalize_X(X_train, X_test)

    log.info("split: train=%d test=%d features=%d",
             len(X_train), len(X_test), len(FEATURE_COLS))

    reg_tr_dist = np.bincount(reg_train, minlength=N_REGIMES)
    reg_te_dist = np.bincount(reg_test, minlength=N_REGIMES)
    log.info("regimes train: %s", reg_tr_dist.tolist())
    log.info("regimes test:  %s", reg_te_dist.tolist())

    return {
        "X_train": X_train, "y_train": y_train,
        "X_test": X_test, "y_test": y_test,
        "r_train": r_train, "r_test": r_test,
        "reg_train": reg_train, "reg_test": reg_test,
        "n_total": len(X), "n_train": len(X_train), "n_test": len(X_test),
        "feature_cols": FEATURE_COLS,
        "horizon": HORIZON, "purge_hours": PURGE_HOURS,
        "n_regimes": N_REGIMES,
        "symbols": sorted(set(sym)), "reference": REFERENCE,
        "ts_test": ts_test, "sym_test": sym_test,
    }


def prepare_one(symbol):
    d = _load_symbol(symbol)
    if d is None:
        return None, None
    feats = d["feats"]
    if not feats:
        return None, None
    latest_ts = max(feats.keys())
    row = _row_for(d, latest_ts)
    return latest_ts, row


def main():
    data = prepare()
    if data is None:
        return
    log.info("total=%d train=%d test=%d features=%d",
             data["n_total"], data["n_train"],
             data["n_test"], len(data["feature_cols"]))


if __name__ == "__main__":
    main()