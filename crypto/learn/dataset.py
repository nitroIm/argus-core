# ============================================================
# ARGUS-Trader - DATASET
# ------------------------------------------------------------
# Загрузка X, y из features_hourly для ML.
# Train/test split по времени (не случайно!).
# ============================================================

import sys
import logging
from pathlib import Path

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

# Признаки (без symbol, timestamp, target,
# computed_at, next_change_pct)
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
]

TARGET_COL = "next_direction"


def fetch_rows(symbol=None, limit=100000):
    """Возвращает список строк из features_hourly."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if symbol:
                    cur.execute(
                        "SELECT symbol, timestamp, "
                        + ", ".join(FEATURE_COLS)
                        + ", " + TARGET_COL
                        + " FROM features_hourly "
                        + "WHERE symbol = %s "
                        + "AND " + TARGET_COL
                        + " IS NOT NULL "
                        + "ORDER BY timestamp "
                        + "LIMIT %s",
                        (symbol, limit),
                    )
                else:
                    cur.execute(
                        "SELECT symbol, timestamp, "
                        + ", ".join(FEATURE_COLS)
                        + ", " + TARGET_COL
                        + " FROM features_hourly "
                        + "WHERE " + TARGET_COL
                        + " IS NOT NULL "
                        + "ORDER BY timestamp "
                        + "LIMIT %s",
                        (limit,),
                    )
                return cur.fetchall()
    except Exception as e:
        log.error("fetch_rows: %s", e)
        return []


def rows_to_xy(rows):
    """Преобразует строки в numpy arrays."""
    if not rows:
        return None, None, [], []

    timestamps = []
    symbols = []
    X = []
    y = []

    n_features = len(FEATURE_COLS)

    for r in rows:
        # r = (symbol, timestamp, f1..fn, target)
        symbols.append(r[0])
        timestamps.append(r[1])

        row_feats = []
        for i in range(2, 2 + n_features):
            v = r[i]
            if v is None:
                row_feats.append(np.nan)
            else:
                try:
                    row_feats.append(float(v))
                except Exception:
                    row_feats.append(np.nan)
        X.append(row_feats)

        target = r[2 + n_features]
        try:
            y.append(int(target))
        except Exception:
            y.append(0)

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.int32)
    return X, y, timestamps, symbols


def time_split(X, y, test_frac=0.2):
    """Split по времени: train=старые, test=новые."""
    n = len(X)
    if n < 20:
        return X, y, X, y

    split = int(n * (1 - test_frac))
    X_train = X[:split]
    y_train = y[:split]
    X_test = X[split:]
    y_test = y[split:]
    return X_train, y_train, X_test, y_test


def prepare(symbol=None, test_frac=0.2):
    """
    Возвращает dict:
      X_train, y_train, X_test, y_test,
      n_total, n_train, n_test, balance
    """
    rows = fetch_rows(symbol=symbol)
    log.info("rows loaded: %d", len(rows))

    if len(rows) < 20:
        log.warning("not enough rows: %d < 20", len(rows))
        return None

    X, y, ts, sym = rows_to_xy(rows)
    if X is None or len(X) < 20:
        log.warning("not enough samples after parse")
        return None

    X_train, y_train, X_test, y_test = time_split(
        X, y, test_frac
    )

    balance = {
        "up_total": int(y.sum()),
        "down_total": int(len(y) - y.sum()),
        "up_train": int(y_train.sum()),
        "down_train": int(len(y_train) - y_train.sum()),
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


def symbols_unique(sym_list):
    return sorted(set(sym_list))


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader DATASET test")
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
    log.info("features: %d", len(data["feature_cols"]))


if __name__ == "__main__":
    main()