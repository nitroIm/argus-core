# ============================================================
# ARGUS-Trader - DATASET v6 [PRODUCTION]
# ------------------------------------------------------------
# v6: + cross-features BTC<->ETH (ratio, lead, spread,
#     rolling correlation). One dataset for the whole brain.
#     + regression target (next_return) parallel to
#     classification (next_direction).
#     + HORIZON config (1/4/12/24).
# v5: + 12 features (EMA, MACD, BB, session).
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

from db import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.learn.dataset")

# ============================================================
# CONFIG — можно переопределить через env
# ============================================================
USE_EXTERNAL = (
    os.getenv("USE_EXTERNAL", "0").strip() == "1"
)

# Горизонт предсказания (часы)
# 1 — шумно, 4 — баланс, 12/24 — тренд
HORIZON = int(os.getenv("HORIZON", "4"))

# Порог "значимого движения" для classification
# next_return > 0.5% → up, < -0.5% → down,
# между → отбрасываем (не шумит)
MOVE_THRESHOLD_PCT = float(
    os.getenv("MOVE_THRESHOLD_PCT", "0.5")
)

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

# Кросс-фичи BTC<->ETH
CROSS_COLS = [
    "other_change_1h",     # как дёрнулась вторая монета
    "other_change_4h",
    "other_change_24h",
    "eth_btc_ratio",       # ETH/BTC * 1000
    "ratio_zscore_24h",    # насколько ratio выше нормы
    "lead_lag_corr_24h",   # rolling корреляция 24h
    "spread_pct",          # разница движений за 4h
]

FEATURE_COLS = (
    INTERNAL_COLS
    + (EXTERNAL_COLS if USE_EXTERNAL else [])
    + CROSS_COLS
)

TARGET_DIR = "next_direction"
TARGET_RET = "next_return"

EXT_MAX_AGE_H = 3


