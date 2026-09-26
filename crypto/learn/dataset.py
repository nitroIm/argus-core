# ============================================================
# ARGUS-Trader - DATASET v3.1 [PRODUCTION]
# ------------------------------------------------------------
# v3.1: threshold 0.15 (было 0.30 - слишком жёстко)
# v3: + threshold MIN_MOVE_PCT
# v2: + external market + eth_btc_ratio
# v1: базовое чтение features_hourly
# ============================================================

import sys
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.dataset")

FEATURE_COLS = [
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
    "dxy_change_pct",
    "spx_change_pct",
    "gold_change_pct",
    "eth_btc_ratio",
]

TARGET_COL = "next_direction"

# Threshold: учимся только на значимых движениях
MIN_MOVE_PCT = 0.15

EXT_MAX_AGE_H = 3

EXTERNAL_COLS = (
    "dxy_change_pct",
    "spx_change_pct",
    "gold_change_pct",
    "eth_btc_ratio",
)


def fetch_features(symbol=None, limit=100000):
    """Читает features_hourly с фильтром threshold."""
    internal = [
        c for c in FEATURE_COLS
        if c not in EXTERNAL_COLS
    ]
    base_cols = (
        ["symbol", "timestamp"]
        + internal
        + [TARGET_COL]
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if symbol:
                    sql = (
                        "SELECT " + ", ".join(base_cols)
                        + " FROM features_hourly "
                        + "WHERE symbol = %s "
                        + "AND " + TARGET_COL
                        + " IS NOT NULL "
                        + "AND ABS(next_change_pct) >= %s "
                        + "ORDER BY timestamp "
                        + "LIMIT %s"
                    )
                    cur.execute(
                        sql, (symbol, MIN_MOVE_PCT, limit)
                    )
                else:
                    sql = (
                        "SELECT " + ", ".join(base_cols)
                        + " FROM features_hourly "
                        + "WHERE " + TARGET_COL
                        + " IS NOT NULL "
                        + "AND ABS(next_change_pct) >= %s "
                        + "ORDER BY timestamp "
                        + "LIMIT %s"
                    )
                    cur.execute(sql, (MIN_MOVE_PCT, limit))
                return cur.fetchall(), base_cols
    except Exception as e:
        log.error("fetch_features: %s", e)
        return [], []


def fetch_external(symbol):
    """Читает external_market для DXY/SPX/GOLD."""
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
        log.error("fetch_external %s: %s", symbol, e)
        return []


def fetch_eth_btc():
    """ts -> ratio ETH_close / BTC_close."""
    try:
        btc = {}
        eth = {}
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, close FROM candles "
                    "WHERE symbol = 'BTCUSDT' "
                    "AND timeframe = '1h' "
                    "ORDER BY timestamp"
                )
                for r in cur.fetchall():
                    ts = r[0]
                    c = float(r[1]) if r[1] else None
                    if ts and c and c > 0:
                        if ts.tzinfo is None:
                            ts = ts.replace(
                                tzinfo=timezone.utc
                            )
                        btc[ts] = c

                cur.execute(
                    "SELECT timestamp, close FROM candles "
                    "WHERE symbol = 'ETHUSDT' "
                    "AND timeframe = '1h' "
                    "ORDER BY timestamp"
                )
                for r in cur.fetchall():
                    ts = r[0]
                    c = float(r[1]) if r[1] else None
                    if ts and c and c > 0:
                        if ts.tzinfo is None:
                            ts = ts.replace(
                                tzinfo=timezone.utc
                            )
                        eth[ts] = c

        ratio = {}
        for ts, ec in eth.items():
            bc = btc.get(ts)
            if bc and bc > 0:
                ratio[ts] = ec / bc * 1000
        return ratio
    except Exception as e:
        log.error("fetch_eth_btc: %s", e)
        return {}


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


