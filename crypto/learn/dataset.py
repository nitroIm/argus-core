# ============================================================
# ARGUS-Trader - DATASET v8.1 [PRODUCTION]
# ------------------------------------------------------------
# v8.1: MOVE_THRESHOLD_PCT kept as dummy const for export.py
#       backwards compat. Not used in target building.
# v8.0: regression target = next_return. No threshold.
# v7.2: per-symbol time split. USE_CROSS optional.
# v7: config-driven symbols + DB routing.
# ============================================================

import os
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

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
        from db2 import get_connection as get_conn_db2
        _t = get_conn_db2()
        with _t as _c:
            with _c.cursor() as _cur:
                _cur.execute("SELECT 1")
                _cur.fetchone()
        DB2_OK = True
    except Exception as e:
        print("DB2 fail: " + str(e))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.dataset")

USE_EXTERNAL = (
    os.getenv("USE_EXTERNAL", "0").strip() == "1"
)
USE_CROSS = (
    os.getenv("USE_CROSS", "1").strip() == "1"
)

HORIZON = int(os.getenv("HORIZON", "12"))

# Dummy для export.py compat. Не используется в v8 target.
MOVE_THRESHOLD_PCT = float(
    os.getenv("MOVE_THRESHOLD_PCT", "0.5")
)

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
        os.getenv("DB2_SYMBOLS") or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        try:
            return get_conn_db2()
        except Exception as e:
            log.warning("db2 conn %s: %s", symbol, e)
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
    "change_7d",
    "change_1d",
    "change_3d",
    "trend_up",
    "hour_of_day",
    "day_of_week",
    "funding_rate",
    "funding_trend",
    "oi_change_pct",
    "ls_ratio",
    "taker_ratio",
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
]

EXTERNAL_COLS = [
    "dxy_change_pct",
    "spx_change_pct",
    "gold_change_pct",
]

_CROSS_ALL = [
    "ref_change_1h",
    "ref_change_4h",
    "ref_change_24h",
    "ratio",
    "ratio_zscore_24h",
    "lead_lag_corr_24h",
    "spread_pct",
]

CROSS_COLS = _CROSS_ALL if USE_CROSS else []

FEATURE_COLS = (
    INTERNAL_COLS
    + (EXTERNAL_COLS if USE_EXTERNAL else [])
    + CROSS_COLS
)

TARGET_DIR = "next_direction"
TARGET_RET = "next_return"
TARGET_COL = TARGET_RET

EXT_MAX_AGE_H = 3


def fetch_features(symbol, limit=100000):
    base_cols = ["symbol", "timestamp"] + INTERNAL_COLS
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT " + ", ".join(base_cols)
                    + " FROM features_hourly "
                    + "WHERE symbol = %s "
                    + "ORDER BY timestamp LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
                return cur.fetchall()
    except Exception as e:
        log.warning("features %s: %s", symbol, e)
        return []


def fetch_candles(symbol, limit=100000):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, low, "
                    "close FROM candles "
                    "WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "ORDER BY timestamp LIMIT %s",
                    (symbol, limit),
                )
                rows = cur.fetchall()
                out = []
                for r in rows:
                    ts = r[0]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    out.append({
                        "ts": ts,
                        "open": float(r[1]) if r[1] else None,
                        "high": float(r[2]) if r[2] else None,
                        "low": float(r[3]) if r[3] else None,
                        "close": float(r[4]) if r[4] else None,
                    })
                return out
    except Exception as e:
        log.warning("candles %s: %s", symbol, e)
        return []


def fetch_external(symbol):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, change_pct "
                    "FROM external_market "
                    "WHERE symbol = %s "
                    "AND change_pct IS NOT NULL "
                    "ORDER BY timestamp",
                    (symbol,),
                )
                out = []
                for r in cur.fetchall():
                    ts = r[0]
                    v = float(r[1]) if r[1] else None
                    if ts and v is not None:
                        if ts.tzinfo is None:
                            ts = ts.replace(
                                tzinfo=timezone.utc
                            )
                        out.append((ts, v))
                return out
    except Exception as e:
        log.warning("external %s: %s", symbol, e)
        return []


def build_targets(candles, horizon):
    out = {}
    n = len(candles)
    for i in range(n):
        if i + horizon >= n:
            out[candles[i]["ts"]] = None
            continue
        c0 = candles[i]["close"]
        c1 = candles[i + horizon]["close"]
        if not c0 or not c1 or c0 <= 0:
            out[candles[i]["ts"]] = None
            continue
        ret = (c1 - c0) / c0 * 100
        out[candles[i]["ts"]] = ret
    return out