# ============================================================
# FETCH BASE FEATURES (per symbol)
# ============================================================
def fetch_features(symbol, limit=100000):
    base_cols = (
        ["symbol", "timestamp"]
        + INTERNAL_COLS
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = (
                    "SELECT " + ", ".join(base_cols)
                    + " FROM features_hourly "
                    + "WHERE symbol = %s "
                    + "ORDER BY timestamp "
                    + "LIMIT %s"
                )
                cur.execute(sql, (symbol, limit))
                return cur.fetchall(), base_cols
    except Exception as e:
        log.error("fetch_features %s: %s", symbol, e)
        return [], []


def fetch_candles(symbol, limit=100000):
    """1h свечи — для расчёта горизонтных таргетов."""
    try:
        with get_connection() as conn:
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
        log.error("fetch_candles %s: %s", symbol, e)
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
        log.error("fetch_external %s: %s", symbol, e)
        return []


# ============================================================
# BUILD HORIZON TARGETS
# ============================================================
def build_targets(candles, horizon):
    """For each candle index, compute return over next
    `horizon` hours. Also direction (with threshold)."""
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


# ============================================================
# CROSS FEATURES
# ============================================================
def build_cross_features(
    btc_feats, eth_feats,
    btc_candles, eth_candles,
):
    """Строит кросс-признаки для BTC и ETH.
    Возвращает dict[ts] -> dict[cross_cols] для BTC и для ETH.
    """
    # --- ratio (ETH/BTC * 1000) ---
    btc_close = {c["ts"]: c["close"] for c in btc_candles}
    eth_close = {c["ts"]: c["close"] for c in eth_candles}
    ratio = {}
    common_ts = sorted(set(btc_close) & set(eth_close))
    for ts in common_ts:
        bc = btc_close[ts]
        ec = eth_close[ts]
        if bc and ec and bc > 0:
            ratio[ts] = ec / bc * 1000

    # --- rolling correlation 24h ---
    def rolling_corr(ts_list, btc_map, eth_map, window=24):
        out = {}
        for i in range(len(ts_list)):
            if i < window - 1:
                out[ts_list[i]] = None
                continue
            xs = []
            ys = []
            for j in range(i - window + 1, i + 1):
                t = ts_list[j]
                bc = btc_map.get(t)
                ec = eth_map.get(t)
                if bc and ec:
                    xs.append(bc)
                    ys.append(ec)
            if len(xs) < 5:
                out[ts_list[i]] = None
                continue
            a = np.array(xs, dtype=float)
            b = np.array(ys, dtype=float)
            if a.std() == 0 or b.std() == 0:
                out[ts_list[i]] = None
                continue
            out[ts_list[i]] = float(np.corrcoef(a, b)[0, 1])
        return out

    # Используем change_pct с features для correlation
    btc_ch = {ts: feats for ts, feats in btc_feats}
    eth_ch = {ts: feats for ts, feats in eth_feats}

    corr_ts = sorted(set(btc_ch) & set(eth_ch))
    corr_map = {}
    window = 24
    for i in range(len(corr_ts)):
        if i < window - 1:
            corr_map[corr_ts[i]] = None
            continue
        xs, ys = [], []
        for j in range(i - window + 1, i + 1):
            t = corr_ts[j]
            bc = btc_ch.get(t)
            ec = eth_ch.get(t)
            if bc is not None and ec is not None:
                xs.append(bc)
                ys.append(ec)
        if len(xs) < 5:
            corr_map[corr_ts[i]] = None
            continue
        a = np.array(xs, dtype=float)
        b = np.array(ys, dtype=float)
        if a.std() == 0 or b.std() == 0:
            corr_map[corr_ts[i]] = None
            continue
        corr_map[corr_ts[i]] = float(
            np.corrcoef(a, b)[0, 1]
        )

    # --- zscore ratio 24h ---
    ratio_ts = sorted(ratio.keys())
    zscore = {}
    for i in range(len(ratio_ts)):
        if i < 24:
            zscore[ratio_ts[i]] = None
            continue
        window_vals = [
            ratio[ratio_ts[j]]
            for j in range(i - 24, i)
        ]
        arr = np.array(window_vals, dtype=float)
        mu = arr.mean()
        sd = arr.std()
        if sd == 0:
            zscore[ratio_ts[i]] = 0.0
        else:
            zscore[ratio_ts[i]] = float(
                (ratio[ratio_ts[i]] - mu) / sd
            )

    return {
        "btc": {
            "ratio": ratio,
            "zscore": zscore,
            "corr": corr_map,
        },
        "eth": {
            "ratio": ratio,
            "zscore": zscore,
            "corr": corr_map,
        },
    }


# ============================================================
# EXT LOOKUP
# ============================================================
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


# ============================================================
# BUILD X, y
# ============================================================
def _get_feat_map(rows, base_cols):
    """ts -> dict внутренних признаков."""
    idx = {}
    for i, col in enumerate(base_cols):
        idx[col] = i
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
    btc_rows, eth_rows, btc_base, eth_base,
    btc_candles, eth_candles,
    ext_dxy, ext_spx, ext_gold,
):
    """Строит единый X/y для обеих монет + кросс-фичи."""
    btc_feat = _get_feat_map(btc_rows, btc_base)
    eth_feat = _get_feat_map(eth_rows, eth_base)

    # Кросс фичи
    btc_ch_simple = {
        ts: f["change_pct"] for ts, f in btc_feat.items()
    }
    eth_ch_simple = {
        ts: f["change_pct"] for ts, f in eth_feat.items()
    }
    cross = build_cross_features(
        btc_ch_simple, eth_ch_simple,
        btc_candles, eth_candles,
    )

    btc_targets = build_targets(btc_candles, HORIZON)
    eth_targets = build_targets(eth_candles, HORIZON)

    X, y_dir, y_ret, ts_list, sym_list = [], [], [], [], []

    def add_rows(feat_map, targets, symbol,
                 other_feat_map, cross_data):
        for ts in sorted(feat_map.keys()):
            ret = targets.get(ts)
            if ret is None:
                continue

            # Direction with threshold
            if ret > MOVE_THRESHOLD_PCT:
                direction = 1
            elif ret < -MOVE_THRESHOLD_PCT:
                direction = 0
            else:
                # слишком шумно — пропускаем
                continue

            feats = feat_map[ts]
            row = []

            # Internal
            for col in INTERNAL_COLS:
                row.append(feats.get(col, np.nan))

            # External
            if USE_EXTERNAL:
                row.append(
                    ext_lookup(ext_dxy, ts)
                    if ext_lookup(ext_dxy, ts) is not None
                    else np.nan
                )
                row.append(
                    ext_lookup(ext_spx, ts)
                    if ext_lookup(ext_spx, ts) is not None
                    else np.nan
                )
                row.append(
                    ext_lookup(ext_gold, ts)
                    if ext_lookup(ext_gold, ts) is not None
                    else np.nan
                )

            # Cross
            other = other_feat_map.get(ts)
            if other is None:
                row.extend([np.nan] * len(CROSS_COLS))
            else:
                row.append(other.get("change_pct", np.nan))
                row.append(other.get("change_4h", np.nan))
                row.append(other.get("change_24h", np.nan))
                row.append(cross_data["ratio"].get(ts, np.nan))
                row.append(cross_data["zscore"].get(ts, np.nan))
                row.append(cross_data["corr"].get(ts, np.nan))
                # spread
                spread = None
                oc = other.get("change_4h")
                sc = feats.get("change_4h")
                if oc is not None and sc is not None:
                    spread = sc - oc
                row.append(
                    spread if spread is not None else np.nan
                )

            X.append(row)
            y_dir.append(direction)
            y_ret.append(float(ret))
            ts_list.append(ts)
            sym_list.append(symbol)

    add_rows(
        btc_feat, btc_targets, "BTCUSDT",
        eth_feat, cross["btc"],
    )
    add_rows(
        eth_feat, eth_targets, "ETHUSDT",
        btc_feat, cross["eth"],
    )

    # Сортировка по времени (модель не должна видеть будущее)
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