def rows_to_xy(rows, base_cols, ext_dxy,
               ext_spx, ext_gold, eth_btc):
    if not rows:
        return None, None, [], []

    n_base = len(base_cols)
    target_idx = n_base - 1
    feat_start = 2

    X = []
    y = []
    timestamps = []
    symbols = []

    for r in rows:
        ts = r[1]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        timestamps.append(ts)
        symbols.append(r[0])

        row_feats = []
        for i in range(feat_start, target_idx):
            v = r[i]
            if v is None:
                row_feats.append(np.nan)
            else:
                try:
                    row_feats.append(float(v))
                except Exception:
                    row_feats.append(np.nan)

        dxy = ext_lookup(ext_dxy, ts)
        spx = ext_lookup(ext_spx, ts)
        gold = ext_lookup(ext_gold, ts)

        row_feats.append(
            dxy if dxy is not None else np.nan
        )
        row_feats.append(
            spx if spx is not None else np.nan
        )
        row_feats.append(
            gold if gold is not None else np.nan
        )

        if r[0] == "ETHUSDT":
            ratio = eth_btc.get(ts)
            row_feats.append(
                ratio if ratio is not None else np.nan
            )
        else:
            row_feats.append(np.nan)

        X.append(row_feats)

        target = r[target_idx]
        try:
            y.append(int(target))
        except Exception:
            y.append(0)

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.int32)
    return X, y, timestamps, symbols


def time_split(X, y, test_frac=0.2):
    n = len(X)
    if n < 20:
        return X, y, X, y

    split = int(n * (1 - test_frac))
    return (
        X[:split], y[:split],
        X[split:], y[split:],
    )


def symbols_unique(sym_list):
    return sorted(set(sym_list))


def prepare(symbol=None, test_frac=0.2):
    rows, base_cols = fetch_features(symbol)
    log.info(
        "rows loaded: %d (threshold %.2f%%)",
        len(rows), MIN_MOVE_PCT,
    )

    if len(rows) < 20:
        log.warning(
            "not enough rows: %d < 20", len(rows)
        )
        return None

    ext_dxy = fetch_external("DXY")
    ext_spx = fetch_external("SPX")
    ext_gold = fetch_external("GOLD")
    log.info(
        "external: DXY=%d SPX=%d GOLD=%d",
        len(ext_dxy), len(ext_spx), len(ext_gold),
    )

    eth_btc = fetch_eth_btc()
    log.info("eth_btc pairs: %d", len(eth_btc))

    X, y, ts, sym = rows_to_xy(
        rows, base_cols, ext_dxy, ext_spx,
        ext_gold, eth_btc,
    )
    if X is None or len(X) < 20:
        log.warning("not enough samples")
        return None

    X_train, y_train, X_test, y_test = time_split(
        X, y, test_frac
    )

    balance = {
        "up_total": int(y.sum()),
        "down_total": int(len(y) - y.sum()),
        "up_train": int(y_train.sum()),
        "down_train": int(
            len(y_train) - y_train.sum()
        ),
    }

    log.info(
        "split: train=%d test=%d",
        len(X_train), len(X_test),
    )
    log.info(
        "balance train: up=%d down=%d",
        balance["up_train"],
        balance["down_train"],
    )

    return {
        "X_train": X_train,
        "y_train": y_train,
        "X_test": X_test,
        "y_test": y_test,
        "n_total": len(X),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "balance": balance,
        "feature_cols": FEATURE_COLS,
        "symbols": symbols_unique(sym),
    }


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader DATASET v3.1 test")
    log.info("=" * 60)

    data = prepare()
    if data is None:
        log.warning("no data")
        return

    log.info(
        "total=%d train=%d test=%d",
        data["n_total"],
        data["n_train"],
        data["n_test"],
    )
    log.info(
        "X_train shape: %s",
        data["X_train"].shape,
    )
    log.info(
        "X_test shape: %s",
        data["X_test"].shape,
    )
    log.info("symbols: %s", data["symbols"])
    log.info(
        "features: %d", len(data["feature_cols"])
    )


if __name__ == "__main__":
    main()