def build_cross_full(
    feat_maps, candle_maps, ref_symbol,
):
    if not CROSS_COLS:
        return {s: {} for s in feat_maps}
    ref_feat = feat_maps.get(ref_symbol) or {}
    ref_close = candle_maps.get(ref_symbol) or {}

    ref_ch = {
        ts: f.get("change_pct")
        for ts, f in ref_feat.items()
    }
    ref_ch4 = {
        ts: f.get("change_4h")
        for ts, f in ref_feat.items()
    }
    ref_ch24 = {
        ts: f.get("change_24h")
        for ts, f in ref_feat.items()
    }

    out = {}
    for symbol, feats in feat_maps.items():
        sym_close = candle_maps.get(symbol) or {}
        common_ts = sorted(
            set(feats.keys()) & set(ref_close.keys())
        )

        ratio = {}
        for ts in common_ts:
            sc = sym_close.get(ts)
            rc = ref_close.get(ts)
            if sc and rc and rc > 0:
                ratio[ts] = sc / rc * 1000

        ratio_ts = sorted(ratio.keys())
        zscore = {}
        for i in range(len(ratio_ts)):
            if i < 24:
                zscore[ratio_ts[i]] = np.nan
                continue
            window = [
                ratio[ratio_ts[j]]
                for j in range(i - 24, i)
            ]
            arr = np.array(window, dtype=float)
            mu = arr.mean()
            sd = arr.std()
            zscore[ratio_ts[i]] = (
                0.0 if sd == 0
                else float((ratio[ratio_ts[i]] - mu) / sd)
            )

        corr_ts = sorted(
            set(feats.keys()) & set(ref_feat.keys())
        )
        corr = {}
        window = 24
        for i in range(len(corr_ts)):
            if i < window - 1:
                corr[corr_ts[i]] = np.nan
                continue
            xs, ys = [], []
            for j in range(i - window + 1, i + 1):
                t = corr_ts[j]
                a = feats.get(t, {}).get("change_pct")
                b = ref_feat.get(t, {}).get("change_pct")
                if a is not None and b is not None:
                    xs.append(a)
                    ys.append(b)
            if len(xs) < 5:
                corr[corr_ts[i]] = np.nan
                continue
            A = np.array(xs, dtype=float)
            B = np.array(ys, dtype=float)
            if A.std() == 0 or B.std() == 0:
                corr[corr_ts[i]] = np.nan
                continue
            corr_val = float(np.corrcoef(A, B)[0, 1])
            corr[corr_ts[i]] = corr_val

        cross_map = {}
        for ts in feats:
            if symbol == ref_symbol:
                cross_map[ts] = {
                    c: np.nan for c in CROSS_COLS
                }
                continue
            own_4h = feats[ts].get("change_4h")
            ref_4h = ref_ch4.get(ts)
            spread = None
            if own_4h is not None and ref_4h is not None:
                spread = own_4h - ref_4h
            cross_map[ts] = {
                "ref_change_1h": (
                    ref_ch.get(ts)
                    if ref_ch.get(ts) is not None
                    else np.nan
                ),
                "ref_change_4h": (
                    ref_ch4.get(ts)
                    if ref_ch4.get(ts) is not None
                    else np.nan
                ),
                "ref_change_24h": (
                    ref_ch24.get(ts)
                    if ref_ch24.get(ts) is not None
                    else np.nan
                ),
                "ratio": ratio.get(ts, np.nan),
                "ratio_zscore_24h": zscore.get(ts, np.nan),
                "lead_lag_corr_24h": corr.get(ts, np.nan),
                "spread_pct": (
                    spread if spread is not None
                    else np.nan
                ),
            }
        out[symbol] = cross_map
    return out


def ext_lookup(ext_list, ts, max_age_h=EXT_MAX_AGE_H):
    if not ext_list:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    result = None
    for e_ts, e_val in ext_list:
        if e_ts <= ts:
            result = (e_ts, e_val)
        else:
            break
    if result is None:
        return None
    if ts - result[0] > timedelta(hours=max_age_h):
        return None
    return result[1]


def _get_feat_map(rows, base_cols):
    idx = {col: i for i, col in enumerate(base_cols)}
    out = {}
    for r in rows:
        ts = r[idx["timestamp"]]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        feats = {}
        for col in INTERNAL_COLS:
            v = r[idx[col]]
            if v is None:
                feats[col] = np.nan
            else:
                try:
                    feats[col] = float(v)
                except Exception:
                    feats[col] = np.nan
        out[ts] = feats
    return out