# ============================================================
# SPLIT
# ============================================================
def time_split(X, y, test_frac=0.2):
    n = len(X)
    if n < 20:
        return X, y, X, y
    split = int(n * (1 - test_frac))
    return (
        X[:split], y[:split],
        X[split:], y[split:],
    )


# ============================================================
# PREPARE
# ============================================================
def prepare(test_frac=0.2):
    btc_rows, btc_base = fetch_features("BTCUSDT")
    eth_rows, eth_base = fetch_features("ETHUSDT")

    log.info(
        "rows: BTC=%d ETH=%d (HORIZON=%dh, threshold=%.2f%%)",
        len(btc_rows), len(eth_rows),
        HORIZON, MOVE_THRESHOLD_PCT,
    )

    if not btc_rows or not eth_rows:
        log.error("empty rows")
        return None

    btc_candles = fetch_candles("BTCUSDT")
    eth_candles = fetch_candles("ETHUSDT")
    log.info(
        "candles: BTC=%d ETH=%d",
        len(btc_candles), len(eth_candles),
    )

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
        btc_rows, eth_rows, btc_base, eth_base,
        btc_candles, eth_candles,
        ext_dxy, ext_spx, ext_gold,
    )

    log.info(
        "samples after threshold: %d", len(X),
    )

    if len(X) < 100:
        log.error("too few samples: %d", len(X))
        return None

    (
        X_train, y_train,
        X_test, y_test,
    ) = time_split(X, y_dir, test_frac)

    (
        _, r_train,
        _, r_test,
    ) = time_split(X, y_ret, test_frac)

    balance = {
        "up_total": int(y_dir.sum()),
        "down_total": int(len(y_dir) - y_dir.sum()),
        "up_train": int(y_train.sum()),
        "down_train": int(
            len(y_train) - y_train.sum()
        ),
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
        "ret stats train: mean=%.3f%% std=%.3f%%",
        float(r_train.mean()) if len(r_train) else 0,
        float(r_train.std()) if len(r_train) else 0,
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
        "threshold_pct": MOVE_THRESHOLD_PCT,
        "symbols": sorted(set(sym)),
    }


# ============================================================
# MAIN (test)
# ============================================================
def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader DATASET v6")
    log.info("HORIZON=%dh  THRESHOLD=%.2f%%",
             HORIZON, MOVE_THRESHOLD_PCT)
    log.info("=" * 60)
    data = prepare()
    if data is None:
        return
    log.info(
        "total=%d train=%d test=%d",
        data["n_total"],
        data["n_train"], data["n_test"],
    )
    log.info("features: %d", len(data["feature_cols"]))
    log.info(
        "ret_test: mean=%.3f%% std=%.3f%%",
        float(data["r_test"].mean()),
        float(data["r_test"].std()),
    )


if __name__ == "__main__":
    main()