def build_xy(
    feat_maps, candle_maps, close_maps,
    targets_map, ext_dxy, ext_spx, ext_gold,
):
    cross_maps = build_cross_full(
        feat_maps, close_maps, REFERENCE,
    )

    X, y_dir, y_ret, ts_list, sym_list = [], [], [], [], []

    for symbol in SYMBOLS:
        feats = feat_maps.get(symbol) or {}
        targets = targets_map.get(symbol) or {}
        cross = cross_maps.get(symbol) or {}

        for ts in sorted(feats.keys()):
            ret = targets.get(ts)
            if ret is None:
                continue

            direction = 1 if ret > 0 else 0

            row = []
            f = feats[ts]

            for col in INTERNAL_COLS:
                row.append(f.get(col, np.nan))

            if USE_EXTERNAL:
                v = ext_lookup(ext_dxy, ts)
                row.append(v if v is not None else np.nan)
                v = ext_lookup(ext_spx, ts)
                row.append(v if v is not None else np.nan)
                v = ext_lookup(ext_gold, ts)
                row.append(v if v is not None else np.nan)

            cm = cross.get(ts, {})
            for col in CROSS_COLS:
                row.append(cm.get(col, np.nan))

            X.append(row)
            y_dir.append(direction)
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
        range(len(ts_list)), key=lambda i: ts_list[i]
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

    train_idx = []
    test_idx = []

    for sym in sorted(set(sym_list)):
        idxs = [
            i for i, s in enumerate(sym_list)
            if s == sym
        ]
        idxs_sorted = sorted(idxs, key=lambda i: ts_list[i])
        n = len(idxs_sorted)
        if n < 20:
            log.warning(
                "%s: too few rows (%d) — all to train",
                sym, n,
            )
            train_idx.extend(idxs_sorted)
            continue
        split = int(n * (1 - test_frac))
        train_idx.extend(idxs_sorted[:split])
        test_idx.extend(idxs_sorted[split:])
        log.info(
            "  %s: train=%d test=%d",
            sym, split, n - split,
        )

    train_idx.sort(key=lambda i: ts_list[i])
    test_idx.sort(key=lambda i: ts_list[i])

    return (
        X[train_idx], y[train_idx], y_ret[train_idx],
        X[test_idx], y[test_idx], y_ret[test_idx],
    )


def prepare(test_frac=0.2):
    log.info("=" * 60)
    log.info("DATASET v8.1 (regression)")
    log.info("SYMBOLS=%s", SYMBOLS)
    log.info("REFERENCE=%s", REFERENCE)
    log.info("USE_CROSS=%s", USE_CROSS)
    log.info("HORIZON=%dh  TARGET=next_return", HORIZON)
    log.info(
        "DB2_SYMBOLS=%s (DB2_OK=%s)",
        sorted(DB2_SYMBOLS), DB2_OK,
    )
    log.info("=" * 60)

    feat_maps = {}
    candle_maps = {}
    close_maps = {}
    targets_map = {}

    for symbol in SYMBOLS:
        rows = fetch_features(symbol)
        if not rows:
            log.warning("%s: no features (skip)", symbol)
            continue

        base_cols = ["symbol", "timestamp"] + INTERNAL_COLS
        feat_maps[symbol] = _get_feat_map(rows, base_cols)

        candles = fetch_candles(symbol)
        if not candles:
            log.warning("%s: no candles (skip)", symbol)
            continue

        candle_maps[symbol] = candles
        close_maps[symbol] = {
            c["ts"]: c["close"] for c in candles
            if c["close"]
        }
        targets_map[symbol] = build_targets(
            candles, HORIZON,
        )
        log.info(
            "%s: features=%d candles=%d",
            symbol, len(feat_maps[symbol]),
            len(candles),
        )

    if REFERENCE not in feat_maps:
        log.error(
            "REFERENCE %s has no data — abort",
            REFERENCE,
        )
        return None

    if USE_EXTERNAL:
        ext_dxy = fetch_external("DXY")
        ext_spx = fetch_external("SPX")
        ext_gold = fetch_external("GOLD")
        log.info(
            "external: DXY=%d SPX=%d GOLD=%d",
            len(ext_dxy), len(ext_spx),
            len(ext_gold),
        )
    else:
        ext_dxy = []
        ext_spx = []
        ext_gold = []

    X, y_dir, y_ret, ts, sym = build_xy(
        feat_maps, candle_maps, close_maps,
        targets_map, ext_dxy, ext_spx, ext_gold,
    )

    log.info("samples: %d (no threshold)", len(X))

    if len(X) < 100:
        log.error("too few samples: %d", len(X))
        return None

    log.info("per-symbol time split:")
    (
        X_train, y_train, r_train,
        X_test, y_test, r_test,
    ) = per_symbol_split(
        X, y_dir, y_ret, ts, sym, test_frac,
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
        "balance train: up=%d down=%d",
        balance["up_train"],
        balance["down_train"],
    )
    log.info(
        "ret_train: mean=%.4f std=%.4f",
        balance["ret_mean"], balance["ret_std"],
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
    }


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
    log.info("symbols in dataset: %s", data["symbols"])


if __name__ == "__main__":
    main()